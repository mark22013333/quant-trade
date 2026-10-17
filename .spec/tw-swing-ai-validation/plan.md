---
slug: tw-swing-ai-validation
name: 台股新手 AI 輔助波段研究與績效驗證
type: feature
verified_at_commit:
verified_at:
drift_policy: normal
---

# 台股新手 AI 輔助波段研究與績效驗證

> 整理資料解析與公告時間問題，確保研究使用當時可取得的資訊。
> 統一選股、回測與紙上交易的策略及成交假設。
> 規劃比較 ETF 買進持有、固定規則波段、波段加 AI 事件篩選三組紙上帳戶。
> 依比較證據，決定保留、整併或降為選用的功能。
> 沿用永豐 Shioaji，保留人工交易決策；本階段先建立規劃任務。
> 使用情境為收盤後研究、持有數週至數月，本金新台幣 10,000 元、整體可接受虧損 2,000 元。

## 目標與範圍        <!-- crew:goal owner=spec -->
- 服務收盤後研究、持有數週至數月的台股新手；以可重現的淨績效及研究時間衡量 AI 協助的價值。
- 已確認：沿用永豐 Shioaji，本金新台幣 10,000 元、整體可接受虧損 2,000 元，保留人工交易決策。
- 納入營收／財報解析、資訊可用時間、污染資料處理、策略與成交假設一致性，以及比較資料覆蓋。
- 比較 A：ETF 買進持有、B：固定規則波段、C：同波段加 AI 事件篩選；各自建立 10,000 元虛擬組合帳戶。
- 沿用 CLI、報表及 HTML/JS 控制台，集中呈現設定、資料品質、AI 證據、績效與功能取捨。
- 行為取捨見 D-2 至 D-11；ETF 標的、策略版本與參數、單筆風險及實際費率仍須於比較啟動前設定並確認。
- 範圍外：實盤自動下單、保證獲利、全面平台重寫、新策略庫擴張、未經依賴及績效證據支持的功能刪除。

## 驗收條件          <!-- crew:ac   owner=spec -->
- [ ] AC-1 設定介面與執行入口揭露 ETF、股票池、期間、策略／持有規則、單筆及組合限制、商品費率與滑價，缺漏或未確認時拒絕啟動合格比較且不繼承百萬本金或舊持有期｜例：S01 正常：完整確認後建立三組各 10,000 元帳戶；S02 例外：缺費率時列出缺項，不自動採最低手續費 20 元。
- [ ] AC-2 依來源語意解析營收年份與月份，以可得同口徑基期計算成長率；缺基期保留未知，評分移除年份誤讀與營收自除的固定加分｜例：S01 正常：date=2019-04-01、revenue_year=2019、revenue_month=3 對應 2019-03；S02 例外：沒有去年同期時不產生 2019% 或虛構 0% 年增率。
- [ ] AC-3 選股與回放僅使用截止時間前已公開且可追溯的資料版本；區分資料期間、公布、取得及修訂時間，無可靠公布時間者不參與基本面／事件評分｜例：S01 正常：截止前公開版本可使用；S02 例外：季底及來源月份標籤不作公告時間，截止後修訂不得回填歷史輸入。
- [ ] AC-4 提供污染資料辨識預覽、可重跑修復或隔離及前後摘要，保留來源與處理證據；既有錯誤資料不得混入合格實驗，封存輸入不因修復而改寫。
- [ ] AC-5 建立實驗及輸出結果時揭露行情、暖機期、股票池、ETF、交易日曆與公司行動覆蓋；缺必要資料拒絕合格比較，探索性結果明示不完整，不補造行情或以現有觀察池宣稱全市場驗證。
- [ ] AC-6 雷達、比較回測與紙上帳本使用同一策略版本及確認過的進出場／持有規則；主動停用策略移除投票權重，缺資料另列狀態並依策略必要性阻擋，兩者不混同。
- [ ] AC-7 收盤後訊號依下一合格交易時段撮合，三組共用估值與成交模型，依商品計算費稅、滑價及交割；拒絕成交須留痕，零股日線近似須明示｜例：S01 正常：有下一合格行情才撮合；S02 例外：不得以訊號日收盤成交、以成交後全日量決定開盤可成交，或缺行情仍記成功成交。
- [ ] AC-8 三組分別共享各自 10,000 元組合資金，逐筆核對現金、預留款、待交割與持股；同一實驗重跑或中斷續跑不重複記帳｜例：S01 正常：兩檔共用同組餘額；S02 例外：資金不足時拒絕第二筆，不為另一檔重新配置 10,000 元。
- [ ] AC-9 B/C 共用候選、排序、策略、期間、風險及執行設定，C 僅增加 AI 事件篩選；排除後保留現金、不補選替代股，記錄決策前機會及固定觀察期間後的結果。
- [ ] AC-10 AI 事件篩選保留可稽核原文或摘錄、來源、公布／查證／截止時間及決策版本；不支持結論、時間未知或晚於截止的內容不得列為合格證據｜例：S01 正常：已取得原文支持事件判斷；S02 例外：僅有格式正確網址或模型自報 confidence 不算已查證。
- [ ] AC-11 風險排除不得為湊名額重新入選，合格清單可為零；AI 失敗／證據拒收時 C 不新增部位並標不完整，既有退出風控及 B 組可繼續，恢復後不回填已錯過的 AI 決策｜例：S01 正常：三檔合格就只列三檔；S02 例外：全部排除或 AI 逾時時不補回風險股、不靜默當作無風險。
- [ ] AC-12 同期間報表列出三組淨報酬、相對 A 的差額、最大回撤、曝險、成本、交易次數、AI 排除結果及研究耗時；股利／拆分／除權息一致計入，不重複計算還原價格與配息，缺資料或樣本不足不得宣稱 AI 有效。
- [ ] AC-13 B/C 依確認的單筆及組合限制計算部位；含成本之帳戶淨值不高於 8,000 元即停止新增、既有持倉按退出規則管理，恢復需人工確認，A 保留買進持有並揭露門檻穿越｜例：S01 正常：淨值 8,001 元仍須通過單筆限制；S02 邊界：8,000 元停止新增且反彈不自動恢復，不因觸線自動清倉。
- [ ] AC-14 既有介面可查看設定缺項、資料／AI 降級、三組比較、決策理由及人工確認紀錄，報表區分歷史探索與前瞻觀察；本研究流程不呼叫實盤送單、不自動推進實盤資格。
- [ ] AC-15 提供依賴、重複職責及比較證據支持的功能保留／整併／選用建議；尚無證據者標待驗證，原有未提交變更不列為本任務新增成果。

## 決策紀錄          <!-- crew:dec  append-only -->
- D-18 [arch] 基本面與來源正規化留在 FinMind／同步層，研究查詢集中於新增 `@code:app/db/swing_repository.py`，雷達評分及實驗一律讀指定截止的可追溯版本；年份／月份按來源欄位解析，成長率由同口徑基期計算，公布時間未知不參與基本面／事件評分；修復先預覽，套用時核對原值雜湊、追加版本及稽核，相同處理鍵核對內容後回傳原結果，封存不變｜理由：把污染隔離在共同資料邊界｜否決：日期猜測、營收自除加分、缺資料回退舊表；另見 `@code:app/services/scoring.py#_fundamental_scores`。
- D-19 [arch] 新增 `@code:app/pipeline/swing_research.py` 統籌已核准設定與資料覆蓋，沿用 `@code:app/services/strategy_config.py#StrategyRunConfig`、`@code:strategies/multi/swing_system.py#MultiStrategySwingSystem.generate_signals` 及共同退出規則，讓合格雷達、回放與紙帳取得同版候選／進出場意圖；停用策略移除權重，必要資料缺漏阻擋，選用缺項依確認政策處理｜理由：消除多路策略及隱性預設差異｜否決：拼接單股結果、沿用五日／百萬本金預設，或雷達門檻／回饋／排序未納入凍結版本；舊入口保留相容並明示探索性，實際策略及參數仍須啟動前確認。
- D-20 [arch] 研究分固定輸入、外部研究、一次封存：短交易追加來源及 started 事件，保存 run_key、截止、固定期限、完整暫存輸入／候選與雜湊；關閉 session 後批次呼叫 AI、取得原文／摘錄並核對內容支持與公開時間，來源取得邊界限制 HTTPS／來源清單／每跳轉址與私網／逾時及大小；最後同交易寫 links→batch→全部決策，B/C 共用事前意圖及排序，C 僅否決、不替補｜理由：避免寫鎖跨外部 I/O 及事後改決策｜否決：先寫 pending 候選再更新、網址格式等同查證；逾時／拒收封存 unavailable／evidence_rejected，C 停新增，B／既有退出續行，晚回應丟棄，重啟依原暫存恢復且不補過期 AI／前瞻成交；見 `@code:app/services/subscription_research.py#CodexSubscriptionResearcher.analyze_batch`、`@code:app/pipeline/shortlist.py#ShortlistService.finalize`。
- D-21 [arch] 擴充 `@code:app/backtest/execution_model.py#ExecutionModelConfig` 作共同成交／估值政策，新增 `@code:app/paper/portfolio.py` 按交易日曆及事件時間推進：訊號後下一合格時段撮合，執行／估值另封批次並連回原決策；僅用撮合當時可得欄位與先前流動性，缺必要資料記拒絕，日線零股近似明示；商品別費稅、捨入、T+2、股利／拆分採同版政策｜理由：三組須有相同資訊與成本口徑｜否決：同收盤成交、以次日全量判斷開盤可成交、用曆日猜交割、還原價格又加配息；既有 `@code:backtest/backtest_engine.py#BacktestEngine.run_multi` 的合格研究入口改委派共同流程。
- D-22 [arch] 組合事件為唯一帳務事實，三組各共享一份本金；浮點限策略指標，金額從字串以 Decimal 依確認政策捨入後轉 e4 整數，股數整數；共同流程核對跨股資金、預留、待收付、可賣量與淨值，固定事件順序、成交排序及公司行動鍵；B/C 淨值≤8,000 元鎖住新增，反彈仍須人工恢復，A 僅記門檻穿越｜理由：資金競爭、退出及交割必須可重播｜否決：單股各領本金、未交割賣款先挪用、觸線清倉／自動恢復；落點 `@code:app/paper/portfolio.py`、`@code:app/db/swing_repository.py`。
- D-23 [arch] 遷移先檢查 SQLite≥3.37／JSON1，依實作時實際 Alembic head 接續 revision，保留使用者未提交的 0014；runtime、遷移與測試連線均在交易前啟用並回讀 foreign_keys／recursive_triggers，研究寫入採短 BEGIN IMMEDIATE，交易內配置 batch ID、先 links 後封存、核對 seq／前狀態／唯一鍵及內容雜湊，鎖衝突僅有限重試整筆交易｜理由：落實已驗證 DDL 的保護條件｜否決：create_all 代替遷移、只設單一連線 PRAGMA、忽略衝突；來源依版本去重、逐批處理與索引分頁，報表快取可重建，封存證據不自動刪除，遷移前一致性備份並驗證還原；見 `@code:app/db/session.py#get_engine`、`@code:app/db/schema.py#initialize_schema`、`@code:alembic/env.py#run_migrations_online`。
- D-24 [arch] UI／CLI／排程僅驗證輸入、呼叫共同服務及呈現結果；合格流程須帶 experiment／config hash 並核對人工核准，變更設定建立新實驗；首頁 `@code:web/shortlist.html` 承接收盤研究及比較，`@code:web/control_panel.html` 保留進階操作，呈現缺項、覆蓋、AI 原文、拒絕／暫停／人工恢復理由；輸出邊界統一把 Decimal／e4 轉明確單位字串並沿用敏感資訊清理及 HTML／CSV 跳脫｜理由：各入口不可改變交易語意｜否決：前端另算績效、默填舊參數、研究流程依賴實盤送單；保留 token 及 Shioaji 人工流程，見 `@code:web/control_panel_app.py#_security_guard`、`@code:app/cli.py#cmd_paper_ledger`、`@code:app/scheduler/jobs.py#run_shortlist_ai_job`。
- D-25 [arch] 新增 `@code:app/reports/swing_comparison.py` 從封存批次及事件產生同期間淨績效、相對 A、回撤、曝險、成本、交易數、固定觀察期機會結果與研究耗時；前瞻／歷史探索及不完整期間分開揭露，保留未投入現金與期末未平倉估值；功能保留／整併／選用結論附入口依賴、重複職責、比較證據及待驗證項｜理由：報表可重建並支持簡化判斷｜否決：加總舊報表、強制期末清倉、挑掉 AI 故障日或樣本不足仍稱有效；既有匯出共用呈現工具，不先刪策略／盤中／Shioaji 功能。
- D-26 [arch] 依 `@code:conftest.py` 的匯入前資料庫隔離，測試使用獨立 SQLite／假行情及 AI／網路／券商替身；固定跨股資料驗證 CLI／服務／UI 同輸入同結果，涵蓋 PIT 修訂／修復、停用與缺資料、跨批次成交、費稅交割／公司行動、重跑／崩潰／鎖競爭、AI 期限／拒收、8,000 邊界／人工恢復，最後核對三組帳務與報表｜理由：主要風險位於服務交界及恢復｜否決：只重測 DDL、測試連到使用者資料庫或真實送單；實作前記錄既有 29 檔差異基線，必要接點增量修改並檢查既有回歸。
- D-13 [db] 新增隔離研究資料層，來源及修復事實僅追加，舊表保留相容快取；新封存須排除已失效版本且不回退污染舊值，既有封存保留並揭露後續污染｜理由：兼顧追溯與既有成果｜否決：覆寫來源再重算歷史（掩蓋修訂）；見 `@sql:deploy.sql#swing_source_versions`、`@sql:deploy.sql#swing_data_repairs`。
- D-14 [db] 人工核准綁定不可變設定雜湊，逐批封存完整輸入、覆蓋與來源集合；B/C 共用候選及排序，批次與全部決策同交易，AI 原文及失敗亦封存｜理由：公平比較且不可恢復後回填｜否決：只引用可變快取或事後補決策（無法重現）；見 `@sql:deploy.sql#swing_experiments`、`@sql:deploy.sql#swing_input_batches`、`@sql:deploy.sql#swing_batch_sources`、`@sql:deploy.sql#swing_candidate_decisions`。
- D-15 [db] 三組各一條共享資金事件鏈，金額用萬分之一元整數，持倉／交割／預留明細用帶版本 JSON 並核對總額；原決策與執行／估值批次分開追溯且限制可用時間｜理由：避免重複本金及未來行情滲入｜否決：浮點金額或單股帳本加總（無法可靠核對）；見 `@sql:deploy.sql#swing_paper_accounts`、`@sql:deploy.sql#swing_ledger_events`。
- D-16 [db] 寫帳採單一立即交易、連續序號與前狀態雜湊檢查；重跑鍵衝突須比對內容，B/C 觸線暫停與人工恢復留痕，固定觀察期結果和實驗失敗另行追加｜理由：可中斷續跑並保留完整觀察｜否決：忽略衝突或覆寫狀態（隱藏差異）；見 `@sql:deploy.sql#swing_opportunity_outcomes`、`@sql:deploy.sql#swing_experiment_events`。
- D-17 [db] 採 SQLite≥3.37／JSON1，後續接入 Alembic 並於每個連線啟用 foreign_keys／recursive_triggers；JSON 業務驗證、雜湊及逐項資金核對由共同服務交易負責｜理由：約束與服務各守可驗證邊界｜否決：以 create_all 或單次 PRAGMA 推定完整保護（不足）；取捨：序列寫入與追加快照會增加鎖競爭及儲存，部署版本／留存／備份待整合確認，回滾須延後自我參照外鍵檢查。
- D-12 [spec] 採納 D-2 至 D-11 為本期行為基準，原提案文字保留為決策史；啟動前個人設定仍須另行確認｜理由：使用者明確回覆「確認規格」｜否決：把規格核准推定為交易參數或實盤授權（超出本次確認範圍）。
- D-1 [spec] 範圍判斷：TASK_TYPE=feature、CHANGE_SCOPE=full、FRONTEND_REQUIRED=true（FRONTEND_TECH=HTML/JS）、DB_REQUIRED=true（DB_TABLES=monthly_revenues,financial_statement_summaries,research_evidence,shortlist_runs,shortlist_items；新增實驗及組合帳本由 db pass 定名）、NEW_API=true、EXISTING_API_CHANGE=true｜理由：需在既有介面呈現可持續追溯的三組組合實驗，單股帳本與僅記日期的基本面不足；服務邊界及相容方式留待 arch pass。
- D-2 [spec] 提案待本輪核准：必要設定於啟動前必填並確認，未知費率可另做明示假設的探索、不列合格比較｜理由：不替使用者選 ETF、風險及參數｜否決：靜默沿用百萬本金或舊預設（用途不同）。
- D-3 [spec] 提案待本輪核准：三組各自一份虛擬本金，B/C 只差事件篩選，C 排除保留現金、不替補｜理由：辨認 AI 貢獻與資金競爭｜否決：單股回測加總或同時改策略及排序（無法歸因）。
- D-4 [spec] 提案待本輪核准：前瞻觀察作 AI 效果主要證據，歷史 AI 回放獨立標為探索性｜理由：模型可能知道後續事件｜否決：僅在提示詞寫截止日即視為無前視偏誤（無法證明）。
- D-5 [spec] 提案待本輪核准：污染資料先預覽再修復或隔離，保留來源及版本，不覆寫已封存的實驗輸入｜理由：保證可重現與稽核｜否決：只更新最新值再重算歷史（掩蓋修訂）。
- D-6 [spec] 提案待本輪核准：主線沿用既有介面及一套確認過的波段策略，其餘策略、盤中工具先列選用，刪除另需證據｜理由：一期聚焦驗證｜否決：全面重寫及先刪後驗（回歸範圍過大）。
- D-7 [spec] 提案待本輪核准：B/C 淨值≤8,000 元停止新增、保留既有退出規則、人工恢復；A 持續作買持基準並揭露穿越｜理由：把風險預算化為可觀察行為並保持基準意義｜否決：自動清倉、自動恢復或改寫基準（使用者未指定且干擾比較）。
- D-8 [spec] 提案待本輪核准：無可靠公布時間的資料不參與基本面／事件評分；其餘可得資料及缺項均明示｜理由：避免將季底或資料標籤冒充已公開時間｜否決：以法定期限或猜測日期當實際公告（可能提前使用資訊）。
- D-9 [spec] 提案待本輪核准：AI 不可用或證據不合格時 C 暫停新增且標不完整，繼續既有退出風控；B 不受 AI 故障影響｜理由：保留失敗證據與風險管理｜否決：靜默降級後仍宣稱完整 AI 組（混淆結果）。
- D-10 [spec] 提案待本輪核准：日線零股成交只作研究近似，共用模型並標示假設；缺次日行情或可得流動性資訊時不假成交｜理由：整股開盤與零股成交不同｜否決：將日線近似或 Shioaji 模擬結果直接視為零股實際成交能力。
- D-11 [spec] 提案待本輪核准：三組共用公司行動及估值政策，未投入與 AI 排除資金留現金、無自動追加本金，研究耗時另列｜理由：公平比較投資與時間成本｜否決：忽略股利、重複還原或跨組搬移資金（扭曲績效）。

## 已知取捨與風險    <!-- crew:risk append-only -->
- 真實公告時間與修訂版本可能無法完整回補；保守排除會降低覆蓋率，資料完整度與比較有效性必須分開呈現。
- 本機資料新鮮度及長度不足不代表部署狀態；目前觀察池存在存活者與選樣偏誤，不能據此宣稱跨行情或全市場有效。
- 零股價格、成交機率、跳空與實際費率可能不同於研究假設；2,000 元是風險預算，並非保證可限制的最大損失。
- 股利、拆分、除權息及下市資料供應可能限制公平比較；缺資料不能當零值，還原價格與現金流不得重複計入。
- AI 原文存取與留存受來源限制；歷史模型知識洩漏無法靠截止時間完全排除，AI 不完整期間不得挑選性省略。
- ETF、股票池、策略版本與參數、單筆／組合限制及實際費率仍待啟動前確認；本輪提案須人類核准後才成為規則。
- 目前 29 個既有變更檔案包含未提交 shortlist；後續實作須辨識基線，避免覆蓋使用者成果或將其誤算為本任務產出。
- 自訂 Python 技術棧尚無專用掃描定義，解析器回退 legacy Java；下游以實際 Python／HTML 程式結構為準。

## 指路              <!-- crew:map  append-only -->
- [build 修復] 行情數值邊界：`@code:app/pipeline/swing_research.py#numeric_market_frame` (L119)；回放與稽核時間分流：`@code:app/paper/portfolio.py#EXECUTION_DECISION_TIME_SQL` (L16)。
- [build 修復] 字串行情及歷史回放回歸：`@code:tests/test_swing_verification_regressions.py#test_historical_research_replays_next_open_with_real_audit` (L267)。
- [build] 資料角色：`@code:app/db/swing_repository.py#SwingRepository` (L79)、`@code:alembic/versions/20261016_0015_swing_research.py#upgrade` (L65)
- [build] 核心角色：`@code:app/pipeline/swing_research.py#SwingResearchService` (L129)、`@code:app/paper/portfolio.py#PortfolioLedger` (L55)、`@code:app/reports/swing_comparison.py#build_comparison` (L14)
- [build] API／來源匯入角色：`@code:web/control_panel_app.py#swing_run` (L2647)、`@code:app/cli.py#cmd_swing_research` (L2082)、`@code:app/data/swing_source_import.py#import_source_bundle` (L214)
- [build] 前端角色：`@code:web/shortlist.html` (L177)
- [build] BDD 測試角色：`@code:tests/test_tw_swing_ai_validation_bdd.py#test_confirmed_configuration` (L246)
- 資料解析：`@code:app/data/finmind_client.py#FinMindClient.fetch_monthly_revenue`、`@code:app/data/finmind_client.py#FinMindClient.fetch_financial_statement_summary`
- 基本面模型：`@code:app/db/models.py#MonthlyRevenue`、`@code:app/db/models.py#FinancialStatementSummary`
- 時點查詢：`@code:app/db/repository.py#TradingRepository.get_latest_monthly_revenue`、`@code:app/db/repository.py#TradingRepository.get_latest_financial_summary`
- 基本面評分：`@code:app/services/scoring.py#_fundamental_scores`
- 策略與啟用：`@code:app/services/strategy_config.py#StrategyRunConfig`、`@code:strategies/multi/swing_system.py#MultiStrategySwingSystem.generate_signals`、`@code:strategies/multi/ensemble.py#EnsembleDecision.combine`
- 研究成交：`@code:app/backtest/execution_model.py#ExecutionModelConfig`、`@code:backtest/backtest_engine.py#BacktestEngine.run_multi`
- 單股帳本邊界：`@code:app/paper/ledger.py#run_symbol_paper_ledger`、`@code:app/advisor/backtest.py#AdvisorBacktestService.run`
- AI 證據與排除：`@code:app/services/subscription_research.py#validate_research_item`、`@code:app/services/subscription_research.py#CodexSubscriptionResearcher.analyze_batch`、`@code:app/pipeline/shortlist.py#ShortlistService.finalize`
- 證據留存：`@code:app/db/models.py#ResearchEvidence`、`@code:app/db/models.py#ShortlistRun`
- 現有操作入口：`@code:web/control_panel_app.py#PaperLedgerRequest`、`@code:web/control_panel_app.py#run_paper_ledger`、`@code:app/cli.py#cmd_paper_ledger`

## 檢查報告摘要      <!-- crew:rep  append-only -->
- [2026-10-18] verify PASS（F1／F2 修復複驗）｜✅15 ⚠️0 ❌0 🚧0 ⏭️0 👤0｜Health 100（AC 通過比例）；139 項測試及離線瀏覽器通過，歷史 A／B／C 交易數 1／4／2，真實稽核時間保留；安全未解項與手機溢位仍保留，IP／網路未動，UAT 待人工。
- [2026-10-17] verify FAIL｜✅12 ⚠️0 ❌3 🚧0 ⏭️0 👤0｜Health 80（AC 通過比例）；121 項後端測試通過，離線整合發現字串行情 500 與歷史決策時間導致漏成交；IP／網路設定未動，UAT 待人工。
- [2026-10-17] security FAIL（限定修復複查）｜🔴1 🟡2｜已修 AI 主體／否認誤判、布林型別與逾時資源，324 項測試通過；IP／跨站項未動，研究目錄及環境已縮限但完整檔案讀取隔離仍待處理。
- [2026-10-17] security FAIL｜🔴2 🟡4｜權限位址誤判、AI 原文主體／否認誤判；風險含跨站寫入、逾時資源、研究程序隔離與布林型別；僅離線掃描，依使用者限制不改 IP／網路設定，未修產品。
