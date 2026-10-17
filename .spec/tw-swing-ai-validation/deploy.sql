-- tw-swing-ai-validation — 台股新手 AI 輔助波段研究與績效驗證
-- DB: SQLite >= 3.37，需 JSON1；此檔是設計產物，尚未套用。
-- 不變更／回填現有快取，不依賴 mutable research_evidence 或真實委託／成交表。
-- 遷移：日後以 Alembic revision 接續實際 head 整合；create_all 不會補約束／觸發器。
-- 執行端須在交易外先設 PRAGMA foreign_keys=ON，確認回讀為 1，再啟動單一遷移交易。
-- runtime 亦須在 app/db/session.py 的每個新 DBAPI 連線 hook 啟用並檢查 foreign_keys；
-- runtime 同時須設定 recursive_triggers=ON，以免 REPLACE 繞過不可變 DELETE trigger。
-- 此處的 PRAGMA 只保護執行本檔的連線，不能取代 runtime hook。不得在已開始的交易中才開啟。
-- 本設計以 STRICT 表拒絕浮點金額；所有 *_e4 均為新台幣 1/10000 元整數，10,000 元=100000000。
-- 所有 *_at_us 均為 UTC Unix 微秒整數；交易日為台北市場 YYYY-MM-DD，須由交易日曆驗證。
-- JSON 均為 canonical JSON、含 schema_version；SHA-256 由服務以固定排序／編碼計算並驗證。
-- SQLite 無原生 SHA-256，此層只能限制長度，不能證明 hash 或文章內容真實。
-- 不使用 IF NOT EXISTS 掩蓋部分部署：由 Alembic 交易與 revision 狀態控制一次性部署。
-- 所有研究事實僅可追加；錯誤更正用新版本／新事件，不改寫舊紀錄。
-- 應用契約：BEGIN IMMEDIATE；同一 transaction 讀最新事件、驗證策略／現金／持倉／交割、
-- 寫事件及任何重建快取，再提交。不可先寫快取後補事件；本設計本身沒有可變帳戶餘額表。
-- event_key／run_key 等唯一鍵碰撞時讀回並比對 canonical payload hash；相同才回傳既有結果，
-- 不同則報 idempotency_conflict；禁止 INSERT OR IGNORE / REPLACE 默默吞掉衝突。
-- 重啟依事件 seq 重建，未提交交易整筆回滾；已提交事件不得重送成本、交割或公司行動。
-- 修復／隔離僅阻擋後續新封存，既有批次保留原證據並另追加 incomplete 實驗事件，不重寫。
-- 來源 JSON 的業務欄位／單位、整數股數、公司行動分配及成本捨入由具版本的同一驗證器處理。
-- 持倉與 obligation/order JSON 的逐項加總必須等於外層整數總額；不以 JSON 有效性取代核對。
-- 只有 verified 來源可作合格輸入；舊快取一律先重驗／隔離，不得缺資料時 fallback。
-- 所有新選取（含 AI 證據）須依 source_key／supersedes 鏈挑當時合格版本，並檢查最新
-- applied 修復／隔離／解除紀錄；不能只 WHERE quality=verified 而繼續使用已失效舊版本。
-- 歷史模式只表示依公開時間回放，不能證明模型不知未來；不得宣稱 AI 有效性。

PRAGMA foreign_keys = ON;
PRAGMA recursive_triggers = ON;

-- ===== 1. 建表 =====
-- @sql:deploy.sql#swing_source_versions
-- 原始／正規化資料、文章原文或必要摘錄、URL、解析器版號均存 payload_json。
-- source_key 是同一外部事實的穩定鍵；version_key 包含內容、來源時間與解析器版號。
CREATE TABLE swing_source_versions (
    id INTEGER PRIMARY KEY,
    version_key TEXT NOT NULL UNIQUE CHECK(length(version_key)=64),
    source TEXT NOT NULL CHECK(length(source)>0),
    kind TEXT NOT NULL CHECK(kind IN ('monthly_revenue','financial_summary','news','market_bar','calendar','corporate_action','universe','ai_evidence')),
    source_key TEXT NOT NULL CHECK(length(source_key)>0),
    symbol TEXT,
    data_period TEXT NOT NULL,
    public_at_us INTEGER,
    obtained_at_us INTEGER NOT NULL CHECK(obtained_at_us>0),
    revised_at_us INTEGER,
    verified_at_us INTEGER,
    quality TEXT NOT NULL CHECK(quality IN ('verified','unknown_publication','quarantined','unverified')),
    supersedes_id INTEGER REFERENCES swing_source_versions(id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    created_at_us INTEGER NOT NULL CHECK(created_at_us>=obtained_at_us),
    CHECK(supersedes_id IS NULL OR supersedes_id<>id),
    CHECK(public_at_us IS NULL OR public_at_us>0),
    CHECK(revised_at_us IS NULL OR (revised_at_us>0 AND (public_at_us IS NULL OR revised_at_us>=public_at_us))),
    CHECK(verified_at_us IS NULL OR verified_at_us>=obtained_at_us),
    CHECK(quality<>'verified' OR (public_at_us IS NOT NULL AND verified_at_us IS NOT NULL)),
    CHECK(quality<>'unknown_publication' OR public_at_us IS NULL)
) STRICT;

-- @sql:deploy.sql#swing_data_repairs
-- 預覽與執行各追加一筆；原表定位／前後值／理由／受影響範圍皆在 audit_json。
-- 修復 legacy 快取時，同一交易追加 applied 紀錄；失敗另記 failed，不修改 preview。
CREATE TABLE swing_data_repairs (
    id INTEGER PRIMARY KEY,
    repair_key TEXT NOT NULL UNIQUE,
    preview_id INTEGER REFERENCES swing_data_repairs(id) ON DELETE RESTRICT,
    stage TEXT NOT NULL CHECK(stage IN ('preview','applied','failed')),
    action TEXT NOT NULL CHECK(action IN ('repair','quarantine','release')),
    old_version_id INTEGER REFERENCES swing_source_versions(id) ON DELETE RESTRICT,
    new_version_id INTEGER REFERENCES swing_source_versions(id) ON DELETE RESTRICT,
    actor TEXT NOT NULL CHECK(length(actor)>0),
    audit_json TEXT NOT NULL CHECK(json_valid(audit_json) AND json_type(audit_json)='object'),
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    created_at_us INTEGER NOT NULL CHECK(created_at_us>0),
    CHECK((stage='preview' AND preview_id IS NULL) OR (stage<>'preview' AND preview_id IS NOT NULL)),
    CHECK(new_version_id IS NULL OR old_version_id IS NULL OR new_version_id<>old_version_id)
) STRICT;

-- @sql:deploy.sql#swing_experiments
-- config_json 固定 ETF／股票池／期間／策略版與參數／持有及風險規則／費稅／滑價／交割／
-- 價格還原和公司行動政策／暖機與缺值政策／觀察期／版本與研究時間計量口徑。
-- 核准綁定 config_hash；改設定必須新 experiment，不可沿用先前人工核准。
CREATE TABLE swing_experiments (
    id INTEGER PRIMARY KEY,
    experiment_key TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK(mode IN ('prospective','historical_exploratory')),
    config_status TEXT NOT NULL CHECK(config_status IN ('confirmed','assumed')),
    config_json TEXT NOT NULL CHECK(json_valid(config_json) AND json_type(config_json)='object'),
    config_hash TEXT NOT NULL CHECK(length(config_hash)=64),
    code_version TEXT NOT NULL CHECK(length(code_version)>0),
    confirmed_by TEXT,
    confirmed_at_us INTEGER,
    created_at_us INTEGER NOT NULL CHECK(created_at_us>0),
    CHECK(config_status<>'confirmed' OR (confirmed_by IS NOT NULL AND length(confirmed_by)>0 AND confirmed_at_us IS NOT NULL)),
    CHECK(mode<>'prospective' OR config_status='confirmed'),
    CHECK(confirmed_at_us IS NULL OR confirmed_at_us<=created_at_us)
) STRICT;

-- @sql:deploy.sql#swing_input_batches
-- 服務先配置明確正整數 batch ID（不可依賴 insert 時自動配置），先插入 source links、
-- 最後插入 batch 封存（同一 deferred-FK transaction）；未封存 links 不可跨 transaction 暫存。
-- manifest_json 是完整逐筆封存值，包含來源 ID/hash、行情、日曆、公司行動、股票池及暖機區間，
-- 不只保存查詢條件或可變快取 ID。input_hash 包括排序後 links／manifest／策略設定。
CREATE TABLE swing_input_batches (
    id INTEGER PRIMARY KEY CHECK(id>0),
    experiment_id INTEGER NOT NULL REFERENCES swing_experiments(id) ON DELETE RESTRICT,
    run_key TEXT NOT NULL,
    cutoff_at_us INTEGER NOT NULL CHECK(cutoff_at_us>0),
    decision_at_us INTEGER NOT NULL CHECK(decision_at_us>=cutoff_at_us),
    coverage_status TEXT NOT NULL CHECK(coverage_status IN ('complete','incomplete')),
    coverage_json TEXT NOT NULL CHECK(json_valid(coverage_json) AND json_type(coverage_json)='object'),
    source_count INTEGER NOT NULL CHECK(source_count>=0),
    manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'),
    input_hash TEXT NOT NULL CHECK(length(input_hash)=64),
    research_elapsed_ms INTEGER NOT NULL CHECK(research_elapsed_ms>=0),
    created_at_us INTEGER NOT NULL CHECK(created_at_us>=decision_at_us),
    UNIQUE(experiment_id,run_key),
    UNIQUE(id,experiment_id)
) STRICT;

-- @sql:deploy.sql#swing_batch_sources
-- FK 延遲到 commit，允許先寫來源集合、再一次封存 batch；batch 存在後拒絕再加 links。
-- 僅供診斷的 unknown/quarantined 來源可附於 manifest 的 excluded 清單，不能放此合格集合。
CREATE TABLE swing_batch_sources (
    batch_id INTEGER NOT NULL,
    experiment_id INTEGER NOT NULL,
    source_version_id INTEGER NOT NULL REFERENCES swing_source_versions(id) ON DELETE RESTRICT,
    purpose TEXT NOT NULL CHECK(purpose IN ('strategy','execution','valuation','calendar','corporate_action','universe')),
    PRIMARY KEY(batch_id,source_version_id,purpose),
    FOREIGN KEY(batch_id,experiment_id) REFERENCES swing_input_batches(id,experiment_id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
) STRICT;

-- @sql:deploy.sql#swing_candidate_decisions
-- 每批候選只保存一套 B/C 共用前置量化排序、規則與下單意圖；C 不產生替補排名。
-- ai_json 保存 prompt/model/provider、原始輸出、判斷理由、各來源查證與失敗明細。
-- AI evidence bundle 的 payload 需封存每篇原文/摘錄及各自公布／取得／查證時間，不信任網址。
-- bundle.public_at_us 為支持結論之事實的最晚公開時間，obtained/verified 為本次取得及查證；
-- bundle 自身生成時間不得冒充文章公布時間。所有逐篇時間亦由服務驗證。
-- 封存 batch 與該批所有候選 decisions 必須同一 transaction：失敗候選也立即存 unavailable。
-- 恢復後不得補寫／替換舊批次決策，僅可處理新批次；服務按固定執行視窗拒絕過期 decision。
CREATE TABLE swing_candidate_decisions (
    id INTEGER PRIMARY KEY,
    experiment_id INTEGER NOT NULL,
    batch_id INTEGER NOT NULL,
    symbol TEXT NOT NULL CHECK(length(symbol)>0),
    quant_rank INTEGER NOT NULL CHECK(quant_rank>0),
    quant_json TEXT NOT NULL CHECK(json_valid(quant_json) AND json_type(quant_json)='object'),
    ai_outcome TEXT NOT NULL CHECK(ai_outcome IN ('allow','veto','unavailable','evidence_rejected','not_requested')),
    evidence_version_id INTEGER REFERENCES swing_source_versions(id) ON DELETE RESTRICT,
    ai_json TEXT NOT NULL CHECK(json_valid(ai_json) AND json_type(ai_json)='object'),
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    created_at_us INTEGER NOT NULL CHECK(created_at_us>0),
    UNIQUE(batch_id,symbol),
    UNIQUE(batch_id,quant_rank),
    UNIQUE(id,experiment_id,batch_id),
    FOREIGN KEY(batch_id,experiment_id) REFERENCES swing_input_batches(id,experiment_id) ON DELETE RESTRICT,
    CHECK(ai_outcome NOT IN ('allow','veto') OR evidence_version_id IS NOT NULL)
) STRICT;

-- @sql:deploy.sql#swing_paper_accounts
-- 一組一個帳戶，所有股票共享資金；同一 transaction 建立且驗證恰有 A/B/C 各一個。
CREATE TABLE swing_paper_accounts (
    id INTEGER PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES swing_experiments(id) ON DELETE RESTRICT,
    arm TEXT NOT NULL CHECK(arm IN ('A','B','C')),
    initial_cash_e4 INTEGER NOT NULL CHECK(initial_cash_e4=100000000),
    stop_equity_e4 INTEGER NOT NULL CHECK(stop_equity_e4=80000000),
    created_at_us INTEGER NOT NULL CHECK(created_at_us>0),
    UNIQUE(experiment_id,arm),
    UNIQUE(id,experiment_id)
) STRICT;

-- @sql:deploy.sql#swing_ledger_events
-- 追加式事件是帳戶狀態唯一事實來源；seq 及 prev_state_hash 作 CAS，所有股共享此鏈。
-- batch_id 為當次執行／估值輸入，decision_batch_id 為原收盤決策輸入，兩者允許不同。
-- 次日行情另封新批，不能加入原決策 batch；服務驗證下個合格交易時段及欄位可得時間，
-- 日線近似僅用當時可得開盤資訊，不能使用之後全日成交量判斷開盤流動性。
-- settled_cash 是已交割現金；reserved 是未成交預留；payable 是已成交待扣款；
-- receivable 是待收款。可用現金=settled-reserved-payable，未交割賣款不得提前挪用。
-- 淨值=settled+receivable-payable+market_value，reserved 不再次扣除，成本須已入付款／現金。
-- positions_json: 每股整數持股、成本及可賣股數；obligations_json: 各待交割款／股日期及事件鍵；
-- orders_json: 預留／取消／部分成交意圖。服務須逐項核對加總、T+2、無借券及 no short。
-- valuation 及 corporate_action 亦追加；同一公司行動 source ID＋account 的事件鍵唯一防重。
CREATE TABLE swing_ledger_events (
    id INTEGER PRIMARY KEY,
    experiment_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    seq INTEGER NOT NULL CHECK(seq>0),
    event_key TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK(event_kind IN ('opening','reserve','cancel','buy','sell','reject','settlement','corporate_action','valuation','risk_breach','pause','human_resume')),
    batch_id INTEGER,
    decision_id INTEGER,
    decision_batch_id INTEGER,
    symbol TEXT,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
    price_e4 INTEGER NOT NULL DEFAULT 0 CHECK(price_e4>=0),
    fee_e4 INTEGER NOT NULL DEFAULT 0 CHECK(fee_e4>=0),
    tax_e4 INTEGER NOT NULL DEFAULT 0 CHECK(tax_e4>=0),
    trade_date TEXT,
    settlement_date TEXT,
    effective_at_us INTEGER NOT NULL CHECK(effective_at_us>0),
    settled_cash_e4 INTEGER NOT NULL CHECK(settled_cash_e4>=0),
    reserved_cash_e4 INTEGER NOT NULL CHECK(reserved_cash_e4>=0),
    receivable_e4 INTEGER NOT NULL CHECK(receivable_e4>=0),
    payable_e4 INTEGER NOT NULL CHECK(payable_e4>=0),
    market_value_e4 INTEGER NOT NULL CHECK(market_value_e4>=0),
    equity_e4 INTEGER NOT NULL,
    risk_paused INTEGER NOT NULL CHECK(risk_paused IN (0,1)),
    ai_blocked INTEGER NOT NULL CHECK(ai_blocked IN (0,1)),
    positions_json TEXT NOT NULL CHECK(json_valid(positions_json) AND json_type(positions_json)='object'),
    obligations_json TEXT NOT NULL CHECK(json_valid(obligations_json) AND json_type(obligations_json)='array'),
    orders_json TEXT NOT NULL CHECK(json_valid(orders_json) AND json_type(orders_json)='array'),
    actor TEXT,
    detail_json TEXT NOT NULL CHECK(json_valid(detail_json) AND json_type(detail_json)='object'),
    prev_state_hash TEXT CHECK(prev_state_hash IS NULL OR length(prev_state_hash)=64),
    state_hash TEXT NOT NULL CHECK(length(state_hash)=64),
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    created_at_us INTEGER NOT NULL CHECK(created_at_us>=effective_at_us),
    UNIQUE(account_id,seq),
    UNIQUE(account_id,event_key),
    FOREIGN KEY(account_id,experiment_id) REFERENCES swing_paper_accounts(id,experiment_id) ON DELETE RESTRICT,
    FOREIGN KEY(batch_id,experiment_id) REFERENCES swing_input_batches(id,experiment_id) ON DELETE RESTRICT,
    FOREIGN KEY(decision_id,experiment_id,decision_batch_id) REFERENCES swing_candidate_decisions(id,experiment_id,batch_id) ON DELETE RESTRICT,
    CHECK(reserved_cash_e4+payable_e4<=settled_cash_e4),
    CHECK(equity_e4=settled_cash_e4+receivable_e4-payable_e4+market_value_e4),
    CHECK((decision_id IS NULL AND decision_batch_id IS NULL) OR (decision_id IS NOT NULL AND decision_batch_id IS NOT NULL)),
    CHECK(event_kind NOT IN ('reserve','buy','sell','reject','valuation','corporate_action') OR batch_id IS NOT NULL),
    CHECK(event_kind NOT IN ('buy','sell') OR (quantity>0 AND price_e4>0 AND symbol IS NOT NULL AND length(symbol)>0 AND trade_date IS NOT NULL AND settlement_date IS NOT NULL)),
    CHECK(event_kind<>'buy' OR (risk_paused=0 AND ai_blocked=0)),
    CHECK(event_kind<>'human_resume' OR (actor IS NOT NULL AND length(actor)>0 AND risk_paused=0)),
    CHECK((seq=1 AND event_kind='opening' AND prev_state_hash IS NULL) OR (seq>1 AND event_kind<>'opening' AND prev_state_hash IS NOT NULL))
) STRICT;

-- @sql:deploy.sql#swing_opportunity_outcomes
-- 固定觀察期於 config 凍結；不能只挑事後有利的 AI 排除結果。缺資料也追加 missing 結果。
CREATE TABLE swing_opportunity_outcomes (
    id INTEGER PRIMARY KEY,
    experiment_id INTEGER NOT NULL,
    decision_id INTEGER NOT NULL,
    decision_batch_id INTEGER NOT NULL,
    outcome_batch_id INTEGER NOT NULL,
    horizon_sessions INTEGER NOT NULL CHECK(horizon_sessions>0),
    status TEXT NOT NULL CHECK(status IN ('complete','missing')),
    outcome_json TEXT NOT NULL CHECK(json_valid(outcome_json) AND json_type(outcome_json)='object'),
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    created_at_us INTEGER NOT NULL CHECK(created_at_us>0),
    UNIQUE(decision_id,horizon_sessions),
    FOREIGN KEY(decision_id,experiment_id,decision_batch_id) REFERENCES swing_candidate_decisions(id,experiment_id,batch_id) ON DELETE RESTRICT,
    FOREIGN KEY(outcome_batch_id,experiment_id) REFERENCES swing_input_batches(id,experiment_id) ON DELETE RESTRICT
) STRICT;

-- @sql:deploy.sql#swing_experiment_events
-- 研究啟動／覆蓋／AI 失敗與人工確認留痕，無可變 status 隱藏失敗期間。
-- running 之前服務檢查完整設定與三帳戶；結案報表依事件及逐批 coverage 判定有效性。
CREATE TABLE swing_experiment_events (
    id INTEGER PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES swing_experiments(id) ON DELETE RESTRICT,
    event_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('started','incomplete','completed','human_ack','aborted')),
    actor TEXT,
    detail_json TEXT NOT NULL CHECK(json_valid(detail_json) AND json_type(detail_json)='object'),
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    created_at_us INTEGER NOT NULL CHECK(created_at_us>0),
    UNIQUE(experiment_id,event_key),
    CHECK(kind<>'human_ack' OR (actor IS NOT NULL AND length(actor)>0))
) STRICT;

-- ===== 2. 索引 =====
CREATE INDEX ix_swing_source_versions_pit ON swing_source_versions(kind,source_key,public_at_us,obtained_at_us);
CREATE INDEX ix_swing_source_versions_symbol_period ON swing_source_versions(symbol,kind,data_period);
CREATE INDEX ix_swing_source_versions_supersedes ON swing_source_versions(supersedes_id);
CREATE INDEX ix_swing_data_repairs_preview ON swing_data_repairs(preview_id,stage);
CREATE UNIQUE INDEX uq_swing_data_repairs_applied ON swing_data_repairs(preview_id) WHERE stage='applied';
CREATE INDEX ix_swing_data_repairs_old ON swing_data_repairs(old_version_id);
CREATE INDEX ix_swing_data_repairs_new ON swing_data_repairs(new_version_id);
CREATE INDEX ix_swing_input_batches_cutoff ON swing_input_batches(experiment_id,cutoff_at_us);
CREATE INDEX ix_swing_batch_sources_source ON swing_batch_sources(source_version_id);
CREATE INDEX ix_swing_candidate_decisions_evidence ON swing_candidate_decisions(evidence_version_id);
CREATE INDEX ix_swing_ledger_events_batch ON swing_ledger_events(batch_id,experiment_id);
CREATE INDEX ix_swing_ledger_events_decision ON swing_ledger_events(decision_id,experiment_id,decision_batch_id);
CREATE INDEX ix_swing_ledger_events_effective ON swing_ledger_events(account_id,effective_at_us,seq);
CREATE INDEX ix_swing_opportunity_outcomes_batch ON swing_opportunity_outcomes(outcome_batch_id,experiment_id);
CREATE INDEX ix_swing_experiment_events_time ON swing_experiment_events(experiment_id,created_at_us);

-- ===== 3. 一致性與不可變觸發器 =====
CREATE TRIGGER swing_source_revision_guard BEFORE INSERT ON swing_source_versions
WHEN NEW.supersedes_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM swing_source_versions s WHERE s.id=NEW.supersedes_id
        AND s.source=NEW.source AND s.kind=NEW.kind AND s.source_key=NEW.source_key
    ) THEN RAISE(ABORT,'source_revision_identity_mismatch') END;
END;

CREATE TRIGGER swing_repair_preview_guard BEFORE INSERT ON swing_data_repairs
WHEN NEW.preview_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM swing_data_repairs p WHERE p.id=NEW.preview_id AND p.stage='preview'
        AND p.action=NEW.action AND p.old_version_id IS NEW.old_version_id
    ) THEN RAISE(ABORT,'repair_preview_mismatch') END;
END;

CREATE TRIGGER swing_batch_link_guard BEFORE INSERT ON swing_batch_sources
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM swing_input_batches WHERE id=NEW.batch_id)
        THEN RAISE(ABORT,'batch_already_frozen') END;
END;

CREATE TRIGGER swing_batch_freeze_guard BEFORE INSERT ON swing_input_batches
BEGIN
    SELECT CASE WHEN NEW.source_count<>(SELECT count(*) FROM swing_batch_sources WHERE batch_id=NEW.id AND experiment_id=NEW.experiment_id)
        THEN RAISE(ABORT,'source_count_mismatch') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM swing_batch_sources l JOIN swing_source_versions s ON s.id=l.source_version_id
        WHERE l.batch_id=NEW.id AND (l.experiment_id<>NEW.experiment_id OR s.quality<>'verified'
            OR s.public_at_us IS NULL OR s.public_at_us>NEW.cutoff_at_us
            OR s.revised_at_us>NEW.cutoff_at_us
            OR (SELECT r.action FROM swing_data_repairs r WHERE r.old_version_id=s.id
                AND r.stage='applied' ORDER BY r.created_at_us DESC,r.id DESC LIMIT 1) IN ('repair','quarantine')
            OR (EXISTS (SELECT 1 FROM swing_experiments e WHERE e.id=NEW.experiment_id AND e.mode='prospective')
                AND (s.obtained_at_us>NEW.cutoff_at_us OR s.verified_at_us>NEW.decision_at_us)))
    ) THEN RAISE(ABORT,'ineligible_point_in_time_source') END;
END;

CREATE TRIGGER swing_decision_evidence_guard BEFORE INSERT ON swing_candidate_decisions
WHEN NEW.ai_outcome IN ('allow','veto')
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM swing_source_versions s JOIN swing_input_batches b ON b.id=NEW.batch_id
        JOIN swing_experiments e ON e.id=b.experiment_id
        WHERE s.id=NEW.evidence_version_id AND s.kind='ai_evidence' AND s.quality='verified'
        AND s.public_at_us<=b.cutoff_at_us AND (s.revised_at_us IS NULL OR s.revised_at_us<=b.cutoff_at_us)
        AND COALESCE((SELECT r.action FROM swing_data_repairs r WHERE r.old_version_id=s.id
            AND r.stage='applied' ORDER BY r.created_at_us DESC,r.id DESC LIMIT 1),'release') NOT IN ('repair','quarantine')
        AND s.verified_at_us<=b.decision_at_us AND NEW.created_at_us>=b.decision_at_us
        AND (e.mode='historical_exploratory' OR s.obtained_at_us<=b.decision_at_us)
    ) THEN RAISE(ABORT,'ineligible_ai_evidence') END;
END;

CREATE TRIGGER swing_ledger_chain_guard BEFORE INSERT ON swing_ledger_events
BEGIN
    SELECT CASE WHEN (SELECT count(*) FROM swing_paper_accounts WHERE experiment_id=NEW.experiment_id)<>3
        THEN RAISE(ABORT,'three_accounts_required') END;
    SELECT CASE WHEN NEW.seq<>COALESCE((SELECT max(seq)+1 FROM swing_ledger_events WHERE account_id=NEW.account_id),1)
        THEN RAISE(ABORT,'ledger_sequence_conflict') END;
    SELECT CASE WHEN NEW.seq>1 AND NOT EXISTS (
        SELECT 1 FROM swing_ledger_events p WHERE p.account_id=NEW.account_id AND p.seq=NEW.seq-1
        AND p.state_hash=NEW.prev_state_hash AND p.effective_at_us<=NEW.effective_at_us
    ) THEN RAISE(ABORT,'ledger_previous_state_mismatch') END;
    SELECT CASE WHEN NEW.seq=1 AND (
        NEW.settled_cash_e4<>100000000 OR NEW.reserved_cash_e4<>0 OR NEW.receivable_e4<>0
        OR NEW.payable_e4<>0 OR NEW.market_value_e4<>0 OR NEW.risk_paused<>0 OR NEW.ai_blocked<>0
        OR NEW.positions_json<>'{}' OR NEW.obligations_json<>'[]' OR NEW.orders_json<>'[]'
    ) THEN RAISE(ABORT,'invalid_opening_balance') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM swing_paper_accounts a WHERE a.id=NEW.account_id AND a.arm='A')
        AND (NEW.risk_paused<>0 OR NEW.ai_blocked<>0)
        THEN RAISE(ABORT,'benchmark_must_continue') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM swing_paper_accounts a WHERE a.id=NEW.account_id AND a.arm IN ('B','C'))
        AND NEW.equity_e4<=80000000 AND NEW.risk_paused<>1
        THEN RAISE(ABORT,'loss_budget_requires_pause') END;
    SELECT CASE WHEN NEW.seq>1 AND NEW.risk_paused=0 AND EXISTS (
        SELECT 1 FROM swing_ledger_events p WHERE p.account_id=NEW.account_id AND p.seq=NEW.seq-1 AND p.risk_paused=1
    ) AND NEW.event_kind<>'human_resume' THEN RAISE(ABORT,'human_resume_required') END;
    SELECT CASE WHEN NEW.batch_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM swing_input_batches b WHERE b.id=NEW.batch_id
        AND b.cutoff_at_us<=NEW.effective_at_us
    ) THEN RAISE(ABORT,'execution_input_from_future') END;
    SELECT CASE WHEN NEW.decision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM swing_candidate_decisions d JOIN swing_input_batches b ON b.id=d.batch_id
        JOIN swing_experiments e ON e.id=b.experiment_id
        WHERE d.id=NEW.decision_id AND d.symbol=NEW.symbol
        AND (CASE WHEN e.mode='historical_exploratory' THEN b.cutoff_at_us ELSE b.decision_at_us END)<=NEW.effective_at_us
        AND (NEW.event_kind NOT IN ('buy','sell') OR
            (CASE WHEN e.mode='historical_exploratory' THEN b.cutoff_at_us ELSE b.decision_at_us END)<NEW.effective_at_us)
    ) THEN RAISE(ABORT,'decision_time_or_symbol_mismatch') END;
    SELECT CASE WHEN NEW.event_kind IN ('buy','reserve') AND EXISTS (
        SELECT 1 FROM swing_paper_accounts a WHERE a.id=NEW.account_id AND a.arm IN ('B','C')
    ) AND NEW.decision_id IS NULL THEN RAISE(ABORT,'swing_entry_requires_common_decision') END;
    SELECT CASE WHEN NEW.event_kind IN ('buy','reserve') AND EXISTS (
        SELECT 1 FROM swing_paper_accounts a WHERE a.id=NEW.account_id AND a.arm='C'
    ) AND (NEW.ai_blocked=1 OR NEW.decision_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM swing_candidate_decisions d WHERE d.id=NEW.decision_id AND d.ai_outcome='allow'
        AND d.symbol=NEW.symbol
    )) THEN RAISE(ABORT,'ai_entry_not_allowed') END;
    SELECT CASE WHEN NEW.event_kind IN ('buy','reserve') AND EXISTS (
        SELECT 1 FROM swing_input_batches b JOIN swing_experiments e ON e.id=b.experiment_id
        WHERE b.id=NEW.batch_id AND e.mode='prospective' AND b.coverage_status<>'complete'
    ) THEN RAISE(ABORT,'incomplete_batch_cannot_add_position') END;
    SELECT CASE WHEN NEW.event_kind='reserve' AND NEW.risk_paused=1
        THEN RAISE(ABORT,'paused_account_cannot_reserve') END;
END;

-- below triggers 禁止 UPDATE／DELETE，包含 REPLACE 的刪除路徑；runtime 禁用 REPLACE 並啟用
-- recursive_triggers=ON，因 SQLite 對 REPLACE 觸發 delete trigger 的預設行為不同。
-- 不可變保護不等於抵抗具有任意 DDL 權限的管理者；檔案寫入權限／備份仍由部署管理。
CREATE TRIGGER swing_source_versions_no_update BEFORE UPDATE ON swing_source_versions
BEGIN SELECT RAISE(ABORT,'immutable_swing_source_versions'); END;
CREATE TRIGGER swing_source_versions_no_delete BEFORE DELETE ON swing_source_versions
BEGIN SELECT RAISE(ABORT,'immutable_swing_source_versions'); END;
CREATE TRIGGER swing_data_repairs_no_update BEFORE UPDATE ON swing_data_repairs
BEGIN SELECT RAISE(ABORT,'immutable_swing_data_repairs'); END;
CREATE TRIGGER swing_data_repairs_no_delete BEFORE DELETE ON swing_data_repairs
BEGIN SELECT RAISE(ABORT,'immutable_swing_data_repairs'); END;
CREATE TRIGGER swing_experiments_no_update BEFORE UPDATE ON swing_experiments
BEGIN SELECT RAISE(ABORT,'immutable_swing_experiments'); END;
CREATE TRIGGER swing_experiments_no_delete BEFORE DELETE ON swing_experiments
BEGIN SELECT RAISE(ABORT,'immutable_swing_experiments'); END;
CREATE TRIGGER swing_input_batches_no_update BEFORE UPDATE ON swing_input_batches
BEGIN SELECT RAISE(ABORT,'immutable_swing_input_batches'); END;
CREATE TRIGGER swing_input_batches_no_delete BEFORE DELETE ON swing_input_batches
BEGIN SELECT RAISE(ABORT,'immutable_swing_input_batches'); END;
CREATE TRIGGER swing_batch_sources_no_update BEFORE UPDATE ON swing_batch_sources
BEGIN SELECT RAISE(ABORT,'immutable_swing_batch_sources'); END;
CREATE TRIGGER swing_batch_sources_no_delete BEFORE DELETE ON swing_batch_sources
BEGIN SELECT RAISE(ABORT,'immutable_swing_batch_sources'); END;
CREATE TRIGGER swing_candidate_decisions_no_update BEFORE UPDATE ON swing_candidate_decisions
BEGIN SELECT RAISE(ABORT,'immutable_swing_candidate_decisions'); END;
CREATE TRIGGER swing_candidate_decisions_no_delete BEFORE DELETE ON swing_candidate_decisions
BEGIN SELECT RAISE(ABORT,'immutable_swing_candidate_decisions'); END;
CREATE TRIGGER swing_paper_accounts_no_update BEFORE UPDATE ON swing_paper_accounts
BEGIN SELECT RAISE(ABORT,'immutable_swing_paper_accounts'); END;
CREATE TRIGGER swing_paper_accounts_no_delete BEFORE DELETE ON swing_paper_accounts
BEGIN SELECT RAISE(ABORT,'immutable_swing_paper_accounts'); END;
CREATE TRIGGER swing_ledger_events_no_update BEFORE UPDATE ON swing_ledger_events
BEGIN SELECT RAISE(ABORT,'immutable_swing_ledger_events'); END;
CREATE TRIGGER swing_ledger_events_no_delete BEFORE DELETE ON swing_ledger_events
BEGIN SELECT RAISE(ABORT,'immutable_swing_ledger_events'); END;
CREATE TRIGGER swing_opportunity_outcomes_no_update BEFORE UPDATE ON swing_opportunity_outcomes
BEGIN SELECT RAISE(ABORT,'immutable_swing_opportunity_outcomes'); END;
CREATE TRIGGER swing_opportunity_outcomes_no_delete BEFORE DELETE ON swing_opportunity_outcomes
BEGIN SELECT RAISE(ABORT,'immutable_swing_opportunity_outcomes'); END;
CREATE TRIGGER swing_experiment_events_no_update BEFORE UPDATE ON swing_experiment_events
BEGIN SELECT RAISE(ABORT,'immutable_swing_experiment_events'); END;
CREATE TRIGGER swing_experiment_events_no_delete BEFORE DELETE ON swing_experiment_events
BEGIN SELECT RAISE(ABORT,'immutable_swing_experiment_events'); END;

-- ===== Rollback（全部註解；僅確認新研究資料可移除並備份後，由 Alembic 反向交易執行）=====
-- 不碰既有行情／基本面／研究／實盤表。以下 drop table 會一併移除該表的索引／觸發器。
-- foreign_keys 保持 ON；自我參照修訂／preview 鏈須在同一 rollback transaction 延後檢查，
-- 否則 DROP TABLE 的隱含 DELETE 會受 RESTRICT 阻擋。以子表到父表移除，先移除 DELETE triggers。
-- 已由 Alembic 開始交易時省略下列 BEGIN/COMMIT；defer_foreign_keys 僅作用於本次交易。
-- BEGIN IMMEDIATE;
-- PRAGMA defer_foreign_keys = ON;
-- DROP TRIGGER swing_experiment_events_no_delete;
-- DROP TRIGGER swing_opportunity_outcomes_no_delete;
-- DROP TRIGGER swing_ledger_events_no_delete;
-- DROP TRIGGER swing_paper_accounts_no_delete;
-- DROP TRIGGER swing_candidate_decisions_no_delete;
-- DROP TRIGGER swing_batch_sources_no_delete;
-- DROP TRIGGER swing_input_batches_no_delete;
-- DROP TRIGGER swing_experiments_no_delete;
-- DROP TRIGGER swing_data_repairs_no_delete;
-- DROP TRIGGER swing_source_versions_no_delete;
-- DROP TABLE swing_experiment_events;
-- DROP TABLE swing_opportunity_outcomes;
-- DROP TABLE swing_ledger_events;
-- DROP TABLE swing_paper_accounts;
-- DROP TABLE swing_candidate_decisions;
-- DROP TABLE swing_batch_sources;
-- DROP TABLE swing_input_batches;
-- DROP TABLE swing_experiments;
-- DROP TABLE swing_data_repairs;
-- DROP TABLE swing_source_versions;
-- COMMIT;
