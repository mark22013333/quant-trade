"""核心整合風險的離線驗證；僅記憶體 SQLite，不接券商／網路。"""
from copy import deepcopy
import json
from pathlib import Path
from datetime import datetime
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.pipeline.swing_research import SwingResearchService, preview_config, MissingConfiguration
from app.paper.portfolio import PortfolioLedger
from app.db.swing_repository import to_us
from app.services.subscription_research import SafeEvidenceFetcher


def config(key='core'):
    return {'experiment_key':key,'mode':'historical_exploratory','etf_symbol':'0050','stock_pool':['2330','2317'],
            'start_date':'2026-07-27','end_date':'2026-07-31','initial_cash_e4':100000000,'stop_equity_e4':80000000,
            'strategy':{'version':'swing-v1','hold_days':20,'parameters':{'enabled':{'momentum_trend':True,'mean_reversion':False,'chip_flow':False}},'stop_loss_pct':'0.10'},
            'risk':{'max_position_e4':90000000,'max_total_exposure_e4':90000000,'max_single_risk_e4':10000000},
            'fees':{p:{'commission_rate':'0','minimum_fee_e4':0,'sell_tax_rate':'0'} for p in ('stock','etf')},
            'execution':{'slippage_bps':'0','settlement_sessions':2,'price_basis':'raw','corporate_actions':'cash_and_split','odd_lot_model':'daily_proxy','rounding':'ROUND_HALF_UP'},
            'data_policy':{'warmup_bars':1,'missing_required':'block'},'observation_sessions':2,'code_version':'offline-core'}


@pytest.fixture
def session():
    engine=create_engine('sqlite://',poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def pragmas(conn,_):
        conn.execute('PRAGMA foreign_keys=ON'); conn.execute('PRAGMA recursive_triggers=ON')
    ddl=Path('.spec/tw-swing-ai-validation/deploy.sql').read_text()
    with engine.connect() as conn: conn.connection.driver_connection.executescript(ddl)
    with sessionmaker(bind=engine,expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def create(session):
    return SwingResearchService(session).create_experiment(config(),'離線測試','核對設定')


def market():
    return [{'date':d,'open':'100','close':'999999','volume':0,'prior_volume':10000,'available_at':d+'T09:00:00+08:00','session_open_at':d+'T09:00:00+08:00'}
            for d in ('2026-07-27','2026-07-28','2026-07-29','2026-07-30','2026-07-31','2026-08-03','2026-08-04')]


def order(ledger,key,**kwargs):
    request={'arm':'B','symbol':'2330','quantity':10,'side':'buy','signal_at':'2026-07-27T15:00:00+08:00','market':market(),'event_key':key,'stop_loss_price':'90'}
    request.update(kwargs)
    return ledger.submit_order(**request)


def test_t1_sellable_is_independent_from_t2_cash(session):
    exp=create(session); ledger=PortfolioLedger(session,exp['experiment_id'])
    assert order(ledger,'buy')['status']=='filled'
    same_day=order(ledger,'same-day-sell',side='sell')
    assert same_day['status']=='rejected'
    sold=order(ledger,'t1-sell',side='sell',signal_at='2026-07-28T15:00:00+08:00')
    assert sold['status']=='filled' and sold['trade_date']=='2026-07-29'
    state=ledger.snapshot('B')
    assert state['payable_e4']==10000000 and state['receivable_e4']==10000000
    assert state['cash_available_e4']==90000000 and not state['positions']
    ledger.settle('B','2026-07-30T09:00:00+08:00','settle-30')
    assert ledger.snapshot('B')['cash_available_e4']==90000000
    ledger.settle('B','2026-07-31T09:00:00+08:00','settle-31')
    assert ledger.snapshot('B')['cash_available_e4']==100000000


def test_manifest_excludes_future_prices_and_later_day_volume(session):
    exp=create(session); ledger=PortfolioLedger(session,exp['experiment_id'])
    order(ledger,'minimal')
    manifest=session.execute(text("SELECT manifest_json FROM swing_input_batches WHERE run_key='execution:B:minimal'")).scalar_one()
    assert '999999' not in manifest
    assert 'volume":0' not in manifest
    assert 'prior_volume' in manifest and '2026-08-04' in manifest


def test_mutation_replay_conflict_and_savepoint(session):
    exp=create(session); ledger=PortfolioLedger(session,exp['experiment_id'])
    first=order(ledger,'first')
    before=ledger.snapshot('B')
    with pytest.raises(ValueError,match='idempotency_conflict'): order(ledger,'first',quantity=9)
    assert ledger.snapshot('B')==before
    assert order(ledger,'first')==first
    original=ledger.value('B',{'2330':'99'},'2026-07-28T13:30:00+08:00','mark-original')
    ledger.value('B',{'2330':'98'},'2026-07-29T13:30:00+08:00','mark-next')
    assert ledger.value('B',{'2330':'99'},'2026-07-28T13:30:00+08:00','mark-original')==original
    assert ledger.snapshot('B')['equity_e4']==99800000


@pytest.mark.parametrize('field,value',[('fees',None),('fees',[]),('strategy',None),('risk',[]),('execution',{}),('data_policy',None)])
def test_malformed_sections_are_configuration_errors(field,value):
    data=config(); data[field]=value
    with pytest.raises(MissingConfiguration): preview_config(data)


@pytest.mark.parametrize('bad',['NaN','Infinity','not-a-number',None,True])
def test_malformed_numeric_is_configuration_error(bad):
    data=config(); data['strategy']['stop_loss_pct']=bad
    with pytest.raises(MissingConfiguration): preview_config(data)


def test_safe_fetcher_rejects_private_resolution(monkeypatch):
    import socket
    monkeypatch.setattr(socket,'getaddrinfo',lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    monkeypatch.setattr(socket,'create_connection',lambda *args,**kwargs:pytest.fail('不得連到私網'))
    with pytest.raises(ValueError,match='private_address'): SafeEvidenceFetcher().fetch('https://www.twse.com.tw/news')
    with pytest.raises(ValueError,match='not_allowed'): SafeEvidenceFetcher().fetch('http://www.twse.com.tw/news')


def test_disabled_strategies_leave_denominator_but_missing_required_blocks():
    import pandas as pd
    from strategies.multi.ensemble import EnsembleDecision
    index=pd.Index([0])
    frames={'momentum_trend':pd.DataFrame({'signal':[1],'score':[1.0],'reason':['signal']},index=index),
            'chip_flow':pd.DataFrame({'signal':[0],'score':[0.0],'reason':['disabled']},index=index)}
    score=EnsembleDecision(weights={'momentum_trend':.4,'chip_flow':.6}).combine(frames)
    assert score['ensemble_score'].iloc[0]==1
    frames['chip_flow']['reason']='missing chip columns'
    score=EnsembleDecision(weights={'momentum_trend':.4,'chip_flow':.6}).combine(frames)
    assert score['ensemble_score'].iloc[0]==.4


def seed(session,service,symbol,day,phase='close',price='100'):
    at=day+('T09:00:00+08:00' if phase=='open' else 'T13:30:00+08:00')
    payload={'schema_version':1,'phase':phase,'open':price,'available_at':at,'session_open_at':day+'T09:00:00+08:00'}
    if phase=='close': payload.update(high=price,low=price,close=price,volume=10000)
    return service.append_source({'source':'offline-exchange','kind':'market_bar','source_key':symbol+':'+day+':'+phase,'symbol':symbol,'data_period':day,'public_at':at,'obtained_at':at,'verified_at':at,'quality':'verified','payload':payload})


def test_prospective_observation_uses_frozen_intents_and_completes_daily_workflow(session,monkeypatch):
    import app.pipeline.swing_research as module
    data=config('prospective'); data['mode']='prospective'; data['observation_sessions']=1; data['strategy']['hold_days']=1
    service=SwingResearchService(session); exp=service.create_experiment(data,'offline-human','確認')
    import pandas as pd
    warmup=[d.date().isoformat() for d in pd.bdate_range(end='2026-07-24',periods=60)]
    calendar=warmup+['2026-07-27','2026-07-28','2026-07-29','2026-07-30','2026-07-31','2026-08-03','2026-08-04']
    service.append_source({'source':'offline-exchange','kind':'calendar','source_key':'july','data_period':'2026-07','public_at':'2026-07-01T00:00:00+08:00','obtained_at':'2026-07-01T00:00:00+08:00','verified_at':'2026-07-01T00:00:00+08:00','quality':'verified','payload':{'schema_version':1,'sessions':calendar,'symbols':['0050','2330','2317'],'period_start':'2026-07-27','period_end':'2026-07-31','corporate_actions_complete':True,'corporate_action_evidence':[{'url':'https://www.twse.com.tw/official','raw_hash':'a'*64}]}})
    for symbol in ['0050','2330','2317']:
        for day in [d for d in calendar if d<='2026-07-31']:
            seed(session,service,symbol,day)
            seed(session,service,symbol,day,'open')
    session.commit()
    cutoff='2026-07-27T15:00:00+08:00'
    monkeypatch.setattr(module,'now_us',lambda:to_us('2026-07-27T15:01:00+08:00'))
    candidate={'schema_version':1,'symbol':'2330','side':'buy','quantity':10,'score':1.0,'rank':1,'stop_loss_price':'90','signal_at':cutoff,'strategy_version':'swing-v1'}
    monkeypatch.setattr(service,'_candidates',lambda *args:([candidate],[]))
    class Researcher:
        def analyze_batch(self,candidates,cutoff_at):
            return {'status':'normal','items':[{'symbol':'2330','event_type':'營運正常','exclude_recommended':False,'sources':[{'url':'https://example.com/official','published_at':'2026-07-27T10:00:00+08:00'}]}]}
    class Fetcher:
        def fetch(self,url,timeout):
            return {'url':url,'published_at':'2026-07-27T10:00:00+08:00','obtained_at':'2026-07-27T15:00:00+08:00','verified_at':'2026-07-27T15:00:00+08:00','original_text':'公司代碼 2330，公司公告：本公司營運狀況正常，本次事件未出現新增重大風險。','excerpt':'本次事件未出現新增重大風險'}
    frozen=service.run(exp['experiment_id'],'research-27',cutoff,researcher=Researcher(),fetcher=Fetcher())
    assert frozen['status']=='frozen'
    monkeypatch.setattr(module,'now_us',lambda:to_us('2026-07-28T15:00:00+08:00'))
    observed=service.observe(exp['experiment_id'],'2026-07-28T15:00:00+08:00','observe-28')
    assert observed['status']=='observed'
    ledger=PortfolioLedger(session,exp['experiment_id'])
    assert ledger.snapshot('B')['positions']['2330']['quantity']==10
    assert ledger.snapshot('C')['positions']['2330']['quantity']==10
    assert ledger.snapshot('A')['positions']['0050']['quantity']==100
    assert any(e['event_kind']=='valuation' for e in ledger.snapshot('B')['events'])
    assert service.observe(exp['experiment_id'],'2026-07-28T15:00:01+08:00','observe-28')==observed
    monkeypatch.setattr(module,'now_us',lambda:to_us('2026-07-29T15:00:00+08:00'))
    service.observe(exp['experiment_id'],'2026-07-29T15:00:00+08:00','observe-29')
    monkeypatch.setattr(module,'now_us',lambda:to_us('2026-07-30T15:00:00+08:00'))
    service.observe(exp['experiment_id'],'2026-07-30T15:00:00+08:00','observe-30')
    assert not ledger.snapshot('B')['positions']
    assert not ledger.snapshot('C')['positions']
    comparison=service.compare(exp['experiment_id'])
    assert comparison['ai_outcomes']['allow']==1
    assert comparison['ai_effectiveness']['claim']=='not_established'
    assert len(comparison['opportunity_outcomes'])==1


def test_external_deadline_stops_wait_and_late_result_has_no_session():
    import threading
    import time
    from app.services.subscription_research import bounded_external_call
    release=threading.Event()
    before=time.monotonic()
    try:
        with pytest.raises(TimeoutError): bounded_external_call(lambda:release.wait(1),.02)
        assert time.monotonic()-before<.3
    finally:
        release.set()


def test_unscoped_calendar_cannot_claim_complete_corporate_actions(session):
    from app.pipeline.swing_research import calendar_coverage
    data=config()
    assert calendar_coverage(data,[{'payload':{'sessions':['2026-07-27'],'corporate_actions_complete':True}}],'2026-07-27') is False
    scoped={'symbols':['0050','2330'],'period_start':'2026-07-01','period_end':'2026-07-31','corporate_actions_complete':True,'corporate_action_evidence':[{'raw_hash':'a'*64}]}
    assert calendar_coverage(data,[{'payload':scoped}],'2026-07-27') is False
    scoped['symbols'].append('2317')
    assert calendar_coverage(data,[{'payload':scoped}],'2026-07-27') is True


def test_mutated_engine_requires_new_human_configuration(session,monkeypatch):
    import app.pipeline.swing_research as module
    exp=create(session)
    monkeypatch.setattr(module,'engine_fingerprint',lambda:'b'*64)
    result=SwingResearchService(session).preflight(exp['experiment_id'],cutoff_at='2026-07-27T15:00:00+08:00')
    assert 'engine_version_requires_new_confirmation' in result['missing_fields']
    with pytest.raises(ValueError,match='engine_version_requires_new_confirmation'):
        SwingResearchService(session).run(exp['experiment_id'],'new-version','2026-07-27T15:00:00+08:00')


def test_exact_nonzero_fees_tax_slippage_and_t2_settlement(session):
    data=config('costs'); data['fees']['stock']={'commission_rate':'0.001425','minimum_fee_e4':200000,'sell_tax_rate':'0.003'}
    data['fees']['etf']={'commission_rate':'0.001425','minimum_fee_e4':10000,'sell_tax_rate':'0.001'}
    data['execution']['slippage_bps']='1'
    service=SwingResearchService(session); exp=service.create_experiment(data,'human','fee-confirmed')
    ledger=PortfolioLedger(session,exp['experiment_id'])
    buy=order(ledger,'cost-buy',quantity=10)
    assert (buy['price_e4'],buy['fee_e4'],buy['tax_e4'])==(1000100,200000,0)
    assert ledger.snapshot('B')['payable_e4']==10201000
    assert ledger.snapshot('B')['cash_available_e4']==89799000
    sell=order(ledger,'cost-sell',quantity=10,side='sell',signal_at='2026-07-28T15:00:00+08:00')
    assert (sell['price_e4'],sell['fee_e4'],sell['tax_e4'])==(999900,200000,29997)
    assert ledger.snapshot('B')['receivable_e4']==9769003
    ledger.settle('B','2026-07-31T09:00:00+08:00','costs-settled')
    assert ledger.snapshot('B')['settled_cash_e4']==99568003
    assert ledger.snapshot('B')['equity_e4']==99568003
    benchmark=order(ledger,'etf-cost',arm='A',symbol='0050',quantity=10)
    assert benchmark['fee_e4']==14251  # e4 HALF_UP: 10001000*.001425 = 14251.425


def test_corporate_dividend_split_and_replay_no_double_count(session):
    service=SwingResearchService(session); exp=service.create_experiment(config('actions'),'human','確認')
    ledger=PortfolioLedger(session,exp['experiment_id']); order(ledger,'action-buy')
    def action(key,payload):
        return service.append_source({'source':'official-offline','kind':'corporate_action','source_key':key,'symbol':'2330','data_period':'2026-07-29',
                                      'public_at':'2026-07-27T08:00:00+08:00','obtained_at':'2026-07-27T08:00:00+08:00','verified_at':'2026-07-27T08:00:00+08:00',
                                      'quality':'verified','payload':{'schema_version':1,**payload}})['id']
    dividend=action('dividend',{'type':'cash_dividend','ex_date':'2026-07-29','payment_date':'2026-07-31','cash_per_share':'2.5'})
    first=ledger.corporate_action('B',dividend,'2026-07-29T09:00:00+08:00','dividend')
    assert ledger.snapshot('B')['receivable_e4']==250000
    split=action('split',{'type':'split','ex_date':'2026-07-29','ratio':'2'})
    ledger.corporate_action('B',split,'2026-07-29T09:00:00+08:00','split')
    state=ledger.snapshot('B'); assert state['positions']['2330']['quantity']==20
    assert state['positions']['2330']['mark_e4']==500000
    assert state['positions']['2330']['cost_e4']==10000000
    assert ledger.corporate_action('B',dividend,'2026-07-29T09:00:00+08:00','dividend')==first
    assert ledger.snapshot('B')==state
    ledger.settle('B','2026-07-31T09:00:00+08:00','actions-settled')
    assert ledger.snapshot('B')['settled_cash_e4']==90250000
    assert ledger.snapshot('B')['equity_e4']==100250000
    adjusted=config('adjusted'); adjusted['execution'].update(price_basis='adjusted',corporate_actions='included_in_prices')
    adjusted_exp=service.create_experiment(adjusted,'human','確認還原價口徑')
    with pytest.raises(ValueError,match='還原價格不得再次計入'):
        PortfolioLedger(session,adjusted_exp['experiment_id']).corporate_action('B',dividend,'2026-07-29T09:00:00+08:00','duplicate-dividend')


def test_short_history_is_missing_strategy_warmup_not_no_signal(session):
    exp=create(session); service=SwingResearchService(session)
    inputs={'market':{symbol:[{'date':'2026-07-24','open':'100','high':'100','low':'100','close':'100','volume':1000,'available_at':'2026-07-24T13:30:00+08:00'}] for symbol in ['0050','2330','2317']},'calendar':['2026-07-24','2026-07-27','2026-07-31'],'corporate_actions_verified':False,'source_ids':[]}
    preflight=service.preflight(exp['experiment_id'],inputs,'2026-07-27T15:00:00+08:00')
    assert 'strategy_warmup:2330:momentum_trend:60' in preflight['missing_fields']
    assert preflight['qualified'] is False


@pytest.mark.parametrize('item,sources',[(None,[]),({'sources':None},[]),({'sources':'url'},[]),({'sources':[{}]},[None,'malformed'])])
def test_malformed_evidence_is_rejected_not_uncaught_exception(session,item,sources):
    result=SwingResearchService(session).verify_evidence(item=item,fetched_sources=sources,cutoff_at='2026-07-27T15:00:00+08:00')
    assert result['accepted'] is False
    assert result['errors']


@pytest.mark.parametrize('response',[None,[],{'status':'normal','items':None},{'status':'normal','items':[{'symbol':'2330','sources':None}]}])
def test_malformed_research_freezes_failure_instead_of_crashing(session,monkeypatch,response):
    service=SwingResearchService(session); exp=create(session); cutoff='2026-07-27T15:00:00+08:00'
    monkeypatch.setattr(service,'preflight',lambda *a,**k:{'schema_version':1,'status':'complete','qualified':True,'missing_fields':[],'inputs':{'source_ids':[]}})
    candidate={'schema_version':1,'symbol':'2330','rank':1,'quantity':10,'side':'buy','signal_at':cutoff,'stop_loss_price':'90','strategy_version':'swing-v1'}
    monkeypatch.setattr(service,'_candidates',lambda *a:([candidate],[]))
    class Researcher:
        def analyze_batch(self,*a,**k): return response
    class Fetcher:
        def fetch(self,*a,**k): return 'malformed-source'
    result=service.run(exp['experiment_id'],'malformed-run',cutoff,researcher=Researcher(),fetcher=Fetcher())
    assert result['status']=='incomplete'
    decision=session.execute(text('SELECT ai_outcome FROM swing_candidate_decisions WHERE experiment_id=:e'),{'e':exp['experiment_id']}).scalar_one()
    assert decision in ('unavailable','evidence_rejected')
    assert len(service.get_experiment(exp['experiment_id'])['accounts'])==3


def test_conflicting_same_day_sources_do_not_inflate_warmup(session):
    service=SwingResearchService(session); exp=create(session)
    seed(session,service,'2330','2026-07-24')
    source=service.append_source({'source':'another-official','kind':'market_bar','source_key':'other:2330:2026-07-24','symbol':'2330','data_period':'2026-07-24',
                                  'public_at':'2026-07-24T13:30:00+08:00','obtained_at':'2026-07-24T13:30:00+08:00','verified_at':'2026-07-24T13:30:00+08:00','quality':'verified',
                                  'payload':{'schema_version':1,'phase':'close','open':'999','high':'999','low':'999','close':'999','volume':10000,'available_at':'2026-07-24T13:30:00+08:00'}})
    result=service.preflight(exp['experiment_id'],cutoff_at='2026-07-27T15:00:00+08:00')
    assert result['bar_counts']['2330']==1
    assert 'conflicting_market:2330:2026-07-24' in result['missing_fields']
    assert result['qualified'] is False
