"""從封存研究與事件鏈重建比較，不把資料不足當成零風險或 AI 有效。"""
from __future__ import annotations

import json
from decimal import Decimal
from sqlalchemy import text
from app.db.swing_repository import from_us


def _ratio(numerator,denominator):
    return str(Decimal(numerator)/Decimal(denominator)) if denominator else None


def build_comparison(session,experiment_id):
    experiment=session.execute(text('SELECT * FROM swing_experiments WHERE id=:e'),{'e':experiment_id}).mappings().first()
    if not experiment: raise ValueError('找不到研究實驗')
    config=json.loads(experiment['config_json'])
    batches=session.execute(text('SELECT * FROM swing_input_batches WHERE experiment_id=:e ORDER BY cutoff_at_us,id'),{'e':experiment_id}).mappings().all()
    decisions=session.execute(text('SELECT * FROM swing_candidate_decisions WHERE experiment_id=:e ORDER BY batch_id,quant_rank'),{'e':experiment_id}).mappings().all()
    outcomes=session.execute(text('SELECT * FROM swing_opportunity_outcomes WHERE experiment_id=:e ORDER BY decision_id'),{'e':experiment_id}).mappings().all()
    diagnostics=session.execute(text("SELECT kind,detail_json FROM swing_experiment_events WHERE experiment_id=:e AND kind IN ('incomplete','aborted')"),{'e':experiment_id}).mappings().all()
    arms=[]; common_times=[]
    for arm in 'ABC':
        rows=session.execute(text('SELECT l.* FROM swing_ledger_events l JOIN swing_paper_accounts a ON a.id=l.account_id WHERE a.experiment_id=:e AND a.arm=:a ORDER BY l.seq'),{'e':experiment_id,'a':arm}).mappings().all()
        last=rows[-1] if rows else None
        equity=last['equity_e4'] if last else 100000000
        peak=100000000; maximum_drawdown=Decimal(0); exposure=[]; timeline=[]
        for row in rows:
            peak=max(peak,row['equity_e4'])
            maximum_drawdown=max(maximum_drawdown,Decimal(peak-row['equity_e4'])/Decimal(peak))
            if row['event_kind'] in ('valuation','risk_breach'):
                exposure.append(Decimal(row['market_value_e4'])/Decimal(row['equity_e4']) if row['equity_e4']>0 else Decimal(0))
                timeline.append({'at':from_us(row['effective_at_us']),'equity_e4':row['equity_e4'],'exposure':str(exposure[-1])})
        common_times.append({p['at'] for p in timeline})
        trades=[row for row in rows if row['event_kind'] in ('buy','sell')]
        total_fees=sum(row['fee_e4'] for row in trades); total_tax=sum(row['tax_e4'] for row in trades)
        # 滑價成本以封存開盤與實際近似成交差計算，費稅不重複納入。
        slippage=0
        for row in trades:
            detail=json.loads(row['detail_json']); opening=detail.get('input',{}).get('open_input',{}).get('open')
            if opening is not None:
                slippage+=int(abs(Decimal(str(opening))*10000-row['price_e4'])*row['quantity'])
        arms.append({'arm':arm,'initial_cash_e4':100000000,'equity_e4':equity,'net_return':_ratio(equity-100000000,100000000),
                     'net_return_pct':str(Decimal(equity-100000000)/1000000),'max_drawdown':str(maximum_drawdown),
                     'average_exposure':str(sum(exposure)/len(exposure)) if exposure else None,
                     'cost_e4':total_fees+total_tax+slippage,'fees_e4':total_fees,'tax_e4':total_tax,'slippage_e4':slippage,
                     'trade_count':len(trades),'buy_count':sum(r['event_kind']=='buy' for r in trades),'sell_count':sum(r['event_kind']=='sell' for r in trades),
                     'cash_available_e4':last['settled_cash_e4']-last['payable_e4']-last['reserved_cash_e4'] if last else 100000000,
                     'market_value_e4':last['market_value_e4'] if last else 0,'risk_paused':bool(last['risk_paused']) if last else False,
                     'ai_blocked':bool(last['ai_blocked']) if last else False,'timeline':timeline,
                     'threshold_crossings':sum(r['equity_e4']<=80000000 and (i==0 or rows[i-1]['equity_e4']>80000000) for i,r in enumerate(rows))})
    for arm in arms: arm['vs_a_return']=str(Decimal(arm['net_return'])-Decimal(arms[0]['net_return']))
    research=[b for b in batches if json.loads(b['manifest_json']).get('purpose') not in ('execution','valuation','corporate_action') and 'candidates' in json.loads(b['manifest_json'])]
    coverage=[{'batch_id':b['id'],'cutoff_at':from_us(b['cutoff_at_us']),'status':b['coverage_status'],'detail':json.loads(b['coverage_json'])} for b in research]
    missing=[str(b['id']) for b in research if b['coverage_status']!='complete']
    counts={state:sum(d['ai_outcome']==state for d in decisions) for state in ('allow','veto','unavailable','evidence_rejected','not_requested')}
    same_period=bool(common_times and common_times[0]) and all(t==common_times[0] for t in common_times)
    prospective=experiment['mode']=='prospective'
    reasons=[]
    if not prospective: reasons.append('historical_ai_model_knowledge_leakage')
    if not research: reasons.append('no_frozen_research')
    if missing: reasons.append('incomplete_coverage')
    if any(b['coverage_status']!='complete' for b in batches if json.loads(b['manifest_json']).get('purpose') in ('execution','valuation')): reasons.append('untrusted_or_incomplete_execution_valuation')
    if diagnostics or counts['unavailable'] or counts['evidence_rejected']: reasons.append('ai_or_data_incomplete')
    if not same_period: reasons.append('valuation_period_not_aligned')
    if not outcomes or any(o['status']!='complete' for o in outcomes): reasons.append('opportunity_followup_incomplete')
    if min((len(a['timeline']) for a in arms),default=0)<max(20,config['observation_sessions']) or len(decisions)<20:
        reasons.append('insufficient_sample')
    # 統計顯著性尚未核准門檻；即使樣本夠，也只提供描述結果，不產生效果保證。
    return {'experiment_id':experiment_id,'mode':experiment['mode'],'config_hash':experiment['config_hash'],
            'period':{'start_date':config['start_date'],'end_date':config['end_date'],'aligned':same_period},
            'status':'incomplete' if reasons else 'descriptive_observation','qualified':not reasons,
            'execution_assumption':'daily_proxy','price_basis':config['execution']['price_basis'],
            'money_unit':'TWD_e4','arms':arms,'accounts':arms,'coverage':coverage,
            'ai_outcomes':counts,'opportunity_outcomes':[dict(o,outcome=json.loads(o['outcome_json'])) for o in outcomes],
            'decisions':[dict(d,quant=json.loads(d['quant_json']),ai=json.loads(d['ai_json'])) for d in decisions],
            'research_time_basis':'system_elapsed_only_human_research_time_not_measured','research_elapsed_ms':sum(b['research_elapsed_ms'] for b in research),
            'ai_effectiveness':{'claim':'not_established','reasons':reasons,'message':'目前僅供描述與學習，不宣稱 AI 有效或可獲利。'},
            'feature_recommendations':[
                {'action':'retain','feature':'Shioaji 人工流程與安全控管','dependencies':['web/control_panel_app.py','app/cli.py'],'evidence':'本研究不接實盤，保留人工決策；非本次新增成果。'},
                {'action':'consolidate','feature':'合格雷達／回放／紙帳','dependencies':['app/pipeline/swing_research.py','app/paper/portfolio.py'],'evidence':'共同封存設定、來源及帳務；舊單股輸出僅供探索，不能相加當成三組組合。'},
                {'action':'optional_pending_validation','feature':'其他策略與盤中工具','dependencies':['strategies/multi','app/alerts'],'evidence':{'experiment_id':experiment_id,'decision_count':len(decisions),'comparison_status':'pending_validation' if reasons else 'descriptive_only'},'message':'沒有足夠依賴與比較證據前不刪除。'}]}
