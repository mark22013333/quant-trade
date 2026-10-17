"""已確認設定、固定輸入與 AI 事件篩選的共同研究入口。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import time
import re
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import pandas as pd
from sqlalchemy import text

from app.db.swing_repository import SwingRepository, canonical_json, content_hash, to_us, from_us, now_us, immediate_transaction
from app.paper.portfolio import PortfolioLedger, freeze_batch, insert_row, money, EXECUTION_DECISION_TIME_SQL
from app.services.subscription_research import bounded_external_call, CodexSubscriptionResearcher, research_execution_slot
from app.services.swing_evidence import event_fact_errors
from app.services.strategy_config import StrategyRunConfig, DEFAULT_ENABLED, DEFAULT_WEIGHTS, STRATEGY_NAMES
from strategies.multi.swing_system import MultiStrategySwingSystem, MultiStrategyConfig


class MissingConfiguration(ValueError):
    def __init__(self, fields):
        self.missing_fields=sorted(set(fields))
        super().__init__('研究設定缺漏或不合法：'+', '.join(self.missing_fields))


def preview_config(config):
    if not isinstance(config,dict): raise MissingConfiguration(['config'])
    config=deepcopy(config); missing=[]
    required=['experiment_key','mode','etf_symbol','stock_pool','start_date','end_date','initial_cash_e4','stop_equity_e4','strategy','risk','fees','execution','data_policy','observation_sessions','code_version']
    for key in required:
        if key not in config or config[key] is None or config[key]=='': missing.append(key)
    fields={'strategy':['version','hold_days','parameters','stop_loss_pct'],'risk':['max_position_e4','max_total_exposure_e4','max_single_risk_e4'],'execution':['slippage_bps','settlement_sessions','price_basis','corporate_actions','odd_lot_model','rounding'],'data_policy':['warmup_bars','missing_required']}
    for section,keys in fields.items():
        if not isinstance(config.get(section),dict): missing.append(section); continue
        missing.extend(section+'.'+key for key in keys if key not in config[section])
    fees=config.get('fees')
    if not isinstance(fees,dict): missing.append('fees')
    else:
        for product in ('stock','etf'):
            if not isinstance(fees.get(product),dict): missing.append('fees.'+product); continue
            missing.extend('fees.'+product+'.'+key for key in ('commission_rate','minimum_fee_e4','sell_tax_rate') if key not in fees[product])
    if missing: raise MissingConfiguration(missing)
    for key in ('experiment_key','etf_symbol','code_version'):
        if not isinstance(config[key],str) or not config[key].strip() or len(config[key])>150: missing.append(key)
    if not isinstance(config['strategy']['version'],str) or not config['strategy']['version'].strip(): missing.append('strategy.version')
    if config['mode'] not in ('historical_exploratory','prospective'): missing.append('mode')
    if not isinstance(config['stock_pool'],list) or not config['stock_pool'] or len(config['stock_pool'])>500 or any(not isinstance(v,str) or not re.fullmatch(r'[0-9A-Z]{2,20}',v) for v in config['stock_pool']): missing.append('stock_pool')
    if not isinstance(config['etf_symbol'],str) or not re.fullmatch(r'[0-9A-Z]{2,20}',config['etf_symbol']): missing.append('etf_symbol')
    if type(config['initial_cash_e4']) is not int or config['initial_cash_e4']!=100000000: missing.append('initial_cash_e4')
    if type(config['stop_equity_e4']) is not int or config['stop_equity_e4']!=80000000: missing.append('stop_equity_e4')
    try:
        if date.fromisoformat(config['start_date'])>date.fromisoformat(config['end_date']): missing.append('end_date')
    except (ValueError,TypeError): missing.extend(['start_date','end_date'])
    integer_values=[('strategy.hold_days',config['strategy']['hold_days']),('observation_sessions',config['observation_sessions']),('data_policy.warmup_bars',config['data_policy']['warmup_bars'])]
    integer_values.extend(('risk.'+k,config['risk'][k]) for k in fields['risk'])
    for path,value in integer_values:
        if type(value) is not int or not 0<value<=100000000: missing.append(path)
    if type(config['execution']['settlement_sessions']) is not int or config['execution']['settlement_sessions']!=2: missing.append('execution.settlement_sessions')
    values=[('strategy.stop_loss_pct',config['strategy']['stop_loss_pct']),('execution.slippage_bps',config['execution']['slippage_bps'])]
    values.extend(('fees.'+p+'.'+k,fees[p][k]) for p in ('stock','etf') for k in ('commission_rate','sell_tax_rate'))
    for path,value in values:
        try:
            number=Decimal(str(value))
            upper=1000 if path=='execution.slippage_bps' else 1
            if not number.is_finite() or number<0 or number>=upper or (path=='strategy.stop_loss_pct' and number==0): missing.append(path)
        except Exception: missing.append(path)
    for p in ('stock','etf'):
        if type(fees[p]['minimum_fee_e4']) is not int or not 0<=fees[p]['minimum_fee_e4']<=100000000: missing.append('fees.'+p+'.minimum_fee_e4')
    if config['execution']['price_basis'] not in ('raw','adjusted'): missing.append('execution.price_basis')
    if config['execution']['price_basis']=='adjusted' and config['execution']['corporate_actions']!='included_in_prices': missing.append('execution.corporate_actions')
    if config['execution']['price_basis']=='raw' and config['execution']['corporate_actions']!='cash_and_split': missing.append('execution.corporate_actions')
    if config['execution']['odd_lot_model']!='daily_proxy': missing.append('execution.odd_lot_model')
    if config['execution']['rounding']!='ROUND_HALF_UP': missing.append('execution.rounding')
    if config['data_policy']['missing_required']!='block': missing.append('data_policy.missing_required')
    params=config['strategy']['parameters']
    if not isinstance(params,dict): missing.append('strategy.parameters')
    if missing: raise MissingConfiguration(missing)
    try:
        run=StrategyRunConfig(enabled=params.get('enabled',dict(DEFAULT_ENABLED)),weights=params.get('weights',dict(DEFAULT_WEIGHTS)),threshold=params.get('threshold',.6),strategy_params=params.get('params',{})).validated()
    except (ValueError,TypeError,AttributeError) as exc:
        raise MissingConfiguration(['strategy.parameters:'+str(exc)]) from exc
    required=params.get('required_strategies',[n for n,on in run.enabled.items() if on])
    if not isinstance(required,list) or any(n not in STRATEGY_NAMES or not run.enabled.get(n,False) for n in required):
        raise MissingConfiguration(['strategy.parameters.required_strategies'])
    fundamental=params.get('required_fundamentals',[])
    if not isinstance(fundamental,list) or any(k not in ('monthly_revenue','financial_summary') for k in fundamental):
        raise MissingConfiguration(['strategy.parameters.required_fundamentals'])
    config['stock_pool']=list(dict.fromkeys(config['stock_pool']))
    config['strategy']['parameters']={**params,**run.strategy_engine_config(),'required_strategies':required,'required_fundamentals':fundamental}
    config['engine_fingerprint']=engine_fingerprint()
    return {'config':config,'config_hash':content_hash(config),'missing_fields':[]}


def engine_fingerprint():
    root=Path(__file__).parents[2]
    files=[Path(__file__),root/'app/paper/portfolio.py',root/'app/backtest/execution_model.py',root/'app/services/strategy_config.py',root/'app/services/subscription_research.py',root/'app/services/research_runtime.py',root/'app/services/swing_evidence.py',root/'app/reports/swing_comparison.py',root/'app/db/swing_repository.py']
    files.extend(sorted((root/'strategies/multi').glob('*.py')))
    return hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest()



def strategy_warmup(config):
    params=config['strategy']['parameters']
    minimum={}
    for name in params['required_strategies']:
        if name in ('momentum_trend','mean_reversion'): minimum[name]=60
        elif name=='chip_flow':
            chip=params.get('params',{}).get('chip_flow',{})
            minimum[name]=max(5,int(chip.get('net_buy_zscore_window',60))+4) if chip.get('net_buy_threshold_mode')=='zscore' else 5
    return minimum


def numeric_market_frame(bars):
    """只轉換指標計算用副本；原始字串及金額精度保留在封存輸入。"""
    frame=pd.DataFrame(bars)
    missing=[]
    for field in ('open','high','low','close','volume'):
        values=[]
        for bar in bars:
            try:
                value=bar.get(field)
                if isinstance(value,bool): raise ValueError('boolean')
                number=Decimal(str(value))
                if not number.is_finite() or (number<0 if field=='volume' else number<=0):
                    raise ValueError('invalid market value')
                numeric=float(number)
                if not math.isfinite(numeric) or (numeric<=0 and field!='volume'):
                    raise ValueError('indicator value out of range')
            except (ValueError,TypeError,InvalidOperation,OverflowError):
                missing.append(field)
                break
            values.append(numeric)
        if field not in missing: frame[field]=values
    if missing: return None,sorted(set(missing))
    return frame,[]


def calendar_coverage(config, calendars, through):
    """覆蓋聲明須附有人工核對的官方清單證據，逐標的及期間核對。"""
    symbols=[config['etf_symbol'],*config['stock_pool']]
    for symbol in symbols:
        matching=[r['payload'] for r in calendars if symbol in r['payload'].get('symbols',[])
                  and r['payload'].get('period_start','9999')<=config['start_date']
                  and r['payload'].get('period_end','')>=through
                  and r['payload'].get('corporate_actions_complete') is True
                  and r['payload'].get('corporate_action_evidence')]
        if not matching: return False
    return bool(calendars)

class SwingResearchService:
    def __init__(self, session):
        self.session=session
        self.repo=SwingRepository(session)

    def preview_config(self,config): return preview_config(config)

    def create_experiment(self,config,confirmed_by,confirmation_note):
        missing=[]
        if not str(confirmed_by or '').strip(): missing.append('confirmed_by')
        if not str(confirmation_note or '').strip(): missing.append('confirmation_note')
        if missing: raise MissingConfiguration(missing)
        validated=preview_config(config); config=validated['config']; digest=validated['config_hash']
        with immediate_transaction(self.session):
            old=self.session.execute(text('SELECT id,config_hash FROM swing_experiments WHERE experiment_key=:k'),{'k':config['experiment_key']}).mappings().first()
            if old:
                if old['config_hash']!=digest: raise ValueError('idempotency_conflict：同一實驗鍵不可變更已確認設定')
                return self.get_experiment(old['id'])
            at=now_us()
            exp=insert_row(self.session,'swing_experiments',dict(experiment_key=config['experiment_key'],mode=config['mode'],config_status='confirmed',config_json=canonical_json(config),config_hash=digest,code_version=config['code_version'],confirmed_by=confirmed_by,confirmed_at_us=at,created_at_us=at))
            for arm in 'ABC': insert_row(self.session,'swing_paper_accounts',dict(experiment_id=exp,arm=arm,initial_cash_e4=100000000,stop_equity_e4=80000000,created_at_us=at))
            ledger=PortfolioLedger(self.session,exp)
            opening=to_us(config['start_date']+'T00:00:00+08:00')
            for arm in 'ABC': ledger.open(arm,opening)
            self._event(exp,'configuration-confirmed','human_ack',{'schema_version':1,'config_hash':digest,'note':confirmation_note},confirmed_by)
            result=self.get_experiment(exp)
        return result

    def list_experiments(self,limit=50):
        rows=self.session.execute(text('SELECT id FROM swing_experiments ORDER BY id DESC LIMIT :n'),{'n':max(1,min(100,int(limit)))}).scalars().all()
        return [self.get_experiment(i) for i in rows]

    def get_experiment(self,experiment_id):
        row=self.session.execute(text('SELECT * FROM swing_experiments WHERE id=:e'),{'e':experiment_id}).mappings().first()
        if not row: raise ValueError('找不到研究實驗')
        ledger=PortfolioLedger(self.session,experiment_id)
        events=self.session.execute(text('SELECT kind,event_key,actor,detail_json,created_at_us FROM swing_experiment_events WHERE experiment_id=:e ORDER BY id'),{'e':experiment_id}).mappings().all()
        return {'experiment_id':row['id'],'experiment_key':row['experiment_key'],'config_hash':row['config_hash'],'config':json.loads(row['config_json']),'mode':row['mode'],'confirmed_by':row['confirmed_by'],'confirmed_at':from_us(row['confirmed_at_us']),'accounts':[dict(initial_cash_e4=100000000,**ledger.snapshot(arm)) for arm in 'ABC'],'events':[{**dict(e),'detail':json.loads(e['detail_json'])} for e in events]}

    def append_source(self,source): return self.repo.append_source(source)
    def eligible_sources(self,**kwargs): return self.repo.eligible_sources(**kwargs)
    def repair_preview(self,**kwargs): return self.repo.preview_repair(**kwargs)
    def repair_apply(self,**kwargs): return self.repo.apply_repair(**kwargs)

    def _event(self,exp,key,kind,detail,actor=None):
        old=self.session.execute(text('SELECT * FROM swing_experiment_events WHERE experiment_id=:e AND event_key=:k'),{'e':exp,'k':key}).mappings().first()
        digest=content_hash(detail)
        if old:
            if old['payload_hash']!=digest: raise ValueError('idempotency_conflict')
            return json.loads(old['detail_json'])
        insert_row(self.session,'swing_experiment_events',dict(experiment_id=exp,event_key=key,kind=kind,actor=actor,detail_json=canonical_json(detail),payload_hash=digest,created_at_us=now_us()))
        return detail

    def _load_inputs(self,config,cutoff):
        market={}; sources=[]; conflicts=[]
        for symbol in [config['etf_symbol'],*config['stock_pool']]:
            rows=self.repo.eligible_sources(kind='market_bar',symbol=symbol,cutoff_at=cutoff,mode=config['mode'])
            market[symbol]=[dict(row['payload'],date=row['data_period'],source_id=row['id'],public_at=row['public_at'],obtained_at=row['obtained_at'],verified_at=row['verified_at']) for row in rows if row['data_period']<=str(cutoff)[:10] and row['payload'].get('phase')!='open' and row['payload'].get('available_at') and to_us(row['payload']['available_at'])<=to_us(cutoff)]
            by_day={}
            for bar in market[symbol]:
                old=by_day.get(bar['date'])
                if old:
                    keys=('open','high','low','close','volume','Foreign_Net_Buy','InvestmentTrust_Net_Buy','Chip_Concentration_Proxy')
                    if any(str(old.get(k))!=str(bar.get(k)) for k in keys): conflicts.append('conflicting_market:'+symbol+':'+bar['date'])
                else: by_day[bar['date']]=bar
            market[symbol]=list(by_day.values())
            sources.extend(row['id'] for row in rows)
        calendars=self.repo.eligible_sources(kind='calendar',symbol=None,cutoff_at=cutoff,mode=config['mode'])
        actions=self.repo.eligible_sources(kind='corporate_action',symbol=None,cutoff_at=cutoff,mode=config['mode'])
        sources.extend(r['id'] for r in [*calendars,*actions])
        fundamentals={}
        for symbol in config['stock_pool']:
            fundamentals[symbol]={}
            for kind in ('monthly_revenue','financial_summary'):
                versions=self.repo.eligible_sources(kind=kind,symbol=symbol,cutoff_at=cutoff,mode=config['mode'])
                fundamentals[symbol][kind]=versions
                sources.extend(v['id'] for v in versions)
        return {'schema_version':1,'market':market,'market_conflicts':conflicts,'fundamentals':fundamentals,'calendar':sorted({day for r in calendars for day in r['payload'].get('sessions',[])}),'corporate_actions':[dict(r['payload'],source_id=r['id'],symbol=r['symbol']) for r in actions],'corporate_actions_verified':calendar_coverage(config,calendars,min(str(cutoff)[:10],config['end_date'])),'source_ids':sources}

    def preflight(self,experiment_id,inputs=None,cutoff_at=None):
        exp=self.get_experiment(experiment_id); config=exp['config']
        cutoff=cutoff_at or config['end_date']+'T15:00:00+08:00'
        to_us(cutoff)
        inputs=deepcopy(inputs) if inputs is not None else self._load_inputs(config,cutoff)
        missing=list(inputs.get('market_conflicts',[]))
        if config.get('engine_fingerprint')!=engine_fingerprint(): missing.append('engine_version_requires_new_confirmation')
        symbols=[config['etf_symbol'],*config['stock_pool']]; count={}
        calendar=inputs.get('calendar',[])
        if not calendar or config['start_date'] not in calendar or not any(d>=config['end_date'] for d in calendar): missing.append('calendar')
        if not inputs.get('corporate_actions_verified'): missing.append('corporate_actions')
        for symbol in symbols:
            bars=inputs.get('market',{}).get(symbol,[])
            available=[b for b in bars if b.get('date') and b['date']<=str(cutoff)[:10] and b.get('available_at') and to_us(b['available_at'])<=to_us(cutoff)]
            if len({b['date'] for b in available})!=len(available): missing.append('duplicate_market:'+symbol)
            available=list({b['date']:b for b in available}.values())
            count[symbol]=len(available)
            before=[b for b in available if b['date']<config['start_date']]
            if len(before)<config['data_policy']['warmup_bars']: missing.append('warmup:'+symbol)
            if symbol in config['stock_pool']:
                for strategy,minimum in strategy_warmup(config).items():
                    if len(before)<minimum-1 or len(available)<minimum:
                        missing.append('strategy_warmup:'+symbol+':'+strategy+':'+str(minimum))
            expected=[d for d in calendar if config['start_date']<=d<=min(config['end_date'],str(cutoff)[:10])]
            if not expected or set(expected)-{b['date'] for b in available}: missing.append('market:'+symbol)
            _,invalid=numeric_market_frame(available)
            if invalid: missing.append(symbol+':missing_ohlcv:'+','.join(invalid))
        if not inputs.get('source_ids'): missing.append('verified_sources')
        for kind in config['strategy']['parameters'].get('required_fundamentals',[]):
            if kind not in ('monthly_revenue','financial_summary'):
                missing.append('unsupported_required_fundamental:'+str(kind)); continue
            for symbol in config['stock_pool']:
                if not inputs.get('fundamentals',{}).get(symbol,{}).get(kind): missing.append('fundamental:'+symbol+':'+kind)
        # 公開入口不接受自報 coverage=true；即使程式內傳入輸入亦須核對所有 source IDs。
        for source_id in inputs.get('source_ids',[]):
            row=self.session.execute(text('SELECT * FROM swing_source_versions WHERE id=:id'),{'id':source_id}).mappings().first()
            if not row or row['quality']!='verified' or row['public_at_us'] is None or row['public_at_us']>to_us(cutoff):
                missing.append('untrusted_source:'+str(source_id))
        return {'schema_version':1,'status':'incomplete' if missing else 'complete','qualified':not missing,'missing_fields':sorted(set(missing)),'bar_counts':count,'scope':'confirmed_stock_pool','mode':config['mode'],'execution_assumption':'daily_proxy','inputs':inputs}

    def verify_evidence(self,*,item,fetched_sources,cutoff_at,expected_symbol=None):
        errors=[]; accepted=[]; cutoff=to_us(cutoff_at)
        if not isinstance(item,dict) or not isinstance(fetched_sources,list): return {'accepted':False,'errors':['malformed_evidence'],'sources':[],'decision_version':None}
        if not isinstance(item.get('sources'),list): return {'accepted':False,'errors':['malformed_sources'],'sources':[],'decision_version':item.get('decision_version')}
        if type(item.get('exclude_recommended')) is not bool:
            return {'accepted':False,'errors':['invalid_exclude_recommended'],'sources':[],'decision_version':item.get('decision_version')}
        if expected_symbol is not None and item.get('symbol') != expected_symbol:
            return {'accepted':False,'errors':['issuer_identity_not_verified'],'sources':[],'decision_version':item.get('decision_version')}
        fetched_sources=[s for s in fetched_sources if isinstance(s,dict)]
        expected={str(s.get('url')):s for s in item.get('sources',[]) if isinstance(s,dict)}
        if not expected: errors.append('missing_source_urls')
        if not item.get('decision_version'): errors.append('missing_decision_version')
        for url,claim in expected.items():
            source=next((s for s in fetched_sources if s.get('url')==url),None)
            if not source: errors.append('original_not_fetched:'+url); continue
            original=str(source.get('original_text') or ''); excerpt=str(source.get('excerpt') or '')
            if len(original.strip())<10 or (excerpt and excerpt not in original): errors.append('invalid_original_excerpt:'+url); continue
            try:
                public=to_us(source.get('published_at')); obtained=to_us(source.get('obtained_at')); verified=to_us(source.get('verified_at'))
                if public>cutoff or verified<obtained or obtained<public: raise ValueError('invalid time')
            except (ValueError,TypeError,AttributeError): errors.append('unknown_or_future_source_time:'+url); continue
            fact_errors = event_fact_errors(original, symbol=expected_symbol or item.get('symbol'),
                                           event=item.get('event_type'), exclude=item['exclude_recommended'],
                                           require_issuer=expected_symbol is not None)
            if fact_errors:
                errors.extend(error+':'+url for error in fact_errors); continue
            try:
                if claim.get('published_at') and to_us(claim['published_at'])!=public:
                    errors.append('publication_claim_mismatch:'+url); continue
            except (ValueError,TypeError,AttributeError):
                errors.append('publication_claim_invalid:'+url); continue
            accepted.append(deepcopy(source))
        return {'accepted':not errors and bool(accepted),'errors':errors,'sources':accepted,'decision_version':item.get('decision_version')}

    def _candidates(self,config,inputs,cutoff):
        candidates=[]; errors=[]; parameters=config['strategy']['parameters']
        for symbol in config['stock_pool']:
            bars=[b for b in inputs.get('market',{}).get(symbol,[]) if b['date']<=str(cutoff)[:10] and b.get('available_at') and to_us(b['available_at'])<=to_us(cutoff)]
            if not bars: errors.append(symbol+':missing_market'); continue
            if len({b['date'] for b in bars})!=len(bars): errors.append(symbol+':duplicate_market'); continue
            frame,invalid=numeric_market_frame(bars)
            if invalid: errors.append(symbol+':missing_ohlcv:'+','.join(invalid)); continue
            if any(len(bars)<minimum for minimum in strategy_warmup(config).values()): errors.append(symbol+':strategy_warmup'); continue
            raw_close=max(bars,key=lambda b:b['date'])['close']
            frame=frame.sort_values('date').rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
            frame.index=pd.to_datetime(frame['date'])
            cfg=StrategyRunConfig(symbol=symbol,enabled=parameters.get('enabled',{}),weights=parameters.get('weights',{}),threshold=parameters.get('threshold',0.6),strategy_params=parameters.get('params',{})).validated()
            required=parameters.get('required_strategies',[n for n,on in cfg.enabled.items() if on])
            engine=MultiStrategySwingSystem(MultiStrategyConfig(symbol=symbol,**cfg.strategy_engine_config()))
            final,outputs=engine.generate_signals(frame)
            missing=[n for n in required if n in outputs and 'missing' in str(outputs[n].iloc[-1].get('reason','')).lower()]
            if missing: errors.append(symbol+':missing_required:'+','.join(missing)); continue
            last=final.iloc[-1]
            if int(last['entry_signal'])!=1: continue
            price=money(raw_close); allocation=min(config['risk']['max_position_e4'],config['initial_cash_e4'])
            quantity=allocation//price if price>0 else 0
            stop=Decimal(str(raw_close))*(1-Decimal(str(config['strategy']['stop_loss_pct'])))
            risk_per_share=price-money(stop)
            if risk_per_share>0: quantity=min(quantity,config['risk']['max_single_risk_e4']//risk_per_share)
            if quantity>0: candidates.append({'schema_version':1,'symbol':symbol,'score':float(last['ensemble_score']),'side':'buy','quantity':quantity,'stop_loss_price':format(stop.normalize(),'f'),'signal_at':cutoff,'strategy_version':config['strategy']['version']})
        candidates.sort(key=lambda c:(-c['score'],c['symbol']))
        for rank,c in enumerate(candidates,1): c['rank']=rank
        return candidates,errors

    def run(self,experiment_id,run_key,cutoff_at,inputs=None,researcher=None,fetcher=None,deadline_seconds=60):
        key = (str(self.session.get_bind().url), int(experiment_id))
        with research_execution_slot(key):
            return self._run(experiment_id,run_key,cutoff_at,inputs,researcher,fetcher,deadline_seconds)

    def _run(self,experiment_id,run_key,cutoff_at,inputs=None,researcher=None,fetcher=None,deadline_seconds=60):
        exp=self.get_experiment(experiment_id); config=exp['config']; cutoff=to_us(cutoff_at)
        local_cutoff=datetime.fromtimestamp(cutoff/1e6,timezone.utc).astimezone(ZoneInfo('Asia/Taipei'))
        if local_cutoff.hour<14: raise ValueError('after_close_cutoff_required')
        if not run_key or len(run_key)>160: raise ValueError('run_key 必須為非空白且長度不超過 160')
        finished=self.session.execute(text('SELECT id,manifest_json FROM swing_input_batches WHERE experiment_id=:e AND run_key=:k'),{'e':experiment_id,'k':run_key}).mappings().first()
        if finished:
            manifest=json.loads(finished['manifest_json'])
            if manifest.get('cutoff_at_us')!=cutoff: raise ValueError('idempotency_conflict')
            return {'batch_id':finished['id'],'status':'frozen','replayed':True,'comparison':self.compare(experiment_id)}
        if config.get('engine_fingerprint')!=engine_fingerprint(): raise ValueError('engine_version_requires_new_confirmation')
        if config['mode']=='prospective' and abs(now_us()-cutoff)>15*60*1000000: raise ValueError('prospective_cutoff_expired：前瞻觀察不得回填過期研究')
        stage=self.session.execute(text('SELECT detail_json FROM swing_experiment_events WHERE experiment_id=:e AND event_key=:k'),{'e':experiment_id,'k':'stage:'+run_key}).scalar_one_or_none()
        if stage:
            staged=json.loads(stage)
            if staged['cutoff_at_us']!=cutoff or (inputs is not None and content_hash(staged['inputs'])!=content_hash(inputs)): raise ValueError('idempotency_conflict')
        else:
            coverage=self.preflight(experiment_id,inputs,cutoff_at); candidates,missing=self._candidates(config,coverage['inputs'],cutoff_at)
            if missing: coverage['status']='incomplete'; coverage['qualified']=False; coverage['missing_fields'].extend(missing)
            staged={'schema_version':1,'run_key':run_key,'cutoff_at_us':cutoff,'started_at_us':now_us(),'deadline_at_us':now_us()+max(1,min(600,int(deadline_seconds)))*1000000,'inputs':coverage.pop('inputs'),'coverage':coverage,'candidates':candidates}
            staged['input_hash']=content_hash(staged)
            with immediate_transaction(self.session): self._event(experiment_id,'stage:'+run_key,'started',staged)
            self.session.commit()
        # 明確關閉讀取交易後才呼叫外部研究；不讓寫鎖跨網路／模型等待。
        self.session.close()
        start=time.monotonic(); response={'status':'normal' if not staged['candidates'] else 'unavailable','items':[]}; fetched={}
        remaining=(staged['deadline_at_us']-now_us())/1000000
        if remaining>0 and researcher is not None and staged['coverage']['qualified'] and staged['candidates']:
            try:
                research_cutoff = datetime.fromisoformat(cutoff_at.replace('Z','+00:00'))
                if isinstance(researcher, CodexSubscriptionResearcher):
                    response = researcher.analyze_batch(deepcopy(staged['candidates']), cutoff_at=research_cutoff, timeout_seconds=remaining)
                else:
                    response=bounded_external_call(lambda: researcher.analyze_batch(deepcopy(staged['candidates']),cutoff_at=research_cutoff),remaining)
            except TimeoutError: response={'status':'timeout','items':[]}
            except Exception as exc: response={'status':'error','items':[],'error':type(exc).__name__}
        if now_us()>staged['deadline_at_us']: response={'status':'timeout','items':[]}
        if not isinstance(response,dict) or not isinstance(response.get('items'),list):
            response={'status':'malformed_response','items':[]}
        items={str(i.get('symbol')):deepcopy(i) for i in response['items'] if isinstance(i,dict)}
        for item in items.values():
            item['decision_version']='official-event-filter-v2'
            item['prompt_version']='shortlist-research-v1'
        checks={}
        for candidate in staged['candidates']:
            item=items.get(candidate['symbol'])
            if not item or response.get('status')!='normal' or fetcher is None:
                checks[candidate['symbol']]={'accepted':False,'errors':['ai_unavailable'],'sources':[]}; continue
            originals=[]
            source_list=item.get('sources')
            if not isinstance(source_list,list): source_list=[]
            for source in source_list[:8]:
                if not isinstance(source,dict) or not isinstance(source.get('url'),str): continue
                if now_us()>staged['deadline_at_us']: break
                try:
                    remaining=min(10,max(.001,(staged['deadline_at_us']-now_us())/1000000))
                    fetched=bounded_external_call(lambda url=source['url'],limit=remaining: fetcher.fetch(url,timeout=limit),remaining)
                    if isinstance(fetched,dict): originals.append(fetched)
                except Exception: pass
            checks[candidate['symbol']]=self.verify_evidence(item=item,fetched_sources=originals,cutoff_at=cutoff_at,expected_symbol=candidate['symbol'])
        if now_us()>staged['deadline_at_us']:
            checks={c['symbol']:{'accepted':False,'errors':['deadline_expired'],'sources':[]} for c in staged['candidates']}
        decision_at=max(cutoff,now_us()); links=[(i,'strategy') for i in staged['inputs'].get('source_ids',[])]
        with immediate_transaction(self.session):
            already=self.session.execute(text('SELECT id,manifest_json FROM swing_input_batches WHERE experiment_id=:e AND run_key=:k'),{'e':experiment_id,'k':run_key}).mappings().first()
            if already:
                saved=json.loads(already['manifest_json'])
                if saved.get('staging_hash')!=staged['input_hash']: raise ValueError('idempotency_conflict')
                return {'batch_id':already['id'],'status':'frozen','replayed':True,'comparison':self.compare(experiment_id)}
            decisions=[]
            for candidate in staged['candidates']:
                symbol=candidate['symbol']; check=checks[symbol]; item=items.get(symbol,{})
                evidence_id=None
                outcome='unavailable' if response.get('status')!='normal' else 'evidence_rejected'
                if check['accepted']:
                    outcome='veto' if item['exclude_recommended'] is True else 'allow'
                    sources=check['sources']; public=max(to_us(s['published_at']) for s in sources); obtained=max(to_us(s['obtained_at']) for s in sources); verified=max(to_us(s['verified_at']) for s in sources)
                    evidence=self.repo.append_source({'source':'verified-event-fetcher','kind':'ai_evidence','source_key':f'{experiment_id}:{run_key}:{symbol}','symbol':symbol,'data_period':str(cutoff_at)[:10],'public_at':from_us(public),'obtained_at':from_us(obtained),'verified_at':from_us(verified),'quality':'verified','payload':{'schema_version':1,'sources':sources,'item':item,'decision_version':check['decision_version'],'outcome':outcome}})
                    evidence_id=evidence['id']
                decisions.append((candidate,outcome,evidence_id,{'schema_version':1,'raw':item,'verification':check,'runner_status':response.get('status')}))
            manifest={'schema_version':1,'cutoff_at_us':cutoff,'staging_hash':staged['input_hash'],'inputs':staged['inputs'],'candidates':staged['candidates'],'ai_status':response.get('status'),'execution_state':'awaiting_next_session'}
            manifest['execution_decision_time_basis']='batch_cutoff_at_us' if exp['mode']=='historical_exploratory' else 'decision_at_us'
            manifest['execution_decision_at_us']=cutoff if exp['mode']=='historical_exploratory' else decision_at
            batch=freeze_batch(self.session,experiment_id,run_key,cutoff,manifest,coverage=staged['coverage'],sources=links,decision_at_us=decision_at,elapsed_ms=max(0,(now_us()-staged.get('started_at_us',now_us()))//1000))
            for candidate,outcome,evidence_id,ai in decisions:
                insert_row(self.session,'swing_candidate_decisions',dict(experiment_id=experiment_id,batch_id=batch,symbol=candidate['symbol'],quant_rank=candidate['rank'],quant_json=canonical_json(candidate),ai_outcome=outcome,evidence_version_id=evidence_id,ai_json=canonical_json(ai),payload_hash=content_hash({'quant':candidate,'ai':ai}),created_at_us=decision_at))
            blocked=any(o not in ('allow','veto') for _,o,_,_ in decisions) or response.get('status')!='normal'
            self._event(experiment_id,'frozen:'+run_key,'incomplete' if blocked or not staged['coverage']['qualified'] else 'completed',{'schema_version':1,'batch_id':batch,'ai_status':response.get('status'),'coverage':staged['coverage'],'incomplete':blocked})
        self.session.commit()
        return {'batch_id':batch,'status':'incomplete' if blocked or not staged['coverage']['qualified'] else 'frozen','coverage':staged['coverage'],'decisions':len(decisions),'comparison':self.compare(experiment_id)}

    def preview_candidates(self, experiment_id, cutoff_at):
        """雷達／回放／紙帳共用入口，凍結設定不同時一律停止合格輸出。"""
        coverage=self.preflight(experiment_id,cutoff_at=cutoff_at)
        config=self.get_experiment(experiment_id)['config']
        candidates,missing=self._candidates(config,coverage['inputs'],cutoff_at)
        coverage['missing_fields']=sorted(set(coverage['missing_fields']+missing))
        coverage['qualified']=not coverage['missing_fields']
        coverage['status']='complete' if coverage['qualified'] else 'incomplete'
        return {'experiment_id':experiment_id,'config_hash':content_hash(config),'candidates':candidates if coverage['qualified'] else [],'coverage':{k:v for k,v in coverage.items() if k!='inputs'},'mode':config['mode']}

    def _execution_market(self,config,symbol,day,calendar):
        """只取該日當時可得開盤及之前一個交易日流動性，絕不取未來價格。"""
        opening=day+'T09:00:00+08:00'
        rows=self.repo.eligible_sources(kind='market_bar',symbol=symbol,cutoff_at=opening,mode=config['mode'])
        current=[r for r in rows if r['data_period']==day and r['payload'].get('open') is not None
                 and r['payload'].get('available_at') and to_us(r['payload']['available_at'])<=to_us(opening)]
        if config['mode']=='prospective': current=[r for r in current if r['payload'].get('phase')=='open']
        if not current: return [],[]
        source=current[-1]; previous_day=max((d for d in calendar if d<day),default=None)
        previous=[r for r in rows if r['data_period']==previous_day and r['payload'].get('volume') is not None
                  and r['payload'].get('available_at') and to_us(r['payload']['available_at'])<to_us(opening)]
        prior=previous[-1] if previous else None
        prior_volume=prior['payload']['volume'] if prior else None
        bar={'date':day,'open':source['payload']['open'],'prior_volume':prior_volume,
             'available_at':source['payload']['available_at'],'session_open_at':opening}
        return [bar],[source['id']]+([prior['id']] if prior else [])

    def observe(self,experiment_id,at,run_key):
        """伺服器可信來源驅動：執行、公司行動、交割、共同退出及逐日估值。"""
        from strategies.multi.risk import confirmed_swing_exit
        exp=self.get_experiment(experiment_id); config=exp['config']; at_us=to_us(at)
        if config.get('engine_fingerprint')!=engine_fingerprint(): raise ValueError('engine_version_requires_new_confirmation')
        if not run_key or len(run_key)>150: raise ValueError('invalid_observation_run_key')
        request={'schema_version':1,'at_us':at_us,'config_hash':exp['config_hash']}
        old=self.session.execute(text('SELECT detail_json FROM swing_experiment_events WHERE experiment_id=:e AND event_key=:k'),{'e':experiment_id,'k':'observe:'+run_key}).scalar_one_or_none()
        if old:
            old=json.loads(old)
            if old['request']['config_hash']!=request['config_hash']: raise ValueError('idempotency_conflict')
            return old['result']
        # 前瞻觀察只接受當日執行，不允許日後補算已錯過的開盤成交。
        taipei=ZoneInfo('Asia/Taipei')
        current_day=datetime.fromtimestamp(at_us/1e6,timezone.utc).astimezone(taipei).date().isoformat()
        if config['mode']=='prospective' and abs(now_us()-at_us)>15*60*1000000:
            raise ValueError('prospective_observation_expired')
        calendars=self.repo.eligible_sources(kind='calendar',symbol=None,cutoff_at=at,mode=config['mode'])
        calendar=sorted({d for source in calendars for d in source['payload'].get('sessions',[])})
        days=[day for day in calendar if config['start_date']<=day<=min(current_day,config['end_date'])]
        if config['mode']=='prospective': days=[day for day in days if day==current_day]
        ledger=PortfolioLedger(self.session,experiment_id); results=[]; missing=[]
        if not calendar_coverage(config,calendars,min(current_day,config['end_date'])): missing.append('calendar_or_corporate_action_coverage')
        if not days: missing.append('observation_calendar')
        with immediate_transaction(self.session):
            # 觀察的全部帳務原子寫入；單一事件重跑由各帳本鍵檢查原內容。
            with self.session.begin_nested():
                for day in days:
                    opening=day+'T09:00:00+08:00'; closing=day+'T13:30:00+08:00'
                    if to_us(opening)>at_us: continue
                    open_us=to_us(opening)
                    markets={}; context={}
                    for symbol in [config['etf_symbol'],*config['stock_pool']]:
                        markets[symbol],ids=self._execution_market(config,symbol,day,calendar)
                        published_cal=self.repo.eligible_sources(kind='calendar',symbol=None,cutoff_at=opening,mode=config['mode'])
                        good=bool(markets[symbol] and ids and calendar_coverage(config,published_cal,day) and markets[symbol][0]['prior_volume'] is not None and not missing)
                        context[symbol]={'source_ids':sorted(set(ids+[r['id'] for r in published_cal])),
                                         'coverage':{'schema_version':1,'status':'complete' if good else 'incomplete','phase':'open','symbol':symbol,'date':day}}
                    for arm in 'ABC':
                        latest=ledger._latest(arm)
                        if not latest or latest['effective_at_us']<=open_us:
                            ledger.settle(arm,opening,'day-settlement:'+day)
                    actions=self.repo.eligible_sources(kind='corporate_action',symbol=None,cutoff_at=opening,mode=config['mode'])
                    for action in actions:
                        if action['payload'].get('ex_date')!=day: continue
                        if config['execution']['price_basis']!='raw': continue
                        for arm in 'ABC':
                            ledger.corporate_action(arm,action['id'],opening,'action:'+str(action['id']))
                    rows=self.session.execute(text(f'SELECT d.*,b.decision_at_us,b.cutoff_at_us,b.coverage_status FROM swing_candidate_decisions d JOIN swing_input_batches b ON b.id=d.batch_id JOIN swing_experiments e ON e.id=b.experiment_id WHERE d.experiment_id=:e AND {EXECUTION_DECISION_TIME_SQL}<:at ORDER BY b.cutoff_at_us,d.quant_rank'),{'e':experiment_id,'at':open_us}).mappings().all()
                    pending=[]
                    for decision in rows:
                        q=json.loads(decision['quant_json'])
                        signal_day=datetime.fromtimestamp(to_us(q['signal_at'])/1e6,timezone.utc).astimezone(taipei).date().isoformat()
                        next_day=next((d for d in calendar if d>signal_day),None)
                        if next_day==day: pending.append(decision)
                    # 新買入前既有退出優先，三組共用成本與時段。
                    exit_events=self.session.execute(text("SELECT detail_json,event_key FROM swing_experiment_events WHERE experiment_id=:e AND kind='started' AND event_key LIKE 'exit:%' ORDER BY id"),{'e':experiment_id}).mappings().all()
                    for intent_row in exit_events:
                        intent=json.loads(intent_row['detail_json'])
                        if next((d for d in calendar if d>intent['signal_day']),None)!=day: continue
                        arm=intent['arm']; symbol=intent['symbol']
                        if ledger._latest(arm)['effective_at_us']>open_us: continue
                        if not markets[symbol]: missing.append('exit_open:'+symbol+':'+day); continue
                        results.append(ledger.submit_order(arm,symbol,intent['quantity'],'sell',intent['signal_at'],markets[symbol],intent_row['event_key']+':fill',calendar=calendar,execution_context=context[symbol]))
                    latest_research=self.session.execute(text(f"SELECT b.* FROM swing_input_batches b JOIN swing_experiments e ON e.id=b.experiment_id WHERE b.experiment_id=:e AND {EXECUTION_DECISION_TIME_SQL}<:at ORDER BY b.cutoff_at_us DESC,b.id DESC"),{'e':experiment_id,'at':open_us}).mappings().all()
                    research=next((b for b in latest_research if 'candidates' in json.loads(b['manifest_json'])),None)
                    if research and ledger._latest('C')['effective_at_us']<=open_us:
                        manifest=json.loads(research['manifest_json'])
                        unavailable=manifest.get('ai_status')!='normal' or any(d['ai_outcome'] not in ('allow','veto') for d in pending)
                        ledger.set_ai_status('unavailable' if unavailable else 'normal',opening,'ai-at-open:'+day)
                    for decision in pending:
                        q=json.loads(decision['quant_json']); symbol=q['symbol']
                        for arm in ('B','C'):
                            if ledger._latest(arm)['effective_at_us']>open_us: continue
                            if arm=='C' and decision['ai_outcome']=='veto': continue
                            if not markets[symbol]: missing.append('entry_open:'+symbol+':'+day); continue
                            # 任何 AI 拒收均阻擋 C 全組新增；B 仍依同排序執行。
                            results.append(ledger.submit_order(arm,symbol,q['quantity'],q['side'],q['signal_at'],markets[symbol],f'decision:{decision["id"]}:{arm}',q.get('stop_loss_price'),decision['id'],calendar=calendar,execution_context=context[symbol]))
                    first_day=next((d for d in calendar if d>config['start_date']),None)
                    if day==first_day and ledger._latest('A')['effective_at_us']<=open_us and markets[config['etf_symbol']]:
                        symbol=config['etf_symbol']; price=money(markets[symbol][0]['open'])*(Decimal(1)+Decimal(config['execution']['slippage_bps'])/10000)
                        rate=Decimal(config['fees']['etf']['commission_rate']); minimum=config['fees']['etf']['minimum_fee_e4']
                        quantity=int((100000000-minimum)/(price*(1+rate)))
                        if quantity>0: results.append(ledger.submit_order('A',symbol,quantity,'buy',config['start_date']+'T15:00:00+08:00',markets[symbol],'benchmark-buy',calendar=calendar,execution_context=context[symbol]))
                    if to_us(closing)>at_us: continue
                    closes=self._load_inputs(config,closing)
                    for arm in 'ABC':
                        if ledger._latest(arm)['effective_at_us']>to_us(closing): continue
                        state=ledger.snapshot(arm); prices={}
                        for symbol in state['positions']:
                            bars=[b for b in closes['market'].get(symbol,[]) if b['date']==day and b.get('close') is not None]
                            if bars: prices[symbol]=bars[-1]['close']
                            else: missing.append('valuation:'+symbol+':'+day)
                        if set(state['positions'])-set(prices): continue
                        ledger.value(arm,prices,closing,'daily-value:'+day,valuation_context={'source_ids':closes['source_ids'],'coverage':{'schema_version':1,'status':'complete' if not missing else 'incomplete','phase':'close','date':day}})
                        if arm=='A': continue
                        for symbol,position in ledger.snapshot(arm)['positions'].items():
                            reason=confirmed_swing_exit(config,position,money(prices[symbol]),calendar,day)
                            if reason:
                                self._event(experiment_id,'exit:'+day+':'+arm+':'+symbol,'started',{'schema_version':1,'arm':arm,'symbol':symbol,'quantity':position['quantity'],'signal_day':day,'signal_at':day+'T15:00:00+08:00','reason':reason})
                    self._followup(experiment_id,day,closing,config,calendar,closes)
                result={'status':'incomplete' if missing else 'observed','missing_fields':sorted(set(missing)),'results':results,'execution_assumption':'daily_proxy'}
                self._event(experiment_id,'observe:'+run_key,'incomplete' if missing else 'completed',{'schema_version':1,'request':request,'result':result})
        return result

    def _followup(self,experiment_id,day,at,config,calendar,inputs):
        rows=self.session.execute(text('SELECT d.*,b.cutoff_at_us FROM swing_candidate_decisions d JOIN swing_input_batches b ON b.id=d.batch_id WHERE d.experiment_id=:e'),{'e':experiment_id}).mappings().all()
        for decision in rows:
            q=json.loads(decision['quant_json']); start=str(q['signal_at'])[:10]
            forward=[d for d in calendar if d>start]
            horizon=config['observation_sessions']
            if len(forward)<horizon or forward[horizon-1]>day: continue
            old=self.session.execute(text('SELECT id FROM swing_opportunity_outcomes WHERE decision_id=:d AND horizon_sessions=:h'),{'d':decision['id'],'h':horizon}).first()
            if old: continue
            bars=inputs['market'].get(q['symbol'],[])
            opening=next((b for b in bars if b['date']==forward[0]),None)
            final=next((b for b in bars if b['date']==forward[horizon-1]),None)
            usable=bool(opening and final and opening.get('open') and final.get('close'))
            outcome={'schema_version':1,'ai_outcome':decision['ai_outcome'],'start_session':forward[0],'end_session':forward[horizon-1],
                     'opportunity_return':str(Decimal(str(final['close']))/Decimal(str(opening['open']))-1) if usable else None,
                     'interpretation':'固定觀察窗口原始價格機會報酬；非 C 實際交易獲利','corporate_action_adjusted':False}
            # 原始價格窗口若有公司行動，不能拿未調整價差冒充可比機會報酬。
            if any(start<a.get('ex_date','')<=forward[horizon-1] and a.get('symbol',q['symbol'])==q['symbol'] for a in inputs.get('corporate_actions',[])):
                usable=False; outcome['opportunity_return']=None; outcome['reason']='corporate_action_followup_requires_cashflow_basis'
            outcome_batch=freeze_batch(self.session,experiment_id,'outcome:'+str(decision['id']),to_us(at),{'schema_version':1,'purpose':'opportunity_outcome','outcome':outcome},sources=[(i,'valuation') for i in inputs.get('source_ids',[])])
            insert_row(self.session,'swing_opportunity_outcomes',dict(experiment_id=experiment_id,decision_id=decision['id'],decision_batch_id=decision['batch_id'],outcome_batch_id=outcome_batch,horizon_sessions=horizon,status='complete' if usable else 'missing',outcome_json=canonical_json(outcome),payload_hash=content_hash(outcome),created_at_us=now_us()))

    def execute_pending(self,experiment_id,market,calendar,at,run_key):
        # 舊程式內呼叫亦不能把自報行情轉成合格成交，統一回到可信來源。
        if market or calendar: raise ValueError('untrusted_execution_inputs: 請使用 observe 讀取封存來源')
        return self.observe(experiment_id,at,run_key)

    def resume(self,experiment_id,arm,actor,note,at,event_key):
        with immediate_transaction(self.session): return PortfolioLedger(self.session,experiment_id).resume(arm,actor,note,at,event_key)

    def compare(self,experiment_id):
        from app.reports.swing_comparison import build_comparison
        return build_comparison(self.session,experiment_id)
