# 投資標的與介面優化 Benchmark

更新日期：2026-07-13

本文整理幾個知名量化交易、投資研究與篩選工具，轉成 quant-trade 可落地的優化方向。目標不是複製大型平台，而是吸收它們最有價值的工作流：更好的投資標的、更清楚的分數解釋，以及更像日常投資工作台的介面。

## 一、外部工具觀察

| 工具 | 值得借鏡的重點 | 對 quant-trade 的啟發 |
| --- | --- | --- |
| QuantConnect LEAN | 事件驅動交易引擎、模組化元件、研究/回測/最佳化/實盤 CLI 工作流 | 保留本專案的安全下單主幹，但要把策略、資料、回測驗證與上線 gate 模組化 |
| Microsoft Qlib | AI-oriented 量化投資平台，強調從研究想法到 production 的流程 | 建立 `StrategyResearchSpec`、OOS / walk-forward、策略版本與資料版本紀錄 |
| OpenBB Workspace | 投資研究工作區，把資料、圖表、文件與 AI 分析整合到單一介面 | 控制台首頁應優先呈現「今日該看什麼」，而不是讓使用者自己翻 API raw JSON |
| TradingView / Finviz screener | 快速篩選、排序、條件濾網、可視化掃描大量標的 | 每日雷達應提供可調風險檔位、分數拆解、阻擋原因、觀察池與一鍵排序 |
| Freqtrade | 自動市場清單、黑名單、dry-run、backtest、hyperopt、WebUI/Telegram 監控 | 台股流程應維持模擬優先，並增加候選池自動擴張、參數敏感度與通知摘要 |
| vectorbt | 高速批次回測、參數掃描、互動圖表 | 對策略 A/B/C 與雷達門檻做批次參數掃描，找出穩定區間而不是單點最佳化 |
| Jesse | 多時間框架/多標的回測、內建風控、metrics、圖表、資料清理與 Monte Carlo | 每日雷達不只產生名單，也要能解釋候選是否可研究、是否只是資料缺口造成的假訊號 |
| QuantRocket | Securities master、資料收集佇列、Data Browser、Pipeline、Moonshot / Zipline / live trading 工作流 | 資料覆蓋與研究結果要同時出現在日常工作台；資料管線異常時，投資標的應先降級成「修資料後再看」 |
| Backtrader | Python 策略研究與回測生態成熟 | 保留現有 Python 生態，讓策略介面更清楚、測試更容易 |

### 2026-07-08 補充觀察

- QuantConnect LEAN 的成熟點不是只有回測，而是把 `research`、`backtest`、`optimize`、`live` 串成一致 CLI 工作流；本專案應把「資料是否可信」放在每日雷達前面，避免壞資料直接進入下單流程。
- Freqtrade 明確把 market selection、dry-run、backtesting、hyperopt、WebUI/Telegram 監控放在同一條線；本專案的台股版本應保留「先觀察 / paper / 小額 live」的節奏，並讓首頁直接提示下一步。
- vectorbt 的優勢是多參數、多標的、多期間快速掃描；本專案後續可以把雷達門檻、風險檔位與族群限制做成參數穩健性探索，而不是只看單日最高分。
- Jesse 的自動資料清理、metrics、圖表、Monte Carlo 與多標的回測提醒我們：每日雷達要先證明候選品質夠乾淨，才值得進入研究與配置。

## 二、優化原則

1. 日常介面先回答三件事：今天可進場、值得觀察、不要碰。
2. 每個標的都要有理由與阻擋標籤，不能只給單一總分。
3. 投資標的排序要同時考慮技術、籌碼、基本面、新聞風險、流動性、資料品質。
4. 回測報表要能說明策略是否適合進 paper，而不是只列績效。
5. 正式下單永遠晚一拍：先觀察、再 paper、再小額 live，流程不能為了方便跳關。

### Phase 1 結束規則

目前先把第一個可暫停階段定義為「日常決策雷達驗收版」：每日雷達、控制台與通知必須能讓使用者在盤前快速判斷「可下手 / 只觀察 / 不碰」，並且透過 `docs/decision_radar_phase1_acceptance.md` 的流程跑過 2 到 3 個交易日。使用者確認 Phase 1 通過前，不進入下一階段的大型介面重構或大規模跨市場池最佳化。

## 三、建議交付順序

### P0：可靠性與資料庫相容性

- 修正舊 SQLite 沒有新欄位時的自動補欄位流程。
- 確保 `pytest -q` 全綠。
- Advisor proposal API 不能因舊雷達資料表欄位缺漏而回 500。

### P1：每日雷達可讀性

- 雷達卡片顯示價格、Entry Score、風險分數、資料品質。
- 補上基本面分數拆解：營收、財務品質、估值/成長、新聞風險。
- 保留阻擋標籤：資料過期、處置股、低流動性、漲跌停風險、籌碼缺漏。

### P2：候選池品質

- 將目前 0050 清單擴充到台灣 50 + 中型 100 + 自訂 watchlist。
- 加入產業分散限制，避免每日雷達全部集中在單一族群。
- 加入近期波動 / ATR 風險欄位，讓高分但過熱的標的比較容易被辨識。

目前進度：
- 已新增 `curated_watchlist` 精選觀察池，作為一鍵候選與每日雷達的日常預設。
- 已支援 `dynamic_balanced`、`dynamic_liquid`、`tw0050`、`tw_mid100`、`twse_tpex`、`custom` 市場池模式；控制台只會在 `custom` 模式送出自訂代號。
- 已新增 `dynamic_balanced` 與 `dynamic_liquid` 市場池：前者用最近成交金額搭配正式產業分類輪替取樣，後者直接選最近流動性較好的上市 + 上櫃標的；兩者都會排除不碰清單，資料不足時退回精選觀察池。
- 已新增市場池驗池 preview：控制台可在跑一鍵候選或每日雷達前檢查目前 universe 的來源、fallback、可用檔數、近期日線 freshness、不碰清單命中、產業分布與分類來源，讓動態池更接近 Freqtrade pairlist 測試工作流，先確認標的池品質再進入雷達。`dynamic_balanced` 在正式產業分類不足時會用台股代號前綴 proxy 做分散取樣，不再直接退成純流動性池；驗池會標示 proxy 佔比，避免把備援分類誤看成正式產業資料。
- 已新增台灣 50 與中型 100 同步來源：控制台與 CLI 可讀取元大 0050 PCF，寫入 `data/cache/tw0050_symbols.txt` 並更新 `universe_0050_snapshot`，遠端不可用時才退回本機快取；控制台與 CLI 也可讀取元大 0051 PCF，寫入 `data/cache/tw_mid100_symbols.txt`。中型 100 沒有快取檔時會以最近日線成交金額推估非台灣 50 的流動性前 100 檔，最後退回內建啟動清單。
- 已新增產業分類資料流：FinMind 股票清單的產業分類會保存到 `instruments.industry`，每日雷達優先用正式產業分類做族群分散，缺資料時才回退到股票代號前綴 proxy。`data-health-overview` 會顯示正式產業分類覆蓋率與缺口樣本，CLI 可用 `.venv/bin/python -m app.cli sync-twse-tpex-universe` 重新同步 TWSE / TPEX 股票名稱、市場別與產業分類；一鍵候選與每日雷達若偵測到產業 metadata 缺漏，也會先嘗試同步再產生候選，讓 proxy 只作為備援，而不是長期靜悄悄地接管分群。
- 日線同步已新增 TWSE 官方 `STOCK_DAY` 與 TPEx 官方 `tradingStock` fallback：當 FinMind 日線同步失敗且本機 parquet 沒有可用資料時，上市股票會嘗試改抓 TWSE 官方日線，上櫃股票會接 TPEx 官方日線，降低核心候選因 FinMind 權限/token 問題完全缺日線的機率。
- 資料健康度已新增市場資料覆蓋 audit：`market_data_coverage` 會按 TWSE/TPEX 普通股拆出日線、法人買賣超與分點聚合的近期覆蓋率、缺口代號樣本與建議重試同步指令，CLI `repair-market-data-coverage` 可先乾跑再執行修復，也可用 `--batches` 連續跑多個小批次，並會略過近期已確認不可重試的代號，避免修復佇列卡在同一批股票；控制台資料源修復佇列與市場資料同步卡也能直接執行同一份小批次修復計畫，完成後會重新整理資料健康度、營運總檢與雷達品質摘要，讓資料黑洞在進入每日雷達排序前先被看見並處理。
- 市場資料覆蓋率會把近期同步已確認 `upstream_no_data`、`upstream_not_found`、`upstream_bad_request`、`upstream_forbidden`，以及官方 bulk 重試後仍受限的 `upstream_payment_required_after_official` 代號列入 `unavailable_symbols`，從可修復缺口與覆蓋率分母中分開呈現；同步 job 也會保存完整的 `failed_symbol_scopes` 與是否使用官方 bulk 的 metadata，避免大型批次只記前幾筆失敗樣本，讓下一輪修復又挑到同一批代號。`repair-market-data-coverage` 也會合併 coverage payload 的不可重試清單，避免長批次後又回頭撿到已確認查無資料的代號。2026-07-09 實跑多輪小批次後，近期日線日期已更新到 2026-07-09，標的池覆蓋由 46% 提升到 56%，普通股日線覆蓋由 59% 提升到 74%，法人買賣超由 57% 提升到 71%，上櫃普通股日線覆蓋已到 100%；小批次修復新確認 `3142`、`3214`、`3271` 查無近期日線與法人資料，會列入不可重試清單。同步健康度與每日雷達健康摘要都會把「有成功寫入資料、失敗原因只有官方查無資料」的 partial 修復批次視為良性資料缺口，不再把資料修復進度誤顯示成同步流程故障。下一步仍是繼續清 TWSE 缺口與決定分點聚合的 sponsor / 替代資料源策略。
- 已新增 ATR 熱度標籤：`normal`、`elevated`、`hot`，控制台會顯示「波動：正常 / 偏高 / 過熱」。
- 已補齊三段風險檔位：`conservative` 會提高 Entry / Watch 門檻、較早標示 ATR 過熱並收緊族群集中；`balanced` 保留日常預設；`aggressive` 放寬波動與軟性進場條件，適合擴大觀察池。
- 已新增產業輪動分數：同族群候選的領先分數、近 5/20 日族群報酬與上漲家數比例會形成 `industry_rotation_score` 與 `industry_trend_*` 指標，排序時以小權重偏向較強且正在升溫的族群。
- 已新增不碰清單管理：控制台可加入、更新、移除不碰代號，也可直接編輯 `data/cache/excluded_symbols.txt`；一鍵候選會跳過，每日雷達會標示阻擋原因。
- 已新增外部指數時間序列：讀取 TWSE `MI_INDEX type=IND` 的產業分類價格指數、TPEx 官方 `statistics/idx` 產業分類股價指數月報 XLSX，以及 TPEx 官方 `indexInfo/inx` 的櫃買整體指數，寫入 `industry_index_bars`；控制台可一鍵同步近 45 日，市場同步與 CLI 也可選擇同步。
- 已新增匯入式與 URL 同步式指數成分股來源：可把指數公司或官方網站下載的 CSV/TXT 用控制台「指數成分股快取」貼上匯入，也可填入官方 HTTPS 下載 URL（支援 CSV/TXT/XLSX），或用 `import-index-constituents --index-key <key>` / `sync-index-constituents-url --index-key <key> --url <官方HTTPS下載URL>` 匯入本機快取，並在每日雷達使用 `--universe index:<key>` 或控制台套用 `index:<key>`。臺灣指數公司官方資訊商品頁顯示成分股檔案屬 Data E-Shop / 資訊商品，因此目前支援合法授權下載 URL 自動同步，尚未假設存在免費公開完整成分股 API；TPEX-only 場景會先用 TPEx 官方產業分類股價指數，再退回本機同產業上櫃股票等權背景，資料不足時才退回 TPEx 櫃買整體指數。首頁資料健康區已新增市場背景覆蓋度與指數成分股快取 freshness，會直接標示 TWSE 官方指數、TPEX 官方產業指數、TPEX 等權背景、TPEx 市場保底，以及 `index:<key>` 快取組數、檔數與最新更新時間。

### P3：研究治理

- 建立 `app.signals.registry` 與策略基礎介面。
- 新增 `StrategyResearchSpec`、`BacktestValidationReport`、`WalkForwardResult`。
- 每次策略回測輸出 OOS、參數敏感度、流動性、處置股、交易成本占比。

目前進度：
- 已新增第一版 `app.signals.registry` 與 `app.signals.base`：目前會登錄每日雷達常用理由標籤、阻擋標籤、分類、資料需求與 outcome 分析政策，控制台也可透過 `/api/signals/catalog` 取得訊號目錄。
- 已新增第一版 `StrategyResearchSpec`：每次多策略回測會輸出研究假說、資料需求、策略參數、風控、成交假設、驗證計畫、升級條件與 artifacts 清單。
- 已新增第一版 `BacktestValidationReport`：多策略回測會輸出交易樣本、報酬輪廓、最大回撤、Sharpe、70/30 OOS 分段驗證、walk-forward 滾動視窗驗證、代表標的籃子敏感度、跨族群壓力、threshold 鄰近值參數敏感度、權益曲線後段穩定度、交易成本占比、成交拒絕率、資料品質與成交模型檢查。
- 已新增第一版參數穩健性摘要：多策略回測會把 threshold 鄰近值的正報酬比例、負報酬變體、零交易變體、最差報酬、baseline 與最佳值差距整合成 `parameter_robustness`，控制台會先顯示「穩健區間 / 可觀察 / 參數敏感 / 參數脆弱」與下一步建議，再讓使用者查看 threshold 明細，避免只追單點最佳化。
- 每日雷達的 outcome 可靠度與配置校準已將樣本單位固定為「標的 × 交易日」：同日重跑只保留最新快照，配置驗證也只保留每個交易日最新的一輪。控制台會顯示被合併的重跑筆數，避免把重跑紀錄誤當成獨立歷史證據。
- 跨日變化、穩定候選與研究優先序也改以不同交易日計算：系統會比較最近兩個有效交易日，穩定度只統計可進場或等待觸發的標的，長期「不建議進場」的重複出現不會被包裝成穩定首選。
- 控制台回測結果會顯示 research gate：`paper_ready`、`needs_review` 或 `blocked`，並列出 `StrategyResearchSpec` 摘要、研究假說、OOS、walk-forward、跨標的敏感度、跨族群壓力、阻擋項目與下一層驗證。
- 跨標的敏感度已支援使用者自訂驗證籃子、掃描上限與單檔最少日線筆數；結果會回報代表標的數、族群數、族群彙總與族群來源統計，若族群覆蓋不足會在 research gate 顯示警示。
- 跨標的敏感度會優先使用 `instruments.industry` 正式產業分類，缺資料時才回退到股票代號分類。
- 已新增正式產業分類自動分層抽樣：若未填自訂籃子，系統會按產業取近期流動性代表標的，避免跨標的壓測被單一熱門族群主導。
- 已新增第一版跨族群壓力摘要：依每個族群的平均報酬、最差報酬與可交易比例標記 `resilient`、`mixed`、`fragile`，並納入 research gate。
- 已新增外部與本機族群背景優先順序：若 `industry_index_bars` 有足夠 TWSE 產業指數資料，跨族群壓力會使用外部價格指數；若驗證範圍明確是 TPEX，會先使用 TPEx 官方產業分類股價指數，再退回本機同產業上櫃股票等權背景；資料不足時才退回 TPEx 櫃買整體指數作為市場保底。
- 臺灣指數公司完整成分股資料源目前已支援授權/官方下載 URL 同步與 CSV/TXT/XLSX 解析；公開免費 API 直連仍未完成，因官方資訊商品頁將成分股名單、權重與完整檔列為 Data E-Shop 資訊商品。目前第一版會使用 TWSE 產業價格指數、TPEx 官方產業分類股價指數、本機 TPEX 同產業等權族群背景、TPEx 櫃買整體市場保底、自訂籃子或正式產業分層代表標的做策略可複用性檢查。

### P4：介面升級

- 首頁改成「決策雷達」：今日候選、觀察池、阻擋原因、資料健康度、下一步動作。
- 將待優化工具箱留作研究區，日常只保留必要操作。
- 增加可匯出的投資標的清單：CSV / JSON / HTML 摘要。

目前進度：
- 已新增首頁「今日決策雷達」摘要：優先觀察、族群分布、阻擋原因集中在第一屏附近。
- 已新增「可進場 / 觀察中 / 不要碰」三段式投資行動卡，直接顯示每日雷達各類數量、代表標的與主要理由，並可一鍵切到 AI 決策頁套用對應篩選；代表標的 chips 可直接帶入 Preview、查看詳情，或把高風險標的加入不碰清單。
- 已新增首頁「日常決策 / 研究與維運」工作模式切換，並把台灣 50 / 中型 100、外部指數與指數成分股快取等資料源工具收進 AI 決策頁的維運抽屜；日常入口預設只保留資料健康、每日雷達、部位檢查與完整清單。
- 已保留一鍵重新計算雷達與完整清單入口，日常工作流可以從首頁直接進入 AI 決策頁。
- 資料健康度首頁已新增注意股 / 處置股 freshness 卡片與警示列；一鍵候選與每日雷達同步時會嘗試更新 Shioaji 注意股，避免過期風險名單被誤判成安全。
- 已新增 `run-daily-radar-job` 與 `run-scheduler --enable-daily-radar`，可將每日雷達資料同步、注意股更新、候選排序、快照保存與匯出排成平日固定工作；`--notify telegram|line|both` 會在雷達完成後把 `decision_brief` 壓成盤前 Brief 通知，送出主線、交接摘要、三條行動線與報表連結。若使用授權指數成分股 URL，可在 job 前用 `--index-constituents-url` / `--radar-index-constituents-url` 先同步 `index:<key>` 快取再選股。macOS 可用 `write-launchd-plist` 產生 launchd plist，Linux/VM 可用 `write-systemd-units` 產生 systemd service + timer，容器部署可用 `write-docker-compose` 產生 Docker Compose + Dockerfile 常駐每日雷達排程；`run-scheduler --enable-trigger-monitor`、`write-trigger-monitor-launchd-plist`、`write-systemd-units --include-trigger-monitor` 與 `write-docker-compose --enable-trigger-monitor` 也可把觸發觀察監控接進固定排程。
- 已新增每日雷達部署健康檢查：`daily-radar-health` CLI、`/api/radar/deployment-health` 與首頁部署狀態列會檢查最新 run、資料年齡、候選數、可進場數與 `daily_radar` sync job，讓排程漏跑或部分完成可以被看見；systemd 產生器可一併輸出健康檢查告警 timer，Docker Compose 也會預設加入 healthcheck。
- 每日雷達部署健康檢查已補上 action 分布、待觸發觀察檔數、前三檔 `WATCH_WAIT_TRIGGER` 候選的觸發摘要，以及等待條件聚合；當 `ENTRY_READY=0` 時，首頁不只顯示警示，也會提示目前可追蹤哪些標的、主要是在等 Entry Score 重新整理轉強、價格突破/回測、ATR 風險降溫、阻擋解除或資料 freshness 補齊。
- 已新增 `run-invest-pipeline` CLI，讓一鍵候選不必只靠控制台 API 重新計算；可指定 `--trade-date`、`--target-count`、`--risk-profile`、`--universe`、`--symbols`、`--include-attention` 與 `--include-extended-chip`，並把日常刻意略過的擴展籌碼記到 `skipped_steps` 而非直接降級。若候選是為了湊足觀察清單而放寬觸發條件，會以 `candidate_mode=relaxed_watch` 與 `quality_warnings` 標成「觀察完成」，不再誤判成降級失敗。2026-07-09 已用 `dynamic_balanced` 重新產出一鍵候選 run `run_20260709_58fb3113`，候選 freshness 警示已解除。
- 已新增營運 readiness 總覽：`ops-readiness` CLI、`/api/ops/readiness` 與首頁「營運總檢」卡會把每日雷達健康檢查、收盤後對帳摘要、promotion readiness 與 live / CodexAdvisor 環境旗標彙整成單一 `ok / warn / danger` 判讀，並輸出阻擋原因、核心指標與下一步；首頁卡也會把阻擋/提醒轉成 chips 與處理按鈕，方便部署後或開盤前快速確認這套投資工作台是否可以安全使用，並直接跳到雷達部署、交易驗收、升級 Gate 或環境檢查區。首頁資料健康度也會依 sync job failure summary 產生「資料源修復佇列」，把資料集缺口、代號缺口、FinMind 用量檢查、Shioaji 環境檢查、資料源工具、重跑每日雷達與小範圍重試同步整理成可執行工作項；重試完成後會自動重新整理資料健康度、營運總檢與雷達部署狀態，並顯示修復前後的待辦、資料集缺口與代號缺口差異。
- 已把分點聚合從核心資料覆蓋拆成加值資料：資料健康度與控制台會標示 `FinMind sponsor` 需求、不可自動修復，修復計畫也會略過這類資料集，避免把 sponsor-only 權限問題誤判成一般資料缺口；下一步是決定升級資料源或改接官方公開分點/注意/處置資料替代來源。
- 每日雷達同步預設改成核心資料優先：先同步日線、法人與產業指數，分點、處置、基本面與新聞改由 `--include-extended-data` / 控制台擴展資料勾選啟用；候選品質摘要也會忽略 broker-only 加值缺口，不再把可用候選誤丟進「修資料後再看」。
- 已新增每日決策 HTML/CSV/JSON 匯出，內容包含今日動作佇列、優先觀察、族群分布、阻擋原因、完整雷達清單與可供後續分析的結構化資料。
- 每日決策 HTML/CSV/JSON 匯出已同步加入 `quality_summary` 候選品質摘要、`trading_feedback` 交易回饋摘要、`decision_brief` 結構化盤前摘要、`investment_actions` 三段式投資行動分類、`diversified_shortlist` 跨族群分散首選、`allocation_plan` 研究配置草稿、`allocation_diagnostics` 配置診斷、`review_queue` 三段式行動線與行動線摘要、`allocation_outcomes` 配置驗證與 `allocation_calibration` 配置校準；總表 CSV 會保留行動線主線、盤前交接、三類檔數、提醒規則與檢查清單，並額外輸出含候選品質、品質分流與交易回饋判讀的盤前 Brief Markdown、Preview / Trigger / Risk 三份含價格、分數、資料品質、提醒計畫、理由與阻擋標籤的獨立 watchlist CSV，以及三份純代號 TXT，HTML 報表也會直接掛上 CSV/TXT/MD 連結並顯示提醒計畫與盤前交接，控制台匯出結果會用可讀名稱列出各 artifact，API 回應也會直接帶 `data.decision_brief`，排程通知也可直接發送同一份含品質分流摘要的 Brief，控制台也可預覽與測試送出通知，並顯示 Telegram / LINE 通道設定狀態；控制台另可把 review queue 轉成短版提醒檢查訊息，單獨預覽或送出給已設定通道，讓候選品質、交易回饋、可進場、觀察中、不要碰的代表標的、分散池排名、配置金額、配置 review lane、校準 profile、配置風險判讀、下一步行動與後驗證據可直接留存、通知與分析。
- 已新增每日雷達跨日候選池變化、穩定候選、研究優先序、研究配置草稿、配置診斷、review queue、配置驗證與配置校準：`/api/radar/changes` 與首頁卡片會比較最新與前一輪 run，列出新進、連續出現、掉出、轉強與轉弱標的；`/api/radar/stability` 會用最近多輪 run 找出連續出現、分數較穩且最新仍在清單內的標的；`/api/radar/research-priority` 會把 Entry、穩定度、跨日變化、可靠度與阻擋整合成「今天先研究」排序，直接提示下一步是建立 Preview、設觸發觀察、清交易回饋、處理阻擋或先略過，摘要也會聚合「可建 Preview / 觸發觀察 / 待處理」檔數、標示最大族群集中占比並拉出跨族群研究首選，且可直接複製代號、下載含交易回饋欄位與分散排名的優先序 CSV 或套用成自訂觀察池；`/api/radar/allocation-plan` 會套用保守研究曝險、單檔上限與族群上限，估算建議金額、零股股數、整股張數與下一步提示，並依成熟 outcome 樣本產生的小幅校準 multiplier 調整權重；`allocation_diagnostics` 會補上預算使用率、最大族群占比、配置警示與下一步，並把標的排進「可先建立 Preview / 設觸發觀察 / 先處理風險」三條 review queue，每條行動線附提醒計畫，每檔標的附提醒規則與檢查清單，並用 `handoff` 摘要標出盤前主線、下一步與代表代號；控制台會用行動線摘要顯示三類檔數與目前主線，並可一鍵複製各行動線代號、下載單一行動線 CSV 或套用成自訂觀察池，CSV 也會保留 review lane 與交易回饋風險欄位，讓配置草稿有 portfolio-level 風險判讀與可操作 watchlist；`calibration_profile=off|conservative|standard|aggressive` 可調整歷史證據影響力，控制台提供關閉、保守、標準與積極切換；`/api/radar/allocation-outcomes` 會用已成熟的歷史雷達 run 回測配置草稿，並和單純照排名挑前幾檔的基準比較平均報酬、正報酬率與配置 alpha；`/api/radar/allocation-calibration` 會列出目前加權與降權最明顯的族群、動作、波動熱度與理由因素，讓 watchlist 更接近專業投資工具的「今日真正變動」、「反覆浮現候選」、「研究優先級」、「配置前檢查」、「配置後驗證」、「條件提醒」、「盤前交接」與「權重校準」工作流。
- 研究優先序的分層已和行動語意對齊：低分但仍是 `WATCH_WAIT_TRIGGER` 的標的會顯示「保留觀察 / 設觸發觀察」，有阻擋則顯示「先處理阻擋」，`NOT_RECOMMENDED` 才顯示「先略過」，避免同一張卡同時叫使用者觀察又叫使用者放棄。
- 配置草稿已把「可配置」和「可盯盤」拆開：`WATCH_WAIT_TRIGGER` 若分數低於配置門檻，會以 `monitor_only` 進入「設觸發觀察」行動線並標為盯盤備選，不占用研究額度；有阻擋、交易回饋或 `NOT_RECOMMENDED` 的標的才留在風險或略過流程。
- 盯盤備選語意已同步到控制台 CSV、每日雷達總表、行動線 CSV、盤前 Brief 與 HTML 摘要；匯出欄位會保留 `monitor_only`、`allocation_lane` 與 `allocation_status`，方便外部試算表或通知流程區分已配置、盯盤備選與未配置。
- 設觸發觀察行動線已新增 `trigger_watchlist_note`，會把代號、名稱、配置狀態、Entry Score 目標、突破/回測價、ATR 降溫條件、優先分與成熟度壓成一行 watchlist 備註，並同步到控制台卡片、控制台 CSV、每日雷達總表、行動線 CSV、盤前 Brief 與 HTML 摘要，方便直接貼到外部觀察清單或通知流程。
- 盤前 Brief 與排程通知已新增短版觸發摘要：`WATCH_WAIT_TRIGGER` 標的會以 `Entry>=74 / 突破87.06 / 回測84.48` 這類短句呈現，Markdown Brief 不再同時輸出完整觸發條件與盯盤備註，控制台提醒檢查也會沿用同一份短版條件，降低盤前訊息重複。
- 已新增第一版觸發觀察監控：`daily-radar-trigger-monitor` CLI、`/api/radar/trigger-monitor` 與控制台「觸發觀察監控」卡會檢查 `WATCH_WAIT_TRIGGER` 標的的 Entry Score、突破/回測價、ATR 熱度、資料 freshness 與阻擋標籤，分成「已觸發 / 接近觸發 / 等待中 / 先處理阻擋」，並可用 `--price-source shioaji_snapshot` 以 Shioaji snapshot 覆蓋雷達快照價格，或用 `--price-source shioaji_streaming --streaming-seconds 3` 短時間訂閱 Shioaji tick / bidask，優先取 tick 最新成交價、沒有串流報價時 fallback snapshot，讓突破與回測判斷不只依賴上一輪雷達價；串流資料已先進 bounded quote buffer，會回傳 tick/bidask 收到筆數、保留筆數與訂閱事件數，控制台也會在觸發監控卡顯示報價來源、freshness、Tick/五檔筆數與 fallback 狀態，作為後續介面報價狀態與常駐行情 manager 的底座。外部報價會套用 `--max-price-age-seconds` / `--trigger-monitor-max-price-age-seconds` freshness gate，過期或時間不可判讀時會降回雷達價格，不會把標的推成已觸發或接近觸發。固定排程已接到 `run-scheduler`、launchd、systemd 與 Docker Compose 產生器，通知也會用狀態檔與冷卻時間抑制同一 run、同一批觸發代號的重複提醒。
- 已新增候選品質總覽：`/api/radar/quality-summary` 會把最新每日雷達的可進場比例、無阻擋比例、資料 freshness、基本面覆蓋、低風險比例、可靠度樣本、族群分散、ATR 過熱與 sync job 資料缺口整合成品質分數、品質警示與 Top Quality Picks；首選排序會避開命中代號缺口的候選，若資料缺口影響候選則改判為「先修資料再選股」，並把候選分成「可先研究 / 只觀察 / 修資料後再看 / 先略過」四條行動線，每條線可複製代號，「可先研究 / 只觀察」可直接套成自訂觀察池，「修資料後再看」可跳到資料源修復佇列；每日決策匯出也會產生四條品質分流的 CSV 與純代號 TXT，方便留存、回看或貼到外部 watchlist。重試同步完成後會顯示品質分、缺口候選與缺口資料集的修復前後差異，讓首頁先回答「今天這份清單值不值得研究」而不是盲目相信高分；每日雷達 JSON、盤前 Brief 與通知也會保留同一層品質判讀、品質分流與缺口資料集摘要，方便盤前決策與盤後回看候選池品質。
- `WATCH_WAIT_TRIGGER` 即使 ATR 過熱，只要資料新鮮且沒有硬性阻擋，會留在「只觀察」並保留風險降溫條件；不建議進場、資料過期或有阻擋的標的才進「先略過」，避免介面把等待觸發誤呈現成完全放棄。
- Top Quality Picks 現在只列「可先研究 / 只觀察」標的；命中資料缺口或 `NOT_RECOMMENDED` 的股票會留在品質分流裡，但不會被複製或套用品質池，避免把「先略過」誤包成首選。
- 已新增交易回饋摘要：`/api/radar/trading-feedback` 會把最新每日雷達候選與近期 Preview、execution、promotion gate、reconciliation 交叉，標出待決策 Preview、送單錯誤、gate 阻擋與對帳不一致；「今天先研究」排序會先扣交易回饋風險，研究配置草稿也會把未清交易回饋的候選降到「先處理風險」行動線，讓隔日候選排序前先看「營運風險是否已清掉」。
- 已新增每日雷達互動式篩選器：可依代號/名稱/族群/理由搜尋，並依進場狀態、族群、ATR 波動熱度、可靠度加權/樣本、阻擋原因、最低 Entry Score 與排序條件縮小觀察清單；另提供高可靠可進場、低波動可進場、高可靠觀察與可靠度扣分檢查快速條件，按鈕會顯示目前命中檔數並標示作用中條件。篩選後會顯示品質判讀、首選標的、平均 Entry、可靠度加分檔數、無阻擋檔數與主要族群，並產生跨族群分散首選；清單也可直接複製代號、套用為自訂觀察池，或複製/下載 CSV，匯出報表會保留分散首選與排名，方便留下當天候選紀錄並接續研究。
- 控制台市場池選擇器已新增「市場池驗池」面板，會隨交易日、市場池與自訂代號即時重新整理，顯示來源、檔數、近期日線覆蓋、不碰清單命中、產業分布與主要建議，讓使用者在跑一鍵候選或每日雷達前先知道這批輸入是否可靠。
- 每日雷達快篩已新增「風險降溫觀察」：當部署健康摘要判斷 `WATCH_WAIT_TRIGGER` 主因是 ATR 過熱或風險尚未降溫時，首頁可一鍵切到觀察中 + 過熱標的，並依觸發成熟度由高到低排序，方便盤前先看哪幾檔最接近解除風險。
- 每日雷達卡片會直接顯示 `WATCH_WAIT_TRIGGER` 的等待條件 strip：包含主要等待狀態、觸發成熟度、Entry Score 差距、突破/回測距離與觸發摘要；使用者不用打開匯出報表或詳情頁，也能在清單層直接判斷是等風險降溫、等價格回落、等突破確認，或等資料 freshness 補齊。
- 首頁「等觸發」行動線會優先顯示觸發成熟度較高的觀察標的，並在卡片底部直接標示成熟度、Entry 差距與回測/突破距離，讓盤前不用進入完整雷達頁也能知道先盯哪幾檔。
- 每日雷達的前端篩選 CSV、配置 review lane CSV 與主報表 CSV 會保留觸發成熟度、Entry 差距、突破/回測距離與成熟度標籤，方便把「等觸發」清單帶到試算表、外部 watchlist 或通知流程中繼續追蹤。
- 已新增研究優先序分數拆解：`/api/radar/research-priority` 會輸出 Entry、穩定度、動作、跨日變化、阻擋、可靠度、風險與交易回饋的加扣分元件，控制台卡片與優先序 CSV 會同步顯示，讓「今天先研究」不只排序，也能解釋排序。
- 已新增每日雷達標的比較面板：可從卡片挑選最多 4 檔，橫向比較 Entry Score、風險、價格、波動、可靠度、阻擋與理由，並用保守 `compare_score` 與「比較結論」提示優先檢查標的；比較 CSV 也會帶出分數，讓候選股選擇更接近專業 watchlist 工作流。
- 已新增觀察線觸發條件：配置 review queue 的「設觸發觀察」標的會產生 `trigger_plan`，列出 Entry Score 目標、突破/回測參考價、ATR 熱度與待檢查條件；控制台、盤前 Brief、HTML、JSON 與行動線 CSV 都會帶出這些條件，讓「今天沒有可進場標的」可以轉成「今天要等什麼」。
- 已新增候選後續表現追蹤：控制台會顯示最新每日雷達候選在後續 5 根日線的正報酬率、平均報酬，並依族群、理由標籤與 ATR 波動熱度拆解命中率，協助判斷哪些條件真的比較可靠。
- 已新增訊號說明與可靠度表：控制台會讀取 `app.signals.registry`，把最近 outcome 出現的理由標籤接上訊號分類、中文說明與正報酬率，避免日常介面只剩技術字串。
- 已新增 outcome 可靠度回饋排序：每日雷達會讀取近期已成熟的候選後續表現，依理由訊號 bucket 計算 5 / 10 / 20 根日線的正報酬率與平均報酬，只在樣本數足夠時對 Entry Score 做小幅加權，並在控制台與匯出報表顯示「可靠度加權」與多週期摘要。
- 已新增第一版每日雷達門檻穩健性掃描：每日雷達匯出會用最新候選清單模擬積極 / 平衡 / 保守風險檔位與 ±2 / ±4 分門檻偏移，標出穩定標的、門檻邊緣標的與下一步；JSON、盤前 Brief、HTML 摘要、通知與控制台「今日決策雷達」卡片都會帶出結果。控制台也新增 `threshold-backtest` 歷史實跑，回看最近多輪雷達與 5 / 10 / 20 根日線 outcome，輸出最佳門檻、基準門檻、跨週期表現、上市 / 上櫃、風格、族群分層、結構化 `threshold_heatmap` 風格 × offset 門檻熱區，以及 `recommended_action` 採用判讀與 `recommended_config` / `optimization_candidates` 候選設定排名；熱區 payload 會標出基準、最佳與穩定區格子，控制台可直接用矩陣顯示哪個門檻組合較可靠。每組候選都會帶 `run_params`，並彙整成 `optimization_plan` 批次重跑計畫；`POST /api/radar/threshold-optimization-runs` 與 CLI `run-daily-radar-threshold-optimization` 可先乾跑前幾組，也可小批次執行推薦設定並寫入新的每日雷達 run。批次最佳化也已可用 `grid_universes` / `--grid-universes`、`grid_profiles` / `--grid-profiles`、`grid_offsets` / `--grid-offsets=-2,0,2` 主動產生跨市場池 grid 候選，控制台乾跑預設會把研究就緒池、動態平衡池、台灣 50 與中型 100 放進比較，也可直接試算 Grid 前 6 組提高探索覆蓋；`candidate_source=grid` / `--candidate-source grid` 與控制台「執行 Grid 第 1 組」可只挑 grid 最高分候選小批次重跑，結果卡也會用來源摘要、來源比較卡與每列徽章標出「歷史推薦 / 跨市場池 Grid / 混合」，並直接提示目前是 Grid 領先、歷史推薦領先或兩者接近；來源比較卡會列出兩邊最佳設定、信心等級、guardrails 與本次候選覆蓋率，後端 `primary_action` 會指出建議執行來源與 CLI command，控制台也會把對應快捷按鈕標成主要操作，並可直接複製推薦或 Grid 的 CLI 指令交給 VM/cron；每列批次候選也可複製自己的 CLI 指令，方便挑任一組設定丟到終端或排程。批次結果會依歷史排名分、重跑後可進場/觀察檔數、降級與錯誤狀態產生採用判讀，並新增 `optimization_grid` 依市場池/族群、風格與 offset 彙總最佳分數、ENTRY/觀察檔數與代表代號，讓批次結果先呈現哪個維度值得優先重跑；每組仍保留代表標的 `top_items` / `top_symbols`。控制台可一鍵複製建議設定的代表代號，或套用成自訂觀察池接續重跑。採用後的 radar run 會把 `threshold_adoption` metadata 寫入 sync job，`GET /api/radar/threshold-adoptions` 與控制台「採用追蹤」會回看成熟 outcome，直接列出每次採用後 5 / 10 / 20 日命中率與均報；成熟採用回饋也會回灌 `threshold-backtest` 候選排名，每組設定保留 `historical_score`、`feedback_adjustment`、成熟樣本數與調整理由。採用後 outcome 也會彙整成 `symbol_adoption_feedback_boost`，直接加入每日雷達 `risk_adjusted_score` 排序，並顯示於控制台候選卡與 CSV 匯出。這先把「今天這批標的是不是只靠門檻剛好卡到」、「下一輪該優先試哪個市場池 + 檔位 + 門檻」、「這組設定採用後有沒有真的改善」與「哪些股票在採用後真的表現好」變成可讀、可執行、可落到股票清單的訊號；控制台可把採用建議套回雷達表單的風格、市場池近似值與 `threshold_offset`，族群型設定會用常見代號建立自訂觀察池，下一輪每日雷達會真的套用該門檻偏移。
- 已新增門檻最佳化下一輪探索佇列：後端 `exploration_queue` 會排除本批已選入的組合，整理未選入但分數較高的歷史推薦與跨市場池 Grid 候選；`candidate_source=exploration` / `--candidate-source exploration` 可直接試算或執行下一批探索組合，控制台也新增「試算探索下一批」與「執行探索第 1 組」，並可顯示剩餘推薦 / Grid 組數、前幾個候選與可複製 CLI 指令，讓分批參數搜尋更接近 vectorbt / QuantConnect 類工具的探索流程。
- 已新增第一版門檻最佳化 walk-forward probe 計畫：批次結果會輸出 `walk_forward_plan`，列出訓練視窗、驗證視窗、下一批探索乾跑與只執行第 1 組 probe 的 CLI 指令；控制台會顯示三步驟流程並可複製每一步指令，先把「訓練 → 驗證 → 等 outcome 成熟」變成日常可執行節奏。
- 已新增第一版 rolling walk-forward 門檻驗證：`threshold-backtest` 會輸出 `walk_forward_windows`，用前段每日雷達 run 找最佳門檻，再放到後段驗證視窗檢查分數、正報酬率與均報；控制台會在分層實跑區上方顯示 Rolling Walk-forward 摘要與最近幾個視窗，避免只看全樣本最佳門檻。
- Rolling walk-forward 已升級成熱區矩陣：`threshold-backtest` 會額外輸出 `walk_forward_heatmap`，把風格 × offset 在 out-of-sample 視窗中的驗證分、正報酬率、視窗數與 rolling 加減權壓成矩陣；控制台會在 Rolling Walk-forward 下方顯示「Rolling 熱區」，快速分辨哪組門檻可加權、哪組需要降權，降低只看全樣本最佳化造成過度擬合的風險。
- Rolling 熱區已可直接套用最佳格：控制台會在 `walk_forward_heatmap.strongest_cell` 存在時顯示「套用最佳格」，將最佳風格與 offset 寫回每日雷達表單，並把 `control_panel_walk_forward_heatmap` adoption metadata 帶到下一輪 radar run，讓 out-of-sample 證據能直接落成可追蹤的參數採用。
- 每日雷達已新增市場狀態分層調整：跑雷達前會依候選池主要族群的近期市場脈絡計算 `market_regime`，在風險收縮時自動切保守風格並提高門檻，在風險擴張時小幅放寬，震盪偏弱時略收緊；結果會同時保留 `requested_*` 與 effective `risk_profile` / `threshold_offset`，sync job metadata 與控制台執行摘要會顯示「市場狀態」與調整理由，讓 regime-aware screener 不只停在研究文件。
- 已將 rolling walk-forward 證據接進推薦排序：`optimization_candidates` 會保留 `walk_forward_adjustment`、`walk_forward_label`、視窗數、正報酬率與驗證均報，通過 rolling 視窗的門檻會小幅加分，驗證偏弱的門檻會降權；控制台推薦設定卡會直接顯示這個調整理由。
- 已將 rolling walk-forward 證據推進到市場池 / 族群層級：每個 rolling 視窗會輸出 `pool_results`，推薦候選會保留 `pool_walk_forward_adjustment`、池別視窗數、正報酬率與驗證均報；同一組門檻在不同市場池或族群的排序會依池別 out-of-sample 表現小幅調整。為避免單一視窗過度影響排序，門檻層級與池別 rolling 證據都至少需要 2 個視窗才會實際加減分，低於門檻時只顯示為觀察中。
- 已新增下一輪池別建議摘要：`threshold-backtest` 會輸出 `pool_recommendation`，從推薦候選中挑出最值得下一輪觀察的市場池或族群池，保留分數、rolling 證據、代表代號與 `run_params`；控制台會在門檻實跑摘要中顯示「優先市場池 / 優先族群池」卡片，可直接套用到雷達表單或複製代表代號，讓池別 out-of-sample 證據不只留在候選列表，而是變成盤前操作入口。
- 已新增標的層級採用回饋操作化：控制台候選卡、比較面板、採用回饋篩選 / 排序、快速預設與 CSV 匯出都會使用 `symbol_adoption_feedback_boost`，並帶出原始 boost、信心權重與信心等級；低樣本會保守調整有效 boost，方便把採用後表現較佳且證據較可靠的股票拉成下一輪檢查清單。比較面板也新增組合判讀，會提示最大族群占比、平均風險與無阻擋比例。
- 已新增每日雷達風險品質層：控制台預設排序改為「風險品質優先」，用 `risk_adjusted_score`、Entry、阻擋標籤、ATR 熱度、風險分數、動作狀態與篩選後族群占比計算保守分數；候選卡與比較面板會顯示「核心候選 / 可小量觀察 / 等待確認 / 先降溫 / 先跳過」風險層與扣分理由，篩選摘要也會顯示平均風險品質、核心候選數與主要族群占比，讓盤前選股更接近專業 watchlist 的風控掃描。每日雷達 JSON、總表 CSV、Preview / Trigger / Risk review lane CSV、每日決策 HTML 與盤前 Brief 也已同步輸出 `risk_quality_score`、`risk_quality_label`、`risk_quality_reason`；watch pool 也會保存風險品質、API 依風險品質優先排序，控制台觀察池表格直接顯示層級與原因，讓匯出報表、通知與可操作標的池保留同一套風控判讀。
- 盤前 Brief 與排程通知已新增「可下手 / 只觀察 / 不碰」行動分級：`ENTRY_READY`、無阻擋且風險品質達標的標的會進「可下手」，等待觸發或仍需補確認的標的進「只觀察」，不建議、阻擋或風險品質偏弱的標的進「不碰」；JSON 會保留 `decision_brief.action_grade`、Markdown Brief 會列出三類數量與代表代號，Telegram / LINE 摘要也會直接顯示同一份分級，讓盤前通知先回答「今天到底要看哪幾檔」。
- 穩定候選已新增風險品質歷史追蹤：每個交易日的每日雷達會先依當日候選池重新計算 `risk_quality_*`，再比較最新、平均與最舊分數，標出「風險品質轉強 / 持平 / 轉弱」。控制台穩定候選卡現在會顯示最新風險品質、平均品質與分數變化，摘要也會統計轉強與轉弱檔數，避免只因連續出現就把風險變差的標的排成優先。
- 已新增盤前首選摘要：控制台會根據目前篩選後的每日雷達清單，挑出 3 檔優先檢查標的，顯示首選分、主要理由與證據缺口，並可一鍵複製代號或套用成自訂觀察池，讓盤前選股從「看完整清單」前進到「先看這幾檔」。
- 已將盤前信心接進配置草稿：研究配置會新增 `premarket_multiplier`，把採用回饋信心、無阻擋、高風險與同族群重複配置轉成小幅加降權，控制台配置卡、review queue CSV 與每日雷達總表 CSV 都會保留調整理由，讓「比較值得看的標的」也能影響研究額度排序。
- 已新增策略升級決策卡：多策略回測完成後，控制台會把 research gate 的 `paper_ready`、`needs_review` 或 `blocked`、警示/阻擋數、關鍵證據與下一步集中顯示，讓研究區更像升級決策台，而不是只剩一份長報告。
- 已新增首頁「候選可信度 Gate」：控制台會把資料健康度、每日雷達部署健康與候選品質總覽合成 `可研究 / 先留意 / 先處理`；若最新雷達同步正常、候選未命中資料缺口，即使全市場核心覆蓋仍待補，也會降成「今天偏觀察」並把全市場修復列為背景待辦，避免冷門缺口持續阻擋已可判讀的日常候選；若候選本身命中日線/法人缺口，才會直接建議先修資料、再重跑每日雷達。

## 四、第一批已落地項目

- 舊 SQLite schema 自動補上基本面與新聞分數欄位，避免 Advisor proposal API 因欄位缺漏失敗。
- 每日雷達卡片增加基本面分數拆解，讓候選標的更容易比較。
- 研究優先序增加分數元件拆解，讓 Entry、穩定度、阻擋、可靠度、風險與交易回饋對排序的影響可追蹤。
- 每日雷達新增正式風險調整分 `risk_adjusted_score`，把 Entry、動作、風險、阻擋、ATR 熱度、族群趨勢與可靠度整合成排序欄位，控制台、比較面板與 CSV 匯出都可追蹤。
- 候選品質總覽與 watch pool 已改成風險調整分優先，避免 Top Quality Picks 和日常觀察池仍被單純 Entry Score 帶偏。
- Top Quality Picks 已改成只取可研究或只觀察標的；資料缺口、不建議進場與阻擋標的仍會顯示於品質分流，但不會進入可複製、可套用的品質池。
- 一鍵候選與每日雷達預設改用精選觀察池，並可切換台灣 50、全市場或自訂觀察池。
- 每日雷達新增正式產業分類優先的族群分散與 ATR 熱度提示，降低清單被單一族群洗版的機率。
- 每日雷達與一鍵候選的風險檔位已真正分成保守、平衡、積極三段，保守型會更嚴格控波動、流動性與族群集中。
- 每日雷達與一鍵候選新增產業輪動分數與族群趨勢列，讓接近分數的標的更偏向近期較強、正在升溫的族群。
- 新增 `tw0050` 與 `tw_mid100` 可同步觀察池：台灣 50 可在控制台由元大 0050 PCF 更新快取與 DB snapshot，中型 100 可由元大 0051 PCF 同步本機清單，也可由最近成交金額自動推估非台灣 50 的流動性前 100 檔。
- 新增 `dynamic_balanced` / `dynamic_liquid` 動態觀察池模式，讓每日雷達可從近期有日線資料且流動性較好的上市 + 上櫃股票中自動取樣，並支援族群分散與不碰清單排除。
- 新增不碰清單管理，讓已知事件風險、長期資料品質差或個人不想碰的標的先被擋下來，並可直接在控制台維護。
- 控制台新增每日雷達互動式篩選器，讓日常決策可以快速切到可進場、觀察中、特定族群、波動正常或無阻擋標的。
- 控制台新增候選命中率追蹤與族群 / 理由 / ATR 熱度聚合，讓每日雷達能回看哪些訊號後續比較有勝率。
- 新增第一版訊號 registry，讓雷達理由、阻擋原因與 outcome 分析共用同一份訊號定義，後續可接策略版本與 paper promotion。
- 控制台新增訊號說明與可靠度表，讓 `TREND_OK`、`ATR_HEAT`、`INDUSTRY_TREND` 等條件能直接對上說明與最近 outcome 表現。
- 每日雷達新增 5 / 10 / 20 多週期 outcome 可靠度加權排序與匯出欄位，讓近期較可靠且跨週期不差的訊號組合在分數接近時微幅往前，近期表現較差的訊號微幅降權。
- 多策略回測新增第一版研究規格、研究驗證報告、OOS 分段驗證、walk-forward 滾動視窗驗證、代表標的籃子敏感度、threshold 參數敏感度與策略比較視圖，讓策略是否適合進 paper 不再只靠單一績效數字判斷。
- live 下單新增委託價格保護：若 preview metadata 帶有 `reference_price`，execution service 會依交易單位套用預設門檻，盤中零股為 3 ticks / 1%，整股為 2 ticks / 0.5%；偏離過大會以 `price_deviation_guard` 拒絕，不會送到 broker。
- live 下單新增第一版交易限制保護：若 preview metadata 帶有漲跌停價，會先阻擋買在漲停、賣在跌停或超出價格區間；控制台建立正式 Preview 時會查本機處置股與注意股資料表，命中時會以 `disposition_attention_guard` 拒絕。注意股可由 Shioaji `api.notice()` 同步到 `attention_stocks`。
- live 賣出單新增部位保護：若 preview metadata 缺少 `position_quantity`、賣出數量大於持股數，或大於 `sellable_quantity` 可賣股數，execution service 會拒絕，不會送到 broker；整股 `Common` 會以 1 張 = 1000 股換算後檢查。
- 控制台會在查詢台股帳戶快照後，依 Preview 股票代號自動帶入目前持股與可賣股數；快照會優先用 Shioaji `Unit.Share` 取得股數，並以券商可賣欄位或 `yd_quantity` 作為基礎可賣股數，再扣除同股票尚未完成的賣出委託，且會標示可賣來源。建立正式 Preview 前也會先重新查詢帳戶快照與券商合約參考價，任一查詢失敗時不建立 Preview，讓賣出單部位與價格偏離保護更容易被正確使用。
- 新增 promotion readiness 摘要：CLI 與控制台 API 可把 observation records 與最近 promotion gate 合併判讀為 `paper_ready`、`small_live_ready` 或 blocked，讓「觀察 -> paper -> 小額 live」有可查詢的證據鏈。
- 正式送單 API 已接上 promotion readiness hard gate：即使前端送出 `promotion_gate_accepted=true`，若後端判定尚未 `small_live_ready`，仍會以 `promotion_readiness_blocked` 拒絕，不送到 broker。
- 新增收盤後對帳摘要：CLI `eod-summary`、API `/api/tw-live/eod-summary` 與控制台交易驗收頁會彙整今日 execution、拒單/錯誤、待決策 Preview、最近 promotion gate、最近 reconciliation 與資料健康度，讓 paper/live 營運差異與對帳缺口更容易被日常看到。
- live order 已要求 `strategy_name`、`strategy_version` 與 `signal_id`，且正式 approve 會檢查這三個追蹤欄位必須與原始 Preview 一致；控制台手動 Preview 也可填策略/訊號欄位，從每日雷達帶入時會自動標成 `daily_radar` 來源。
- live order 新增整體投組曝險 hard gate：若 preview metadata 帶有 `portfolio_equity` 與 `current_portfolio_exposure`，execution service 與 Shioaji gateway 會各自檢查本次委託後是否超過 `max_portfolio_exposure_pct`，超過時以 `max_portfolio_exposure` 拒絕，並保留單一持股與當日損失上限檢查。
- 控制台會從帳戶快照估算單檔市值、投組權益、目前曝險、未成交買單曝險、已實現損益與未實現損益，建立正式 Preview 時自動寫入 portfolio risk metadata、PnL 來源 metadata 與整股/零股換算後的 `share_quantity`，讓投組曝險與日損 hard gate 不只停在手動欄位；Preview 區塊也會用風控摘要卡同步呈現價格保護、可賣股數、單檔上限、投組曝險與日損上限狀態。
- 每日雷達排程已補部署健康檢查入口，控制台首頁可看到排程是否漏跑、候選數是否異常偏低，以及最新 sync job 是否失敗或部分完成。

## 五、目前仍未完成的優化

1. 全市場資料覆蓋仍要持續修復：TWSE 普通股日線與法人覆蓋率還沒完全收斂，分點聚合仍屬 FinMind sponsor 加值資料，需決定升級資料源或改接官方公開替代來源。
2. 指數成分股自動化仍未完整：目前支援授權下載 URL、CSV/TXT/XLSX 匯入與本機快取，但臺灣指數公司完整成分股免費公開 API 尚未完成，仍需合法資料源策略。
3. 觸發觀察監控的盤中價仍可強化：目前已可選用 Shioaji snapshot 覆蓋雷達快照價格，也可用 `shioaji_streaming` 短時間訂閱 tick / bidask 並在未收到串流報價時 fallback snapshot；兩者都會套用報價 freshness gate。串流路徑已加入 bounded quote buffer、訂閱事件記錄、訂閱速率控制與第一版 `quote_manager` 生命週期狀態，會記錄連線、斷線、重連、重訂閱與心跳時間；控制台也可直接切換雷達快照、Shioaji snapshot、Shioaji streaming、串流秒數、報價有效秒數與正式/模擬 Shioaji，並顯示連線/重連/重訂閱狀態。第一版 process-local 盤中行情服務也已能手動啟動/停止共用 quote manager、檢查 market session、查詢狀態與 snapshot，非盤中預設不啟動，並會輸出 `health` 判讀，將斷線、重新連線中、缺訂閱、尚未收到報價與非交易時段執行轉成問題清單與下一步，也已接入控制台「營運總檢」作為第五個元件，並把最近 health 寫成 `data/cache/intraday_quote_service_status.json` 供 `intraday-quote-service-status` CLI 跨程序檢查 freshness 與異常。CLI、`run-scheduler`、launchd、systemd 或 Docker Compose 都可固定通知，通知冷卻與重複提醒抑制也已完成。後續還應把 quote manager 推進到真正跨程序或部署級的長時間行情 daemon、補服務存活監控與盤後健康回顧，讓盯盤監控更接近即時交易工作台。
   - 已新增 `run-intraday-quote-service` 長時間訂閱入口，可持續更新同一份 health snapshot；systemd 與 Docker Compose 產生器也可輸出長時間行情服務與 healthcheck。狀態檔也會累積 session summary，`intraday-quote-service-review` 可在盤後檢查 snapshot freshness、heartbeat、最差 health、報價覆蓋、tick/五檔證據與最後連線狀態，輸出可歸檔的「盤後正常 / 盤後留意 / 盤後需處理」回顧；控制台「營運總檢」的盤中行情服務卡也會顯示這份回顧的核心證據，`ops-readiness --include-intraday-quote-review` 也可把它併入 VM/cron 總檢。`write-systemd-units --health-use-ops-readiness --health-include-intraday-quote-review` 與 `write-docker-compose --health-use-ops-readiness --health-include-intraday-quote-review` 也能直接產生納入盤後行情回顧的健康檢查；`deploy/systemd/README.md` 與 `deploy/docker/README.md` 已補第一版盤後總檢與盤中行情服務重啟 runbook。後續仍需做實機重啟策略驗證與更完整的正式營運手冊。
4. 通知通道客製仍可再實戰化：盤前 Brief 去重與短版觸發摘要已落地，`run-daily-radar-job --notify-style premarket|intraday|postmarket`、`run-scheduler --radar-notify-style` 與 launchd/systemd/Docker 產生器也已支援盤前完整 Brief、盤中精簡快訊與盤後回顧三種訊息密度；但 Telegram / LINE 的長度限制、個人化版型、不同收件人的摘要層級與更細的盤中/盤後語氣仍可再調整。
5. 雷達門檻尚未做真正大規模跨市場池最佳化：目前已有基於最新雷達結果的多門檻 / 多風險檔位輕量掃描，也能回看歷史雷達 outcome 做跨週期、上市 / 上櫃、風格、族群分層、結構化風格 × offset 熱區、候選設定排名與批次比較矩陣；批次乾跑也能主動產生第一版 `市場池 × 風險檔位 × offset` grid 候選，先把研究就緒池、動態池、台灣 50 與中型 100 放進同一張比較表，可指定 grid 來源小批次實跑最高分候選，也可用 exploration 來源分批跑下一批未試組合，並已輸出第一版 walk-forward probe 三步驟計畫與 rolling 驗證視窗。但還未做到類似 vectorbt 的多市場池、多期間完整重算雷達、大批次平行最佳化與平行化滾動視窗參數搜尋。
6. 策略治理仍是第一版：`StrategyResearchSpec`、OOS、walk-forward 與敏感度報告已落地，但策略版本、資料版本、paper promotion、live feedback 與成本模型校準仍需要更完整的 production 紀錄鏈。
7. 可靠度與配置校準樣本仍偏早期：同日重跑已去重，但成熟交易日樣本還需要累積，並應依多空環境、族群、波動熱度與流動性分 regime 驗證。
8. 介面工程仍需要拆分與設計整理：控制台已能完成日常流程，但 HTML/CSS/JS 仍偏大型單檔，後續應拆成元件、補上可測的前端狀態、行動版檢查與 accessibility 細節。
9. live 交易邊界情境還可強化：已有限價、部位、可賣、投組曝險、promotion readiness 等 hard gate，但當沖、融資融券、特殊交割、券商即時可賣欄位與收盤後對帳閉環仍要補齊。
10. 部署與通知仍需實戰化：launchd/systemd/Docker 產生器與 Telegram/LINE 通知已存在，但尚未把實際 VM 監控、秘密管理、失敗重試、健康告警與盤後回顧報表完整串成正式營運手冊。

## 六、參考來源

- QuantConnect LEAN: https://github.com/QuantConnect/Lean
- Microsoft Qlib: https://github.com/microsoft/qlib
- OpenBB Workspace: https://openbb.co/products/workspace
- Freqtrade: https://www.freqtrade.io/en/stable/
- vectorbt: https://vectorbt.dev/
- Jesse: https://docs.jesse.trade/
- QuantRocket Docs: https://www.quantrocket.com/docs/
- Backtrader: https://www.backtrader.com/
- TradingView Screener: https://www.tradingview.com/screener/
- Finviz: https://finviz.com/
- 元大台灣中型 100 ETF（0051）PCF 來源頁：https://www.yuantaetfs.com/product/detail/0051/ratio
- 元大台灣 50 ETF（0050）PCF 來源頁：https://www.yuantaetfs.com/product/detail/0050/ratio
- TWSE 每日收盤行情（MI_INDEX）：https://www.twse.com.tw/zh/trading/historical/mi-index.html
- 臺灣指數公司指數資訊銷售：https://www.taiwanindex.com.tw/promotion_and_service/index_data_selling
- 臺灣證券交易所 Data E-Shop TIP 專區：https://eshop.twse.com.tw/zh/category/main/49
