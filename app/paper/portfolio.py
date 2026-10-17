"""三組共享資金紙上帳本。僅操作研究表，金額一律為新台幣 e4 整數。"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from sqlalchemy import text
from app.backtest.execution_model import ExecutionModelConfig

from app.db.swing_repository import canonical_json, content_hash, to_us, now_us, immediate_transaction, SwingRepository


# 查詢必須將 b 批次 join 至其不可變的 e 實驗；不可使用呼叫端自報模式。
EXECUTION_DECISION_TIME_SQL = "CASE WHEN e.mode='historical_exploratory' THEN b.cutoff_at_us ELSE b.decision_at_us END"


def money(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("金額必須為有限數值")
    return int((number * 10000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def insert_row(session, table, values):
    columns = ','.join(values)
    return session.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({','.join(':'+key for key in values)})"), values).lastrowid


def freeze_batch(session, experiment_id, run_key, at_us, manifest, coverage=None, sources=(), decision_at_us=None, elapsed_ms=0):
    existing = session.execute(text("SELECT * FROM swing_input_batches WHERE experiment_id=:e AND run_key=:k"), {'e':experiment_id,'k':run_key}).mappings().first()
    digest = content_hash(manifest)
    if existing:
        if existing['input_hash'] != digest or (coverage is not None and existing['coverage_json']!=canonical_json(coverage)):
            raise ValueError('idempotency_conflict')
        old_links=session.execute(text('SELECT source_version_id,purpose FROM swing_batch_sources WHERE batch_id=:b ORDER BY source_version_id,purpose'),{'b':existing['id']}).all()
        if sorted(set(sources))!=[tuple(link) for link in old_links]: raise ValueError('idempotency_conflict')
        return existing['id']
    batch_id = session.execute(text('SELECT coalesce(max(id),0)+1 FROM swing_input_batches')).scalar_one()
    for source_id, purpose in sorted(set(sources)):
        insert_row(session,'swing_batch_sources',dict(batch_id=batch_id,experiment_id=experiment_id,source_version_id=source_id,purpose=purpose))
    coverage = coverage or {'schema_version':1,'status':'incomplete','reason':'低階探索性輸入，未經完整覆蓋檢查'}
    decision_at_us = decision_at_us or at_us
    insert_row(session,'swing_input_batches',dict(id=batch_id,experiment_id=experiment_id,run_key=run_key,cutoff_at_us=at_us,decision_at_us=decision_at_us,coverage_status=coverage['status'],coverage_json=canonical_json(coverage),source_count=len(set(sources)),manifest_json=canonical_json(manifest),input_hash=digest,research_elapsed_ms=elapsed_ms,created_at_us=max(now_us(),decision_at_us)))
    return batch_id


def ledger_mutation(method):
    """每次公開寫入以短立即交易及 savepoint 保護，失敗不留下半筆帳。"""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with immediate_transaction(self.session):
            with self.session.begin_nested():
                return method(self, *args, **kwargs)
    return wrapped


class PortfolioLedger:
    def __init__(self, session, experiment_id):
        self.session = session
        self.experiment_id = int(experiment_id)
        row = session.execute(text('SELECT * FROM swing_experiments WHERE id=:e'),{'e':self.experiment_id}).mappings().first()
        if not row:
            raise ValueError('找不到研究實驗')
        self.config = json.loads(row['config_json'])
        self.mode = row['mode']

    def _account(self, arm):
        row = self.session.execute(text('SELECT * FROM swing_paper_accounts WHERE experiment_id=:e AND arm=:a'),{'e':self.experiment_id,'a':arm}).mappings().first()
        if not row:
            raise ValueError('arm 必須為 A、B 或 C')
        return row

    def _latest(self, arm):
        account = self._account(arm)
        return self.session.execute(text('SELECT * FROM swing_ledger_events WHERE account_id=:a ORDER BY seq DESC LIMIT 1'),{'a':account['id']}).mappings().first()

    def snapshot(self, arm):
        row = self._latest(arm)
        if not row:
            return dict(arm=arm,settled_cash_e4=100000000,reserved_cash_e4=0,receivable_e4=0,payable_e4=0,market_value_e4=0,equity_e4=100000000,risk_paused=False,ai_blocked=False,positions={},obligations=[],orders=[],events=[],cash_available_e4=100000000)
        state = {key:row[key] for key in ('settled_cash_e4','reserved_cash_e4','receivable_e4','payable_e4','market_value_e4','equity_e4')}
        state.update(arm=arm,risk_paused=bool(row['risk_paused']),ai_blocked=bool(row['ai_blocked']),positions=json.loads(row['positions_json']),obligations=json.loads(row['obligations_json']),orders=json.loads(row['orders_json']))
        state['cash_available_e4'] = state['settled_cash_e4']-state['reserved_cash_e4']-state['payable_e4']
        events = self.session.execute(text('SELECT event_key,event_kind,actor,effective_at_us,detail_json FROM swing_ledger_events WHERE account_id=:a ORDER BY seq'),{'a':row['account_id']}).mappings()
        state['events'] = [dict(event_key=e['event_key'],event_kind=e['event_kind'],actor=e['actor'],effective_at_us=e['effective_at_us'],detail=json.loads(e['detail_json'])) for e in events]
        return state

    def _replay(self, arm, key, payload):
        row = self.session.execute(text('SELECT detail_json,payload_hash FROM swing_ledger_events WHERE account_id=:a AND event_key=:k'),{'a':self._account(arm)['id'],'k':key}).mappings().first()
        if row:
            if row['payload_hash'] != content_hash(payload):
                raise ValueError('idempotency_conflict')
            return json.loads(row['detail_json'])['result']
        return None

    def _append(self, arm, key, kind, at_us, state, payload, result, **fields):
        previous = self._latest(arm)
        if previous and at_us < previous['effective_at_us']:
            raise ValueError('事件時間不可倒序')
        positions = state['positions']
        if any(type(p['quantity']) is not int or p['quantity'] < 0 for p in positions.values()):
            raise ValueError('持股逐項核對失敗')
        for position in positions.values():
            if sum(lot['quantity'] for lot in position.get('lots',[]))!=position['quantity']:
                raise ValueError('持股批次核對失敗')
            if not 0<=position.get('sellable_quantity',0)<=position['quantity']:
                raise ValueError('可賣持股逐項核對失敗')
        for name in ('payable','receivable'):
            if state[name+'_e4'] != sum(o['amount_e4'] for o in state['obligations'] if o['kind']==name):
                raise ValueError('交割款逐項核對失敗')
        if state['reserved_cash_e4'] != sum(o['reserved_e4'] for o in state['orders']):
            raise ValueError('預留款逐項核對失敗')
        state['market_value_e4'] = sum(p['quantity']*p['mark_e4'] for p in positions.values())
        state['equity_e4'] = state['settled_cash_e4']+state['receivable_e4']-state['payable_e4']+state['market_value_e4']
        if arm in ('B','C') and state['equity_e4']<=80000000:
            state['risk_paused']=True
        if arm=='A':
            state['risk_paused']=False
            state['ai_blocked']=False
        balances={key:state[key] for key in ('settled_cash_e4','reserved_cash_e4','receivable_e4','payable_e4','market_value_e4','equity_e4')}
        hashed = {**balances,'positions':positions,'obligations':state['obligations'],'orders':state['orders'],'risk_paused':bool(state['risk_paused']),'ai_blocked':bool(state['ai_blocked'])}
        values=dict(experiment_id=self.experiment_id,account_id=self._account(arm)['id'],seq=previous['seq']+1 if previous else 1,event_key=key,event_kind=kind,effective_at_us=at_us,**balances,risk_paused=int(state['risk_paused']),ai_blocked=int(state['ai_blocked']),positions_json=canonical_json(positions),obligations_json=canonical_json(state['obligations']),orders_json=canonical_json(state['orders']),detail_json=canonical_json({'schema_version':1,'input':payload,'result':result}),prev_state_hash=previous['state_hash'] if previous else None,state_hash=content_hash(hashed),payload_hash=content_hash(payload),created_at_us=max(now_us(),at_us),**fields)
        insert_row(self.session,'swing_ledger_events',values)
        self.session.flush()
        return result

    @ledger_mutation
    def open(self, arm, at_us):
        if not self._latest(arm):
            self._append(arm,'opening','opening',at_us,self.snapshot(arm),{'opening':True},{'status':'opened'})

    def _settle(self, arm, at_us, day):
        state=self.snapshot(arm)
        due=[o for o in state['obligations'] if o['settlement_date']<=day]
        if not due:
            return state
        for obligation in due:
            sign=1 if obligation['kind']=='receivable' else -1
            state['settled_cash_e4']+=sign*obligation['amount_e4']
            state[obligation['kind']+'_e4']-=obligation['amount_e4']
            state['obligations'].remove(obligation)
        key='settlement:'+content_hash([o['event_key'] for o in due])
        self._append(arm,key,'settlement',at_us,state,{'due':due},{'status':'settled'})
        return self.snapshot(arm)

    @staticmethod
    def _sellable(position, day):
        # 普通交易不允許當沖；T+1 可賣與 T+2 款項交割分開核對。
        lots = position.get('lots', [])
        return sum(lot['quantity'] for lot in lots if lot['trade_date'] < day)

    @ledger_mutation
    def settle(self, arm, at, event_key):
        at_us = to_us(at)
        payload = {'at': at_us}
        replay = self._replay(arm, event_key, payload)
        if replay is not None:
            return replay
        day = datetime.fromtimestamp(at_us / 1e6, timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei')).date().isoformat()
        state = self._settle(arm, at_us, day)
        for position in state['positions'].values():
            position['sellable_quantity'] = self._sellable(position, day)
        return self._append(arm, event_key, 'cancel', at_us, state, payload, {'status': 'advanced'})

    @ledger_mutation
    def submit_order(self, arm, symbol, quantity, side, signal_at, market, event_key, stop_loss_price=None, decision_id=None, *, calendar=None, execution_context=None):
        if type(quantity) is not int or quantity<=0 or side not in ('buy','sell'):
            raise ValueError('quantity 必須為正整數，side 必須為 buy 或 sell')
        signal_us=to_us(signal_at)
        market=sorted(deepcopy(market),key=lambda bar:bar['date'])
        next_bar=next((b for b in market if b.get('session_open_at') and to_us(b['session_open_at'])>signal_us),None)
        calendar=sorted(set(calendar or [bar['date'] for bar in market]))
        # 未來交易日只保留公開日曆；成交封存不可包含未來價格、收盤或全日量。
        open_input = {k: next_bar.get(k) for k in ('date','open','prior_volume','available_at','session_open_at')} if next_bar else None
        payload={'arm':arm,'symbol':symbol,'quantity':quantity,'side':side,'signal_at_us':signal_us,'open_input':open_input,'calendar':calendar,'stop_loss_price':stop_loss_price,'decision_id':decision_id}
        prior=self._replay(arm,event_key,payload)
        if prior is not None:
            return prior
        reason=None
        if next_bar is None:
            reason='missing_next_session'
        at_us=to_us(next_bar['session_open_at']) if next_bar else signal_us
        previous=self._latest(arm)
        if previous and at_us<previous['effective_at_us']:
            reason='expired_decision'
            at_us=previous['effective_at_us']
        state=self.snapshot(arm)
        trade_day=next_bar['date'] if next_bar else None
        if next_bar:
            if not next_bar.get('available_at') or to_us(next_bar['available_at'])>at_us:
                reason='unavailable_open_input'
            if next_bar.get('prior_volume') is None or Decimal(str(next_bar['prior_volume']))<=0:
                reason='missing_prior_liquidity'
        settlement=None
        if next_bar:
            index=calendar.index(trade_day)+self.config['execution']['settlement_sessions']
            if index>=len(calendar): reason='missing_settlement_calendar'
            else: settlement=calendar[index]
        if reason is None:
            state=self._settle(arm,at_us,trade_day)
        price_e4=0; fee=tax=0
        product='etf' if symbol==self.config['etf_symbol'] else 'stock'
        if next_bar:
            try:
                price_e4,fee,tax=ExecutionModelConfig.from_confirmed(self.config,product).quote_e4(next_bar['open'],quantity,side)
                if price_e4<=0: reason=reason or 'invalid_open_price'
            except (ValueError,TypeError,ArithmeticError):
                reason=reason or 'invalid_open_price'
        gross=price_e4*quantity
        if side=='buy':
            if state['risk_paused']: reason=reason or 'risk_paused'
            if arm=='C' and state['ai_blocked']: reason=reason or 'ai_blocked'
            if arm=='A' and symbol!=self.config['etf_symbol']: reason=reason or 'benchmark_etf_only'
            if arm in ('B','C') and symbol not in self.config['stock_pool']: reason=reason or 'outside_stock_pool'
            risk=self.config['risk']
            stop=money(stop_loss_price) if stop_loss_price is not None else int(price_e4*(1-Decimal(str(self.config['strategy']['stop_loss_pct']))))
            if arm in ('B','C') and (stop<0 or stop>=price_e4 or (price_e4-stop)*quantity+fee>risk['max_single_risk_e4']): reason=reason or 'single_risk_limit'
            position_value=state['positions'].get(symbol,{}).get('quantity',0)*price_e4+gross
            if arm in ('B','C') and (position_value>risk['max_position_e4'] or state['market_value_e4']+gross>risk['max_total_exposure_e4']): reason=reason or 'portfolio_risk_limit'
            if gross+fee>state['cash_available_e4']: reason=reason or 'insufficient_cash'
        else:
            if gross-fee-tax<0: reason=reason or 'fees_exceed_sale_proceeds'
            position=state['positions'].get(symbol,{})
            if quantity>self._sellable(position, trade_day or ''): reason=reason or 'insufficient_sellable_position'
        decision=None
        if decision_id is not None:
            decision=self.session.execute(text(f'SELECT d.*,b.cutoff_at_us,b.decision_at_us,b.coverage_status,{EXECUTION_DECISION_TIME_SQL} AS execution_decision_at_us FROM swing_candidate_decisions d JOIN swing_input_batches b ON b.id=d.batch_id JOIN swing_experiments e ON e.id=b.experiment_id WHERE d.id=:d AND d.experiment_id=:e'),{'d':decision_id,'e':self.experiment_id}).mappings().first()
            if not decision or decision['symbol']!=symbol or decision['execution_decision_at_us']>=at_us:
                reason=reason or 'invalid_common_decision'
                decision=None
            elif side=='buy':
                if self.mode=='prospective' and decision['coverage_status']!='complete': reason=reason or 'incomplete_common_decision'
                intent=json.loads(decision['quant_json'])
                if intent.get('side')!='buy' or quantity>intent.get('quantity',0) or to_us(intent.get('signal_at'))!=signal_us:
                    reason=reason or 'common_intent_mismatch'
                if arm=='C' and decision['ai_outcome']!='allow': reason=reason or 'ai_not_allowed'
        elif side=='buy' and arm=='C': reason=reason or 'ai_decision_required'
        elif side=='buy' and arm=='B' and self.mode=='prospective': reason=reason or 'common_decision_required'
        coverage=None; links=[]
        if execution_context is not None:
            links=[(int(i),'execution') for i in execution_context.get('source_ids',[])]
            coverage=execution_context.get('coverage')
            if not links or not coverage or coverage.get('status')!='complete':
                reason=reason or 'incomplete_execution_sources'
        if self.mode=='prospective' and side=='buy' and execution_context is None:
            reason=reason or 'trusted_execution_context_required'
        if not reason and side=='buy' and arm in ('B','C') and state['equity_e4']-fee<=80000000:
            reason='post_cost_risk_limit'
        batch=freeze_batch(self.session,self.experiment_id,'execution:'+arm+':'+event_key,at_us,{'schema_version':1,'purpose':'execution','input':payload,'execution_assumption':'daily_proxy'},coverage=coverage,sources=links)
        if not reason and side=='buy' and arm=='B' and decision is None:
            # 低階歷史研究仍封存共同量化意圖；不冒充合格比較或 AI 核准。
            quant={'schema_version':1,'symbol':symbol,'side':side,'quantity':quantity,'signal_at':signal_at,'strategy_version':self.config['strategy']['version'],'stop_loss_price':stop_loss_price,'exploratory':True}
            signal_batch=freeze_batch(self.session,self.experiment_id,'manual-intent:'+event_key,signal_us,{'schema_version':1,'candidates':[quant]})
            decision_id=insert_row(self.session,'swing_candidate_decisions',dict(experiment_id=self.experiment_id,batch_id=signal_batch,symbol=symbol,quant_rank=1,quant_json=canonical_json(quant),ai_outcome='not_requested',ai_json=canonical_json({'schema_version':1,'exploratory':True}),payload_hash=content_hash(quant),created_at_us=max(now_us(),signal_us)))
            decision={'batch_id':signal_batch}
        result=dict(status='rejected' if reason else 'filled',reason=reason or 'next_session_open',trade_date=trade_day,settlement_date=settlement,price_e4=price_e4,quantity=quantity,fee_e4=fee if not reason else 0,tax_e4=tax if not reason else 0,execution_assumption='daily_proxy',qualified=bool(coverage and coverage.get('status')=='complete' and self.mode=='prospective'))
        if not reason:
            if side=='buy':
                position=state['positions'].setdefault(symbol,{'quantity':0,'sellable_quantity':0,'cost_e4':0,'mark_e4':price_e4,'first_trade_date':trade_day})
                position['quantity']+=quantity; position['cost_e4']+=gross+fee; position['mark_e4']=price_e4
                position.setdefault('lots',[]).append({'quantity':quantity,'trade_date':trade_day,'price_e4':price_e4})
                position['sellable_quantity']=self._sellable(position,trade_day)
                state['payable_e4']+=gross+fee
                state['obligations'].append(dict(kind='payable',amount_e4=gross+fee,settlement_date=settlement,event_key=event_key,symbol=symbol,quantity=quantity))
            else:
                position=state['positions'][symbol]
                position['cost_e4']=position['cost_e4']*(position['quantity']-quantity)//position['quantity']
                position['quantity']-=quantity
                remaining=quantity
                for lot in position.get('lots',[]):
                    if lot['trade_date']>=trade_day: continue
                    used=min(remaining,lot['quantity']); lot['quantity']-=used; remaining-=used
                    if remaining==0: break
                position['lots']=[lot for lot in position.get('lots',[]) if lot['quantity']]
                position['sellable_quantity']=self._sellable(position,trade_day)
                if not position['quantity']: del state['positions'][symbol]
                state['receivable_e4']+=gross-fee-tax
                state['obligations'].append(dict(kind='receivable',amount_e4=gross-fee-tax,settlement_date=settlement,event_key=event_key,symbol=symbol,quantity=quantity))
        return self._append(arm,event_key,'reject' if reason else side,at_us,state,payload,result,batch_id=batch,decision_id=decision_id if decision else None,decision_batch_id=decision['batch_id'] if decision else None,symbol=symbol,quantity=0 if reason else quantity,price_e4=price_e4,fee_e4=0 if reason else fee,tax_e4=0 if reason else tax,trade_date=trade_day,settlement_date=settlement)

    @ledger_mutation
    def value(self, arm, prices, at, event_key, *, valuation_context=None):
        at_us=to_us(at); payload={'prices':prices,'at':at_us}
        replayed=self._replay(arm,event_key,payload)
        if replayed is not None: return replayed
        state=self.snapshot(arm)
        if set(state['positions'])-set(prices): raise ValueError('缺少必要估值行情')
        for symbol,position in state['positions'].items():
            position['mark_e4']=money(prices[symbol])
            if position['mark_e4']<=0: raise ValueError('估值價格必須大於零')
        batch=freeze_batch(self.session,self.experiment_id,'valuation:'+arm+':'+event_key,at_us,{'schema_version':1,'purpose':'valuation','prices':prices},coverage=valuation_context.get('coverage') if valuation_context else None,sources=[(i,'valuation') for i in valuation_context.get('source_ids',[])] if valuation_context else [])
        equity=state['settled_cash_e4']+state['receivable_e4']-state['payable_e4']+sum(p['quantity']*p['mark_e4'] for p in state['positions'].values())
        kind='risk_breach' if arm=='A' and equity<=80000000 else 'valuation'
        result=deepcopy({k:v for k,v in state.items() if k!='events'})
        result['equity_e4']=equity
        result['market_value_e4']=sum(p['quantity']*p['mark_e4'] for p in state['positions'].values())
        result['risk_paused']=state['risk_paused'] or (arm in ('B','C') and equity<=80000000)
        self._append(arm,event_key,kind,at_us,state,payload,result,batch_id=batch)
        return result

    @ledger_mutation
    def resume(self, arm, actor, note, at, event_key):
        if not str(actor).strip() or not str(note).strip(): raise ValueError('human confirmation required')
        at_us=to_us(at); payload={'actor':actor,'note':note,'at':at_us}
        replayed=self._replay(arm,event_key,payload)
        if replayed is not None: return replayed
        state=self.snapshot(arm)
        if state['equity_e4']<=80000000: raise ValueError('risk budget 仍低於門檻，無法恢復')
        state['risk_paused']=False
        result={k:v for k,v in state.items() if k!='events'}
        self._append(arm,event_key,'human_resume',at_us,state,payload,result,actor=actor)
        return result

    @ledger_mutation
    def set_ai_status(self, status, at, event_key):
        at_us=to_us(at); payload={'status':status,'at':at_us}
        replayed=self._replay('C',event_key,payload)
        if replayed is not None: return replayed
        state=self.snapshot('C'); state['ai_blocked']=status!='normal'
        result={k:v for k,v in state.items() if k!='events'}
        self._append('C',event_key,'pause' if status!='normal' else 'cancel',at_us,state,payload,result)
        return result

    @ledger_mutation
    def corporate_action(self, arm, source_id, at, event_key):
        row=self.session.execute(text("SELECT * FROM swing_source_versions WHERE id=:id AND kind='corporate_action' AND quality='verified'"),{'id':source_id}).mappings().first()
        at_us=to_us(at)
        if not row or row['public_at_us'] is None or row['public_at_us']>at_us or (row['revised_at_us'] or 0)>at_us or (self.mode=='prospective' and max(row['obtained_at_us'],row['verified_at_us'])>at_us): raise ValueError('公司行動來源不可用')
        action=json.loads(row['payload_json']); payload={'source_id':source_id,'at':at_us}
        replayed=self._replay(arm,event_key,payload)
        if replayed is not None: return replayed
        if self.config['execution']['price_basis']!='raw': raise ValueError('還原價格不得再次計入股利／拆分')
        state=self.snapshot(arm); position=state['positions'].get(row['symbol'])
        if position:
            if action.get('type')=='cash_dividend':
                per_share=money(action['cash_per_share'])
                if per_share<0: raise ValueError('股利不得為負值')
                if not action.get('payment_date') or action['payment_date']<action['ex_date']: raise ValueError('股利付款日期不合法')
                amount=position['quantity']*per_share
                state['receivable_e4']+=amount
                state['obligations'].append(dict(kind='receivable',amount_e4=amount,settlement_date=action['payment_date'],event_key=event_key))
            elif action.get('type')=='split':
                ratio=Decimal(str(action['ratio']))
                if not ratio.is_finite() or ratio<=0: raise ValueError('拆分比例必須大於零')
                new=Decimal(position['quantity'])*ratio
                if new!=new.to_integral_value(): raise ValueError('零碎股分配資料不足')
                for lot in position.get('lots',[]):
                    exact=Decimal(lot['quantity'])*ratio
                    if exact!=exact.to_integral_value(): raise ValueError('零碎股分配資料不足')
                    lot['quantity']=int(exact); lot['price_e4']=int(Decimal(lot['price_e4'])/ratio)
                position['quantity']=int(new); position['sellable_quantity']=int(Decimal(position['sellable_quantity'])*ratio); position['mark_e4']=int(Decimal(position['mark_e4'])/ratio)
            else: raise ValueError('不支援的公司行動')
        batch=freeze_batch(self.session,self.experiment_id,'corporate:'+arm+':'+str(source_id),at_us,{'schema_version':1,'action':action},sources=[(source_id,'corporate_action')])
        result={k:v for k,v in state.items() if k!='events'}
        self._append(arm,event_key,'corporate_action',at_us,state,payload,result,batch_id=batch)
        return result
