# quant-trade

台股量化研究與日常決策雷達系統，涵蓋「資料 → 策略 → 回測 → 每日雷達 → 盤前 Brief → 驗收」完整流程。實盤交易（Shioaji）保留於專案中，但目前主線是研究、紙上交易與人工決策輔助；正式下單必須經過明確 gate，不會把你一腳推上火線。

## 先看這裡：每天怎麼跑

如果你只是想每天跑「每日決策雷達」，不用先讀完整 README。照這個順序就好：

1. 啟動服務：

```bash
.venv/bin/python run_web.py --host 127.0.0.1 --port 8766
```

2. 打開 [本機控制台：指揮台](http://127.0.0.1:8766/#dashboard)。
3. 在畫面中按 `重新計算雷達`。
4. 看 `候選可信度 Gate`、`今日決策結論`、`日常決策雷達驗收`。
5. 每日詳細步驟看 [每日決策雷達 SOP（含圖）](docs/daily_radar_sop.md)。

![重新計算雷達按鈕](docs/assets/daily-radar-recalculate-button.png)

常用內部連結：

- [本機控制台：指揮台](http://127.0.0.1:8766/#dashboard)
- [本機控制台：AI 決策](http://127.0.0.1:8766/#pipeline)
- [本機控制台：交易驗收](http://127.0.0.1:8766/#shioaji)
- [每日決策雷達 SOP（含圖）](docs/daily_radar_sop.md)
- [Phase 1 驗收說明](docs/decision_radar_phase1_acceptance.md)
- [Phase 1 測試紀錄](docs/decision_radar_phase1_test_journal.md)

## 功能重點

- 多資料來源（YFinance / FinMind / Shioaji*）
- 可擴充策略架構，策略輸出統一為：
  - `position`：目標持倉（-1/0/1）
  - `signal`：進出場事件（`position.diff()`）
- 多策略波段系統（A/B/C）：
  - A: 動能趨勢（MA20/MA60 + Donchian + 量能）
  - B: 均值回歸（RSI + Bollinger 反彈 + 長期濾網）
  - C: 籌碼跟隨（外資/投信 5 日買超 + 集中度 proxy）
- 回測引擎：支援 T+1 開盤成交模擬、交易成本（手續費、交易稅、滑價）
- 共用風控：停損、移動停利、時間出場
- 報表：產生 HTML 互動報表與 CSV 原始資料
- 每日決策雷達：產生候選分級、風控欄位、盤前 Brief 與 Phase 1 驗收報告
- Shioaji AI 協作中心：同步官方 AI 文件、提示詞模板、安裝指引與常用程式片段
- 風險管理與即時交易模組（本期不展開）

## 專案結構

```
quant-trade/
├── backtest/             # 回測引擎
├── broker/               # 券商介面（Paper / Shioaji）
├── config/               # 全域設定
├── data/                 # 資料處理與下載
├── strategies/           # 策略（含 strategies/multi 多策略系統）
├── live_trading/         # 即時交易（保留）
├── reports/              # 報表輸出
├── docs/shioaji/          # Shioaji AI 文件快取
├── prompts/               # AI 提示詞模板
├── tools/                 # 工具腳本（同步 / 檢查）
├── tests/                # 測試
├── main.py               # CLI 入口
├── swing_analysis.py     # 波段分析報表
├── ai_assistant_dashboard.py # AI 協作中心 HTML 產生器
└── requirements.txt      # 依賴
```

## 附錄：完整建置 SOP

這段是第一次接手專案、或要重建本機環境時的主流程。日常操作請優先看 [每日決策雷達 SOP（含圖）](docs/daily_radar_sop.md)；只有要重建環境、修資料或部署排程時，才需要回來讀這段。

### 0) 先確認安全邊界

1. 預設不要啟用實盤下單。
2. `.env` 不要提交到 Git。
3. 任何與 Shioaji 相關的實盤功能，都必須先確認 `SHIOAJI_ENABLE_LIVE_ORDERS` 不是 `1`。
4. Phase 1 的目標是「能不能穩定產生可信的日常決策雷達」，不是自動交易。

### 1) 建立 Python 環境

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-dev.txt
```

如果本機已經有 `.venv`，可以只重新安裝依賴：

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-dev.txt
```

### 2) 準備環境變數

```bash
cp .env.example .env
```

最小可跑設定建議如下：

```env
DATABASE_URL=sqlite:///data/quant_trade.db
CONTROL_PANEL_TOKEN=請換成你自己的長隨機字串
FINMIND_API_KEY=你的 FinMind token
SHIOAJI_ENABLE_LIVE_ORDERS=
CODEX_ADVISOR_ENABLED=
```

補充說明：

- `FINMIND_API_KEY`：用於台股資料同步與補洞。
- `CONTROL_PANEL_TOKEN`：控制台 API token，建議使用長隨機字串。
- `SHIOAJI_*`：只有在需要券商查詢、即時報價或未來紙上交易整合時才需要。
- `CODEX_ADVISOR_ENABLED`：只有在明確要啟用顧問協作流程時才設定。

### 3) 初始化資料庫

```bash
.venv/bin/python -m app.cli init-db
```

如果資料庫已存在，仍可重跑此指令補齊 schema。若後續看到欄位缺漏或 migration 相關錯誤，先回到這一步確認。

### 4) 同步股票清單與檢查資料健康

```bash
.venv/bin/python -m app.cli sync-twse-tpex-universe
.venv/bin/python -m app.cli data-health-overview
```

如果資料健康檢查顯示 coverage 不足，可先試跑修補規劃：

```bash
.venv/bin/python -m app.cli repair-market-data-coverage --json
```

確認規劃合理後，再執行實際修補：

```bash
.venv/bin/python -m app.cli repair-market-data-coverage --execute --batches 1
```

### 5) 執行每日決策雷達

```bash
.venv/bin/python -m app.cli run-daily-radar-job --universe curated_watchlist --risk-profile balanced --notify none
```

執行後會產生每日雷達結果，通常包含候選分級、風控欄位、品質警示、盤前 Brief 與報表輸出。初期建議先用 `--notify none`，等輸出品質穩定後再串 Slack、Webhook 或 Email。

### 6) 檢查營運與 Phase 1 驗收 gate

```bash
.venv/bin/python -m app.cli ops-readiness --target-phase paper --include-intraday-quote-review
.venv/bin/python -m app.cli decision-radar-phase1-acceptance
```

也可以輸出 JSON 方便自動化或控制台讀取：

```bash
.venv/bin/python -m app.cli decision-radar-phase1-acceptance --json
```

判讀原則：

- `status=ok`：系統健康、資料品質與雷達輸出達到 Phase 1 測試門檻。
- `ready_for_user_testing=true`：可以進入使用者測試。
- `requires_user_confirmation=true`：仍需要你完成測試紀錄並明確回覆「Phase 1 通過」。
- `blocking_reasons` 有內容時，先照原因修正，不要推進到 Phase 2。

詳細驗收文件：

- [docs/decision_radar_phase1_acceptance.md](docs/decision_radar_phase1_acceptance.md)
- [docs/decision_radar_phase1_test_journal.md](docs/decision_radar_phase1_test_journal.md)

### 7) 啟動 Web 控制台

```bash
.venv/bin/python run_web.py --host 127.0.0.1 --port 8766
```

開啟：

```text
http://127.0.0.1:8766
```

控制台建議檢查順序：

1. `營運總檢`：確認資料、排程、通知與風控狀態。
2. `日常決策雷達驗收`：查看 Phase 1 gate、測試紀錄與使用者通過條件。
3. `今日決策雷達`：看候選股票、action grade、risk flags 與不確定性說明。
4. `候選可信度 Gate`：檢查候選是否因資料品質或風險被降級。
5. `觸發觀察監控`：需要盤中觀察時再啟用。

若控制台 API 回傳 401，請確認請求有帶 `CONTROL_PANEL_TOKEN` 對應的 token。

### 8) 每日操作 SOP

盤前：

```bash
.venv/bin/python -m app.cli run-daily-radar-job --universe curated_watchlist --risk-profile balanced --notify none
.venv/bin/python -m app.cli decision-radar-phase1-acceptance
```

盤前判讀重點：

- 先看 `action grade`，再看每檔的風控理由。
- `可下手` 不等於自動下單，只代表符合當前規則的候選。
- `只觀察` 或 `不碰` 是有效輸出，不是失敗。
- 若全部都是 `不碰`，但理由清楚、資料健康正常，這通常代表系統在保守保護你。

盤中需要監控觸發條件時：

```bash
.venv/bin/python -m app.cli daily-radar-trigger-monitor --price-source shioaji_snapshot --notify none
```

盤後：

```bash
.venv/bin/python -m app.cli eod-summary
.venv/bin/python -m app.cli ops-readiness --target-phase paper --include-intraday-quote-review
```

若要做帳務或部位對帳，可使用：

```bash
.venv/bin/python -m app.cli reconcile \
  --expected-cash 1000000 \
  --actual-cash 1000000 \
  --expected-positions 2330:1000 \
  --actual-positions 2330:1000
```

### 9) Phase 1 使用者測試 SOP

1. 連續 2 到 3 個交易日執行每日雷達。
2. 每天填寫 [docs/decision_radar_phase1_test_journal.md](docs/decision_radar_phase1_test_journal.md)。
3. 檢查候選分級是否可理解、風險理由是否足夠、盤前 Brief 是否能支援人工決策。
4. 確認沒有 blocking issue 後，明確回覆「Phase 1 通過」。
5. 在你明確確認前，Phase 2 不應啟動。

### 10) 常見問題排查

資料庫 schema 或欄位缺漏：

```bash
.venv/bin/python -m app.cli init-db
```

每日雷達沒有最新資料：

```bash
.venv/bin/python -m app.cli data-health-overview
.venv/bin/python -m app.cli repair-market-data-coverage --json
```

Phase 1 驗收顯示 `risk_quality_present=false`：

```bash
.venv/bin/python -m app.cli run-daily-radar-job --universe curated_watchlist --risk-profile balanced --notify none
.venv/bin/python -m app.cli decision-radar-phase1-acceptance
```

`ops-readiness` 顯示 danger：

1. 先看輸出的 `next_step`。
2. 若是資料健康問題，回到資料同步與修補流程。
3. 若是通知或 broker gate 問題，先維持 `--notify none` 與非實盤模式。
4. 若是盤後對帳問題，先跑 `eod-summary` 與 `reconcile`。

控制台無法登入或 API 被拒：

1. 確認 `.env` 裡的 `CONTROL_PANEL_TOKEN`。
2. 重新啟動 `run_web.py`。
3. 確認前端請求使用相同 token。

### 11) 驗證與測試

針對 Phase 1 與控制台安全檢查：

```bash
.venv/bin/python -m pytest tests/test_decision_radar_phase1_acceptance.py -q
.venv/bin/python -m pytest tests/test_control_panel_security.py::test_radar_phase1_acceptance_api_returns_user_test_gate -q
```

完整測試：

```bash
.venv/bin/python -m pytest -q
```

文件或程式變更後，至少建議跑一次相關測試。這一步雖然不刺激，但很省未來的心跳。

## 快速開始（回測與報表補充入口）

### 1) 建立環境

```bash
python -m venv venv
source venv/bin/activate
```

### 2) 安裝依賴

```bash
pip install -r requirements.txt
```

> 注意：`TA-Lib` 可能需要系統層安裝（不易安裝時可先略過，特徵工程會自動降級為純 pandas 版本）。
> 若要使用 Shioaji（實盤/模擬），請額外安裝 `shioaji` 並設定 `.env`。

### 3) 回測

```bash
python main.py --mode backtest --symbol 2330.TW
```

可選參數：
- `--start-date YYYY-MM-DD`
- `--end-date YYYY-MM-DD`
- `--short-window` / `--long-window`

**回測資料來源**：
- 會優先讀取本地 CSV（`data/<symbol>.csv` 或 `data/stock_data/<symbol>_1d.csv`）
- 若本地不存在，會自動下載

### 4) 產生波段分析報表

```bash
python main.py --mode report
# 或
python swing_analysis.py
```

輸出位置：`./reports/`

### 5) 產生短期投資 Dashboard

```bash
python main.py --mode dashboard
```

輸出位置：
- `reports/short_term_dashboard_<timestamp>.html`
- `reports/short_term_top20_<timestamp>.csv`

資料來源：
- TWSE 上市公司清單（公開 CSV）
- TWSE 當日成交資料（STOCK_DAY_ALL）

你也可以用一鍵腳本：

```bash
./run_dashboard.sh
```

### 6) 產生 Shioaji AI 協作中心

```bash
python main.py --mode shioaji-ai
```

輸出位置：
- `reports/shioaji_ai_dashboard.html`
- `docs/shioaji/llms.txt`
- `docs/shioaji/llms-full.txt`（不進 git）

若要以 Web 方式查看（FastAPI）：

```bash
python run_web.py --reload
```

### 7) Web 控制台（報表 + Shioaji 測試）

```bash
python run_web.py --reload
```

或使用一鍵腳本：

```bash
./run_web.sh
```

控制台可調整參數：
- 多策略工作台：策略開關/權重/門檻、風控參數、交易成本、回測期間
- 今日決策雷達：重新計算雷達、匯出最新投資清單 HTML/CSV/JSON、查看完整篩選清單
- 短期投資 Dashboard：Top N / 流動性預篩數量 / 回溯天數
- 波段報表：開始日 / 結束日 / 回溯天數
- AI 協作中心：是否強制同步文件
- AI 提案工作台：TradingAdvisor 提案、下單 Preview、人工確認、婉拒與 Advisor 回測
- Shioaji 測試中心：登入測試 / 證券下單測試 / 期貨下單測試 / 一鍵模擬整套測試
- 正式環境切換檢核：檢查 `production permission`、帳戶 `signed` 狀態、CA 設定

控制台會顯示後端心跳與 Log，執行紀錄同時寫入：
- `reports/control_panel.log`

若要以 Python 直接啟動（適合 IDE Run/Stop）：

```bash
python run_web.py
python run_web.py --reload
python run_web.py --host 0.0.0.0 --port 8080
.venv/bin/python run_web.py --host 127.0.0.1 --port 8766
```

若帳戶查詢無回應，請避免同時點多個帳戶查詢（帳務 API 需要序列化），可先看 `reports/control_panel.log` 追蹤進度。

### 7-1) 外部指數同步

控制台的一鍵設定區可執行「同步外部指數（TWSE/TPEx 45 日）」，會讀取 TWSE `MI_INDEX` 的產業分類價格指數、TPEx 官方 `statistics/idx` 產業分類股價指數月報 XLSX，以及 TPEx 官方 `indexInfo/inx` 的櫃買整體指數，寫入 `industry_index_bars`。市場資料同步工具箱也可勾選「外部市場/產業指數」一起同步。首頁資料健康區會顯示市場背景覆蓋度，拆成 TWSE 官方產業價格指數、TPEX 官方產業股價指數、TPEX 本機同產業等權背景、TPEx 櫃買市場保底指數，也會顯示 `index:<key>` 指數成分股快取組數、檔數與最後更新時間，方便判斷族群分數目前使用的是官方資料、等權替代、市場保底，或正式指數池快取。

CLI 可手動補資料：

```bash
.venv/bin/python -m app.cli sync-market-bundle --start-date 2026-05-01 --end-date 2026-06-25 --no-include-bars --no-include-chip --no-include-broker-agg --no-include-disposition --include-industry-indices
```

跨族群壓力測試會優先使用外部 TWSE 產業指數背景；若驗證範圍明確是 TPEX，會先使用 TPEx 官方產業分類股價指數，資料不足時再退回本機同產業上櫃股票日線等權背景，最後才退回 TPEx 櫃買整體指數作為上櫃市場保底背景。

### 7-2) 多策略回測資料欄位

輸入 DataFrame 至少需要：
- `Open`, `High`, `Low`, `Close`, `Volume`

策略 C（台股籌碼）會額外使用：
- `Foreign_Net_Buy`
- `InvestmentTrust_Net_Buy`
- `Chip_Concentration_Proxy`

若你沒有自行提供籌碼欄位，系統會嘗試透過 FinMind 載入（失敗時自動降級，不阻斷策略 A/B）。

### 7-3) 回測匯出 API（CSV/JSON）

可直接呼叫 API 進行回測並輸出 artifacts 到 `reports/`：

`POST /api/strategy/backtest/export`

請求 body 可沿用控制台的多策略參數（`symbol`, `market`, `enabled`, `weights`, `threshold`, `risk_config`, `backtest_config`）。

回應會包含：
- `export.files`: 匯出的檔名（summary JSON / trades CSV / signals CSV / equity CSV）
- `export.urls`: 可直接開啟下載的 `/reports/...` 路徑
- `backtest.research_spec`: 本次策略研究規格，包含假說、資料需求、策略參數、風控、成交假設、驗證計畫與升級條件
- `backtest.validation_report`: 第一版研究驗證報告，包含 paper readiness、阻擋項目、交易樣本、70/30 OOS 分段驗證、walk-forward 滾動視窗驗證、代表標的籃子敏感度、跨族群壓力、threshold 鄰近值參數敏感度、成本占比、成交拒絕率、資料品質與權益曲線後段穩定度檢查；控制台回測結果上方會先顯示「策略升級決策」卡，把 `paper_ready` / `needs_review` / `blocked`、警示/阻擋數與下一步拉成一頁式判讀。
- `backtest.strategy_comparison`: 策略比較摘要，包含 baseline、threshold 鄰近值比較、最佳 threshold 候選與跨族群壓力排序，控制台會顯示成比較表
- `backtest.parameter_robustness`: 參數穩健性摘要，會把 threshold 鄰近值的正報酬比例、負報酬/零交易變體、最差報酬、baseline 與最佳值差距壓成穩健分數與下一步建議，控制台會先顯示「穩健區間 / 可觀察 / 參數敏感 / 參數脆弱」判讀，再列 threshold 明細。

研究驗證可選填：
- `validation_config.cross_symbol_symbols`: 自訂跨標的驗證籃子，可用陣列或逗號分隔字串。
- `validation_config.max_cross_symbols`: 跨標的掃描上限。
- `validation_config.min_cross_symbol_rows`: 單檔標的最少日線筆數；不足會標為 skipped，不會讓主回測失敗。
- `validation_config.cross_industry_per_group`: 自動分層抽樣時每個產業最多取幾檔。
- `validation_config.cross_symbol_lookback_days`: 自動分層抽樣的流動性排序回看天數。
- 跨標的敏感度的族群判讀會優先使用 `instruments.industry` 正式產業分類，缺資料時才回退到股票代號分類；結果會顯示正式產業 / 代號分類的來源統計。
- 跨族群壓力會依各族群平均報酬、最差報酬與可交易比例標記 `resilient`、`mixed`、`fragile`，並納入 research gate。
- 若資料庫有足夠 `industry_index_bars`，跨族群壓力會優先附上外部指數背景：TWSE 使用產業分類價格指數；TPEX-only 驗證會先用 TPEx 官方產業分類股價指數，再退回本機同產業等權族群背景，資料不足時才退回 TPEx 櫃買整體指數。

### 7-4) 最新每日雷達清單匯出

控制台首頁的「匯出 HTML/CSV/JSON」會呼叫：

`POST /api/radar/latest/export-summary`

回應會包含：
- `export.files.html_summary`: 每日決策摘要 HTML，包含配置診斷、行動線摘要、門檻穩健性、盤前交接、review queue、提醒計畫與三份 watchlist CSV 連結。
- `export.files.decision_brief_md`: 每日雷達盤前 Brief Markdown，濃縮候選品質判讀、交易回饋、門檻穩健性、主線、交接摘要、行動線、提醒計畫與代表代號，方便貼到筆記、Notion 或盤前檢查流程。
- `export.files.csv`: 最新每日雷達 CSV，欄位包含 Entry Score、風險調整分、風險分數、族群、ATR 熱度、動作標籤、投資行動分類、分散首選排名、研究配置草稿金額、配置 review lane、行動線摘要與三類檔數、盤前交接、提醒規則、檢查清單、校準 multiplier、理由與阻擋標籤。
- `export.files.allocation_review_preview_csv` / `allocation_review_trigger_csv` / `allocation_review_risk_csv`: 三條 review queue 的獨立 watchlist CSV，包含價格、Entry/風險調整/Risk 分數、資料品質、提醒規則、檢查清單、理由與阻擋標籤，方便直接拿去建立 Preview、設定觸發觀察或處理風險清單。
- `export.files.allocation_review_preview_symbols_txt` / `allocation_review_trigger_symbols_txt` / `allocation_review_risk_symbols_txt`: 三條 review queue 的純代號清單，一行一個股票代號，方便貼到外部 watchlist 或自訂腳本。
- `export.files.json`: 最新每日雷達完整 JSON，包含 `quality_summary` 候選品質摘要、`threshold_sweep` 門檻穩健性掃描、`trading_feedback` 交易回饋摘要、`decision_brief` 結構化盤前摘要、`investment_actions` 三段式行動分類、`action_queue` 動作佇列、`diversified_shortlist` 分散首選、`allocation_plan` 研究配置草稿、`allocation_diagnostics` 配置診斷與 `review_queue` 行動線摘要、`allocation_outcomes` 配置驗證與 `allocation_calibration` 配置校準，適合丟給後續分析、自訂篩選腳本或通知整合。
- `data.decision_brief`: 同一份結構化盤前摘要也會直接放在 API 回應裡，通知、筆記或自訂整合不必再讀完整 JSON artifact 才能取得候選品質、交易回饋、門檻穩健性、主線、交接摘要與三條行動線。
- `export.urls`: 對應 `/reports/...` 下載路徑。

控制台的匯出結果連結會用「每日決策 HTML」「盤前 Brief MD」「完整雷達 CSV」「Preview 清單 CSV」「Preview 代號 TXT」「觸發觀察 CSV」「風險處理 CSV」等可讀名稱顯示，方便從畫面直接打開對應 artifact。

控制台「今日決策雷達」也可直接預覽盤前 Brief 通知文字、複製通知文字，並用 Telegram、LINE 或兩者做測試送出；同一面板也能把 review queue 的提醒規則整理成短版「提醒檢查」訊息，供盤中盯價格、資料 freshness、阻擋解除與 Preview gate。`WATCH_WAIT_TRIGGER` 的觸發條件會用 `Entry>=74 / 突破87.06 / 回測84.48` 這類短句呈現，避免盤前 Brief 同時重複完整提醒規則與盯盤備註；完整盯盤備註仍保留在 CSV/JSON。面板會先顯示 Telegram / LINE 是否已設定，以及缺少的環境變數。預覽、複製與提醒檢查不會送出外部通知，測試送出或送出提醒才會呼叫已設定的通知通道。

每日雷達匯出也會產生 `threshold_sweep` 門檻穩健性掃描：用同一批候選清單模擬積極 / 平衡 / 保守風險檔位與 ±2 / ±4 分門檻偏移，標出穩定候選、穩定可進場標的、門檻邊緣標的與下一步。JSON、盤前 Brief、每日決策 HTML 與通知摘要都會帶出判讀，避免候選池只因單一門檻剛好卡到就被誤當高信心標的。控制台也提供 `GET /api/radar/threshold-sweep` 與「今日決策雷達」門檻穩健性卡片，可直接在主畫面查看穩定標的、門檻邊緣與不同門檻偏移下的可行動檔數；`GET /api/radar/threshold-backtest` 則會回看最近多輪雷達與 5 / 10 / 20 根日線 outcome，輸出最佳門檻、基準門檻、跨週期表現、上市 / 上櫃、風格、族群分層、結構化 `threshold_heatmap` 風格 × offset 門檻熱區，以及 `recommended_action` 採用判讀與 `recommended_config` / `optimization_candidates` 候選設定排名；熱區會標出基準、最佳與穩定區格子，讓介面能直接顯示哪個門檻組合較可靠。每個候選都會帶 `run_params`，也會彙整成 `optimization_plan` 批次重跑計畫，列出可重跑的 API payload 與 CLI command；`POST /api/radar/threshold-optimization-runs` 與 CLI `run-daily-radar-threshold-optimization` 可先乾跑前幾組，也可用 `execute=true` / `--execute` 小批次執行推薦設定並寫入新的每日雷達 run。CLI/API 也可用 `grid_universes` / `--grid-universes`、`grid_profiles` / `--grid-profiles`、`grid_offsets` / `--grid-offsets=-2,0,2` 額外產生跨市場池 grid 候選，讓乾跑不只比較歷史推薦，也能主動比較研究就緒池、動態池、台灣 50 或中型 100 等市場池；`candidate_source=grid` / `--candidate-source grid` 會只選 grid 最高分候選執行，控制台也提供「執行 Grid 第 1 組」安全入口。批次結果會再依歷史排名分、重跑後可進場/觀察檔數、降級與錯誤狀態產生 `decision_score`、`decision_label`、`recommended_run` 與 `optimization_grid`，並依市場池/族群、風格與 offset 彙總最佳分數、ENTRY/觀察檔數與代表代號，讓控制台先顯示哪個維度值得優先重跑；每組也保留 `top_items` / `top_symbols` 代表標的，讓控制台直接顯示哪組設定與哪些股票較值得下一輪人工檢查。建議設定的代表代號也可一鍵複製或套用成自訂觀察池，接續重跑每日雷達。控制台可把採用建議套回雷達表單的風格、市場池近似值與 `threshold_offset`，族群型設定會用常見代號建立自訂觀察池，下一輪每日雷達會真的套用該門檻偏移；`GET /api/radar/threshold-adoptions` 會回看採用後的 radar run 與成熟 outcome，控制台「採用追蹤」可直接看每次採用後 5 / 10 / 20 日命中率與均報。成熟採用回饋也會回灌 `threshold-backtest` 的候選分數：每組設定保留 `historical_score`、`feedback_adjustment` 與調整理由，讓表現好的設定加權、偏弱的設定降權；同一批採用後 outcome 也會產生 `symbol_adoption_feedback_boost` 回灌每日雷達排序，控制台候選卡與 CSV 匯出都會顯示標的層級採用回饋。CLI / scheduler / launchd / systemd / Docker 也支援 `--threshold-offset` 或 `--radar-threshold-offset`，讓門檻調整不只靠今天這批資料。

標的層級採用回饋也可在控制台候選卡、比較面板、採用回饋篩選 / 排序、快速預設與 CSV 匯出中使用，方便把「採用後真的表現好」的股票拉成下一輪檢查清單。採用回饋會依成熟樣本數與採用 run 數輸出信心權重與信心等級，低樣本會保守調整有效加分，避免少量 outcome 過度推高排序。

控制台市場池選擇器會先用 `/api/universe/preview` 做「市場池驗池」：顯示來源、fallback、可用檔數、近期日線 freshness、不碰清單命中、產業分布與分類來源。`research_ready` 會先要求足夠歷史日線、近期日線、近期法人資料，再依成交金額與產業分散取樣；`dynamic_balanced` 在正式產業分類不足時則會用台股代號前綴 proxy 做分散取樣，並標出 proxy 佔比，方便在執行一鍵候選或每日雷達前先確認這批標的池是否可靠。

控制台也會呼叫 `GET /api/radar/changes` 比較最新與前一輪每日雷達，顯示新進、連續出現、掉出、轉強與轉弱標的，方便把真正變動的候選池從每日雜訊中分離出來；`GET /api/radar/stability` 會彙整最近多輪雷達，找出連續出現、分數較穩且最新仍在清單內的穩定候選；`GET /api/radar/research-priority` 會把 Entry、穩定度、跨日變化、可靠度與阻擋整合成研究優先序，並直接提示下一步是建立 Preview、設觸發觀察、清交易回饋、處理阻擋或先略過，摘要也會聚合「可建 Preview / 觸發觀察 / 待處理」檔數、標示最大族群集中占比，並拉出跨族群研究首選，供人工先打開最值得檢查又不過度集中的幾檔，控制台也可直接複製優先序代號、下載含交易回饋欄位與分散排名的優先序 CSV，或套用成自訂觀察池；`GET /api/radar/allocation-plan` 會用保守研究曝險、單檔上限與族群上限，把優先研究標的轉成研究配置草稿，包含建議金額、估計股數與下一步提示，並套用成熟 outcome 樣本產生的小幅配置校準；回應中的 `allocation_diagnostics` 會標示預算使用率、最大族群占比、進場/觀察檔數、跳過檔數、配置警示與下一步，且 `review_queue` 會把標的分成「可先建立 Preview」「設觸發觀察」「先處理風險」三條行動線，每條行動線會附提醒計畫，每檔標的也會附提醒規則與檢查清單，並用 `handoff` 摘要標出盤前主線、下一步與代表代號；控制台會顯示行動線摘要與目前主線，可直接複製各行動線代號、下載單一行動線 CSV，也可一鍵套用成自訂觀察池，匯出 CSV 與單一行動線 CSV 都會保留 review lane 與交易回饋風險欄位，讓配置草稿多一層 portfolio-level 風險判讀與可操作 watchlist。`calibration_profile=off|conservative|standard|aggressive` 可控制歷史證據對今日配置的影響力，控制台預設 `standard` 並可一鍵切換。`GET /api/radar/allocation-outcomes` 會用已成熟的歷史雷達 run 回測配置草稿，和單純照排名挑前幾檔的基準比較，顯示平均報酬、正報酬率與配置 alpha；`GET /api/radar/allocation-calibration` 會列出目前加權與降權最明顯的族群、動作、波動熱度與理由因素，也支援同一組 `calibration_profile`。

研究優先序也會輸出 `priority_score_components` 與 `priority_score_component_summary`，把 Entry、穩定度、動作、跨日變化、阻擋、可靠度、風險與交易回饋拆成可讀的加扣分，控制台卡片與優先序 CSV 都會保留這份拆解，方便盤前判斷排序原因。

每日雷達會把近期已成熟候選的後續表現回饋到排序，並同時參考 5 / 10 / 20 根日線：
- `outcome_reliability_score`: 依理由訊號 bucket 在多週期的正報酬率與平均報酬計算，樣本不足時為 0。
- `outcome_reliability_boost`: 可靠度分數換算成 Entry Score 的小幅加權，控制在保守範圍內。
- `outcome_reliability_samples`: 本次可靠度加權參考到的成熟樣本數；同日同標的重跑只保留最新快照，不會重複計入歷史證據。
- `outcome_reliability_horizons`: 各週期的正報酬率摘要，例如 `5d:0.60|10d:0.55|20d:0.50`。
- 跨日變化、穩定候選與研究優先序以不同交易日計算，同日重跑只取最新 run；穩定候選只保留 `ENTRY_READY` 或 `WATCH_WAIT_TRIGGER`，避免「不建議進場」的重複出現被誤當成投資首選。

### 7-5) TradingAdvisor 決策輔助工作流

本專案第一版的 TradingAdvisor 是「AI 決策輔助 + 人類確認送單」，不是自動化交易。預設使用 `stub` advisor；`CodexAdvisor` 保留介面但預設失敗關閉，不會即時呼叫 LLM，也不會自動送出委託。

建議先用本機控制台觀察：

```bash
.venv/bin/python run_web.py --host 127.0.0.1 --port 8766
```

開啟：

```text
http://127.0.0.1:8766/#shioaji
```

基本流程：
- 在「AI 決策」頁籤跑一鍵候選流程或每日雷達。
- 到「交易驗收」頁籤的「AI 提案工作台」輸入股票代號、資金與持股。
- `Advisor Provider` 選 `stub`，按「產生 AI 提案」。
- 檢查理由、風險、資料品質與 preview。
- 不採納就按「婉拒提案」；採納模擬測試才勾選人工確認並按「確認送單」。

Advisor API 摘要：
- `POST /api/advisor/proposals`：產生 advisor decision，可選擇建立 order preview。
- `POST /api/advisor/reject`：人工婉拒提案，只更新 decision record。
- `POST /api/advisor/backtest`：執行 Advisor 回測。
- `POST /api/advisor/backtest/export`：執行回測並匯出 HTML/CSV/JSON。
- `POST /api/tw-live/promotion-readiness`：用 observation records 與最近 promotion gate 判讀是否可進 paper 或小額 live。
- `POST /api/tw-live/order-approve-execute`：以 `preview_id` 還原委託，必須有 `manual_confirmed=true` 與 `promotion_gate_accepted=true`；正式環境還會強制查 `small_live_ready`，未通過時以 `promotion_readiness_blocked` 拒絕。
- live order 必須帶 `strategy_name`、`strategy_version` 與 `signal_id`；正式 approve 會拒絕與原始 Preview 不一致的追蹤欄位，避免送單前後策略/訊號被改寫，後續可直接用稽核紀錄回看訊號績效。
- live order 若 preview metadata 帶有 `reference_price`，execution service 會先檢查委託價是否落在交易單位對應的價格保護內：盤中零股預設 3 ticks / 1%，整股預設 2 ticks / 0.5%；偏離過大會以 `price_deviation_guard` 拒絕，不會送到 broker。
- live order 若 preview metadata 帶有 `limit_up_price` / `limit_down_price`，會先阻擋買在漲停價、賣在跌停價或超出漲跌停區間的委託；若帶有 `disposition_active`、`attention_active` 或 `is_disposition_or_attention`，會以 `disposition_attention_guard` 拒絕，不會送到 broker。控制台建立正式 Preview 時也會查本機處置股資料表，自動把處置期間寫入 metadata。
- live 賣出單必須帶有 preview 的 `position_quantity`；若 metadata 另有 `sellable_quantity`，會優先用可賣股數做 live gate 檢查。台股帳戶快照會優先以 Shioaji `Unit.Share` 取得股數，並以 `yd_quantity` 或券商可賣欄位作為基礎可賣股數，再扣除同股票未成交賣單；整股 `Common` 委託會以 1 張 = 1000 股換算後檢查。缺少持股數、委託股數大於持股數，或大於扣除未成交賣單後的可賣股數時，會以 `position_quantity_required_for_sell`、`position_quantity_for_sell` 或 `sellable_quantity_for_sell` 拒絕，不會送到 broker。
- live order 若 preview metadata 帶有 `portfolio_equity` 與 `current_portfolio_exposure`，會在後端與 broker 前雙層檢查本次委託後的整體投組曝險，超過 `max_portfolio_exposure_pct` 時以 `max_portfolio_exposure` 拒絕；同一批 metadata 也會套用單一持股上限與當日損失上限。
- 控制台「查詢台股帳戶快照」後，會依 Preview 股票代號自動帶入「目前持股」與「可賣股數」欄位；可賣股數會先以券商可賣欄位或 `yd_quantity` 判斷，再扣除同股票尚未完成的賣出委託，並在 Preview 提示中標示來源（券商可賣欄位 / 昨日庫存 / 持股數回退）。帳戶快照也會估算單檔市值、投組權益、目前曝險、未成交買單曝險、已實現損益、未實現損益與損益估計，並在提示中標示 PnL 來源拆解。正式 Preview 會一併寫入 portfolio risk metadata、PnL 來源 metadata 與整股/零股換算後的 `share_quantity`。Preview 區塊會用風控摘要卡同步顯示價格保護、可賣股數、單檔上限、投組曝險與日損上限狀態；切換股票代號、買賣別、交易單位、價格、數量、曝險上限或日損上限時會重新估算提示。建立正式 Preview 前也會先重新查詢帳戶快照與券商合約參考價，任一查詢失敗時不建立 Preview。

Advisor 回測規則：
- 使用隔離暫存 SQLite，不寫正式資料庫。
- 第 `d` 天只使用第 `d` 天以前資料。
- 成交價使用下一交易日 open。
- 賣出款 T+2 才回到 cash。
- FIFO lot、手續費、證交稅、勝率、期望值都會輸出。

操作手冊與驗收清單：
- `docs/advisor_decision_assist_runbook.md`
- `docs/advisor_acceptance_checklist.md`
- `docs/competitive_optimization_benchmark.md`：外部知名量化/投資工具 benchmark 與本專案優化順序
- `docs/decision_radar_phase1_acceptance.md`：決策雷達 Phase 1 使用者驗收清單，通過後再進下一階段
- `docs/decision_radar_phase1_test_journal.md`：Phase 1 連續 2 到 3 個交易日測試紀錄表

投資標的池：
- 控制台的一鍵候選與每日雷達建議預設使用 `research_ready` 研究就緒池；CLI 與既有排程仍維持 `curated_watchlist`，避免自動變更既有排程範圍。
- 風格可切換 `conservative`、`balanced`、`aggressive`：保守型會提高一鍵候選與每日雷達的 Entry / Watch 門檻、較早標示 ATR 過熱並收緊族群集中；積極型會放寬波動與軟性進場條件，適合只先擴大觀察池時使用。
- 可切換 `research_ready`、`dynamic_balanced`、`dynamic_liquid`、`tw0050`、`tw_mid100`、`index:custom`、`twse_tpex` 或 `custom`；自訂清單只在選擇 `custom` 時套用。
- `dynamic_balanced` 會從上市 + 上櫃股票中依最近成交金額與正式產業分類輪替取樣，並排除不碰清單；`dynamic_liquid` 則直接取最近流動性較好的標的。資料不足時會退回精選觀察池。
- `research_ready` 預設要求近 240 天至少 60 根日線、近 45 天至少 5 筆法人資料，且日線與法人資料都在 5 天 freshness 內；通過後再依成交金額與產業輪替挑選，資料不足時才退回動態族群分散池。
- 本機可用 `data/cache/curated_watchlist.txt` 覆寫精選觀察池，每行一個股票代號，可加 `#` 註解。
- 台灣 50 可在控制台按「同步台灣 50（元大 0050 PCF）」更新，或用 CLI 執行 `.venv/bin/python -m app.cli sync-0050`；系統會優先讀取元大 0050 PCF，寫入 `data/cache/tw0050_symbols.txt` 並更新 `universe_0050_snapshot`，遠端來源暫時不可用時會標示 `remote_error`，再退回本機快取或內建清單。
- 中型 100 可在控制台按「同步中型 100（元大 0051 PCF）」更新，或用 CLI 執行 `.venv/bin/python -m app.cli sync-mid100`；結果會寫入 `data/cache/tw_mid100_symbols.txt`。沒有檔案時會先用最近日線成交金額自動推估非台灣 50 的流動性前 100 檔，再退回內建啟動清單。
- 正式股票 metadata 可用 `.venv/bin/python -m app.cli sync-twse-tpex-universe` 同步 TWSE / TPEX 股票名稱、市場別與 FinMind 產業分類；`data-health-overview` 會顯示正式產業分類覆蓋率，覆蓋不足時每日雷達仍會暫用代號 proxy 分群並在市場池驗池標示來源。
- 指數公司或官方網站若只提供下載檔，可在控制台「指數成分股快取」貼上官方 CSV/TXT，或填入官方 HTTPS 下載 URL（支援 CSV/TXT/XLSX）建立 `index:custom`；也可用 `.venv/bin/python -m app.cli import-index-constituents --index-key custom --file <官方CSV/TXT/XLSX>` 或 `.venv/bin/python -m app.cli sync-index-constituents-url --index-key custom --url <官方HTTPS下載URL>` 匯入到 `data/cache/index_constituents/custom.csv`。每日雷達/一鍵候選可直接套用 `index:<key>` 快取，CLI 也可用 `--universe index:<key>` 使用不同指數成分股快取；首頁資料健康度會顯示目前快取組數、總成分股檔數與最新更新時間。
- 每日雷達排程可在解析 `index:<key>` 前先同步授權指數成分股 URL：單次 job 使用 `--index-constituents-url <官方HTTPS下載URL>`，常駐 `run-scheduler --enable-daily-radar` 使用 `--radar-index-constituents-url <官方HTTPS下載URL>`；若 universe 是 `index:tip_tw50` 且未指定 key，系統會自動寫入 `data/cache/index_constituents/tip_tw50.csv`。
- 控制台的觀察池區塊可管理不碰清單；也可直接編輯 `data/cache/excluded_symbols.txt`，支援 `2330 # 原因` 或 `2330,原因`。一鍵候選會跳過，每日雷達會標示「排除清單」。
- 每日雷達會優先使用 FinMind 股票清單的產業分類做族群分散，缺資料時才回退到股票代號前綴 proxy；同時標示 ATR 熱度，讓過度集中或波動過熱的標的更容易被看見。
- 產業輪動分數會用同族群候選的領先分數、近 5/20 日族群報酬與上漲家數比例做溫和加權，讓接近分數的標的優先偏向近期較強、正在升溫的族群。
- 控制台首頁新增「日常決策 / 研究與維運」工作模式切換：日常模式聚焦資料健康、每日雷達與部位檢查；研究與維運模式可快速開啟資料源工具，更新台灣 50 / 中型 100、外部指數與指數成分股快取。
- 控制台首頁新增「候選品質總覽」：`GET /api/radar/quality-summary` 會把最新每日雷達的可進場比例、無阻擋比例、資料 freshness、基本面覆蓋、風險、可靠度樣本、族群分散、ATR 過熱與 sync job 資料缺口壓成品質分數、品質警示與 Top Quality Picks，首選排序會優先使用 `risk_adjusted_score`，再依資料品質與候選是否命中代號缺口做補正；若資料缺口影響候選，判讀會改成先修資料再選股，避免在 FinMind / Shioaji partial 時過度相信單檔分數。資料健康度也會輸出 `market_data_coverage`，按 TWSE/TPEX 普通股拆出日線、法人買賣超與分點聚合覆蓋率、缺口代號樣本與建議重試同步指令，控制台資料源修復佇列會把這些缺口轉成「重試缺口同步」按鈕，讓資料源問題更容易在選股前修掉。品質總覽也會把候選分成「可先研究 / 只觀察 / 修資料後再看 / 先略過」四條行動線，每條線可複製代號，「可先研究 / 只觀察」可直接套成自訂觀察池，「修資料後再看」可跳到資料源修復佇列；每日決策匯出也會額外產生四條品質分流的 CSV 與純代號 TXT，盤前 Brief 與通知會同步帶出品質分流摘要，且「修資料後再看」會附上缺口資料集摘要，方便留存或貼到外部 watchlist。Top Quality Picks 可直接複製代號或套用成自訂觀察池；若資料可信度偏弱，品質警示可直接跳到資料源修復佇列，重試同步完成後會顯示品質分、缺口候選與缺口資料集的修復前後差異。
- 控制台首頁新增「交易回饋摘要」：`GET /api/radar/trading-feedback` 會交叉最新每日雷達候選與近期 Preview、execution、promotion gate、reconciliation，標出待決策 Preview、送單錯誤、gate 阻擋與對帳不一致；「今天先研究」排序會先扣交易回饋風險，研究配置草稿也會把未清交易回饋的候選降到「先處理風險」行動線，避免隔日又研究或送出同一批尚未清理的標的。
- 控制台首頁的今日決策雷達會先整理成「可進場 / 觀察中 / 不要碰」三張行動卡，顯示每類數量、代表標的、主要理由，並可一鍵切到 AI 決策頁套用對應篩選；代表標的 chips 可直接帶入 Preview、查看詳情，或把高風險標的加入不碰清單。
- 控制台首頁可匯出每日決策 HTML 摘要，方便回看當天優先觀察、族群分布與阻擋原因。
- 每日雷達清單支援互動式篩選、跨日候選池變化、穩定候選、研究優先序、研究配置草稿、配置診斷、配置驗證、配置校準與標的比較：可依代號/名稱/族群/理由搜尋，並用進場狀態、族群、ATR 波動熱度、可靠度加權/樣本、阻擋原因、最低 Entry Score 與排序條件快速縮小觀察清單；也提供「高可靠可進場、低波動可進場、高可靠觀察、可靠度扣分檢查」一鍵快速條件，按鈕會顯示目前命中檔數並標示作用中條件。首頁會比較最新與前一輪每日雷達，顯示新進、連續出現、掉出、轉強與轉弱標的，也會用最近多輪 run 找出連續出現且仍在最新清單內的穩定候選，並把 Entry、穩定度、跨日變化、可靠度與阻擋整合成「今天先研究」排序；每日雷達本身也會輸出 `risk_adjusted_score`，把 Entry、動作、風險、阻擋、ATR 熱度、族群趨勢與可靠度壓成正式風險調整分，控制台可直接依此排序，卡片、比較面板與 CSV 都會保留。研究配置草稿則會用保守研究曝險、單檔上限與族群上限估算建議金額、估計零股股數、整股張數與下一步提示，且會依配置校準小幅調整權重；配置診斷會標示預算使用率、最大族群占比、進場/觀察檔數、配置警示與下一步，並把標的排進「可先建立 Preview / 設觸發觀察 / 先處理風險」三條 review queue；控制台會先用行動線摘要顯示三類檔數與目前主線，三條行動線都可複製、下載 CSV 或套用成自訂觀察池，避免配置草稿只剩金額。控制台可把校準強度切成關閉、保守、標準與積極，避免歷史證據的音量固定不變。配置驗證會回看成熟雷達 run，將配置草稿加權報酬與單純照排名挑前幾檔的基準比較，讓調參有證據；配置校準會列出目前加權與降權最明顯的因素，避免權重變化變成黑箱。篩選後會顯示品質判讀（可優先檢查 / 族群集中 / 阻擋偏多 / 可靠度偏弱等）、首選標的、平均 Entry、可靠度加分檔數、無阻擋檔數與主要族群，並產生跨族群「分散首選」供複製或套用成自訂觀察池；HTML/JSON/CSV 匯出也會保留分散首選、配置草稿、配置診斷、review queue、配置驗證與校準 profile，方便盤後追蹤當天候選池。目前清單也可直接複製代號、套用成自訂觀察池，或複製/下載 CSV。卡片可加入最多 4 檔比較，橫向查看 Entry、風險調整分、風險、價格、波動、可靠度、阻擋與理由。比較面板會優先用後端 `risk_adjusted_score` 彙整 Entry、風險、阻擋、波動、可靠度與族群趨勢，直接提示優先檢查標的，並可複製或下載比較 CSV 留作盤後紀錄。
- 每日雷達的採用回饋現在會輸出原始 boost、信心權重、信心等級與有效 boost；控制台可篩「高信心加分」，比較面板也會顯示採用回饋信心與「組合判讀」，提示候選組合是否族群過度集中、平均風險偏高或阻擋偏多。
- 控制台會依目前篩選條件產生「盤前首選摘要」，濃縮 3 檔優先檢查標的、首選分、主要理由與仍需留意的證據缺口，並可直接複製代號或套用成自訂觀察池，讓盤前流程不用從完整卡片清單重新挑一次。
- 研究配置草稿會把盤前首選邏輯也納入權重：採用回饋高信心、無阻擋會小幅加權，採用回饋扣分、高風險或同族群重複配置會保守降權；控制台配置卡、review queue CSV 與每日雷達總表 CSV 都會保留 `premarket_multiplier` 與調整理由。
- 每日雷達卡片可直接帶入台股 Preview、查看詳情或加入不碰清單；加入後需重新計算每日雷達才會套用。
- 每日雷達篩選器可用「儲存條件」記住常用篩選，之後按「套用儲存」直接回到同一組條件；設定存在瀏覽器端，不會寫入投資資料庫。

stub 觀察期可用 CLI 落庫追蹤：

```bash
.venv/bin/python -m app.cli advisor-observation-add --symbol 2330 --advisor-name stub --preview-created --human-action rejected --simulation-result skipped --data-quality fresh
.venv/bin/python -m app.cli advisor-observation-summary --advisor-name stub --min-days 3
.venv/bin/python -m app.cli promotion-readiness --advisor-name stub --strategy-name manual --strategy-version manual --target-phase paper
.venv/bin/python -m app.cli promotion-readiness --advisor-name stub --strategy-name manual --strategy-version manual --target-phase small_live
```

`promotion-readiness` 會把 observation summary 與最近的 promotion gate 紀錄合併成 `paper_ready`、`small_live_ready` 或 blocked 狀態；小額 live 必須同時通過觀察期、paper gate、對帳與交易限制檢查。

### 8) Shioaji 模擬測試與正式切換（Web）

控制台提供五個交易測試動作：
- `登入測試`：驗證 API key/secret 可登入
- `證券下單測試`：送出測試股票單並回報狀態
- `期貨下單測試`：送出測試期貨單並回報狀態
- `一鍵模擬整套測試`：依序執行登入、證券、期貨測試（內建最小間隔）
- `檢核正式環境`：以正式環境登入並檢查 `signed=True` 與切換條件

安全機制：
- 正式環境下單測試預設鎖定，需在頁面勾選「允許正式環境下單測試」
- 若 token 沒有正式權限，檢核結果會直接提示開通步驟
- 所有流程結果皆保留 JSON raw 資料，便於除錯與稽核
- 永豐模擬帳戶若不支援可用餘額，台股模擬測試會使用 `SHIOAJI_SIMULATION_CASH_FALLBACK`，預設 `1000000`
- 若模擬帳戶沒有期貨帳戶，期貨測試會標示 skipped，不影響台股模擬驗證

模擬健康檢查 API：

```bash
curl "http://127.0.0.1:8766/api/tw-live/health?simulation=true"
```

控制台「交易驗收」頁籤可執行一鍵模擬整套測試。

### 9) Shioaji 環境檢查（CLI）

```bash
python tools/shioaji_check.py
```

### 10) 台股 Phase 1 CLI（資料庫 + 同步 + 回測）

新增 `app/` 模組，支援以下命令：

```bash
python -m app.cli init-db
python -m app.cli check-finmind
python -m app.cli sync-0050
python -m app.cli sync-0050 --local-cache
python -m app.cli sync-twse-tpex-universe
python -m app.cli import-index-constituents --index-key custom --file data/imports/official_index_constituents.csv --source official_csv
python -m app.cli sync-index-constituents-url --index-key tip_custom --url https://backend.taiwanindex.com.tw/path/to/authorized_constituents.xlsx --source tip_official_url
python -m app.cli list-index-constituents
python -m app.cli sync-bars --start-date 2024-01-01
python -m app.cli sync-chip --start-date 2024-01-01
python -m app.cli sync-broker-agg --start-date 2024-01-01
python -m app.cli sync-disposition --start-date 2024-01-01
python -m app.cli sync-attention
python -m app.cli sync-market-bundle --start-date 2024-01-01 --symbols 2330,2317
python -m app.cli rebuild-features --start-date 2024-01-01 --symbols 2330
python -m app.cli signal-preview --trade-date 2025-01-03 --available-cash 10000
python -m app.cli list-0050
python -m app.cli backtest --symbol 2330 --start-date 2024-01-01 --end-date 2025-01-01
python -m app.cli promotion-readiness --target-phase small_live --strategy-name manual --strategy-version manual
python -m app.cli eod-summary --limit 100
python -m app.cli run-signal-job --notify telegram --available-cash 10000
python -m app.cli run-daily-radar-job --trade-date-mode T --target-count 15 --universe curated_watchlist --notify telegram --notify-style premarket
python -m app.cli run-daily-radar-job --trade-date-mode T --target-count 15 --universe index:tip_custom --index-constituents-url https://backend.taiwanindex.com.tw/path/to/authorized_constituents.xlsx --index-constituents-source tip_official_url
python -m app.cli daily-radar-health --max-age-days 3 --min-rows 5 --notify telegram --notify-on danger --fail-on-danger
python -m app.cli daily-radar-trigger-monitor --notify telegram --notify-on ready --price-source shioaji_snapshot --max-price-age-seconds 180 --notification-cooldown-minutes 240
python -m app.cli intraday-quote-service-status --max-age-seconds 300 --fail-on-warn
python -m app.cli intraday-quote-service-review --max-age-seconds 3600
python -m app.cli run-intraday-quote-service --symbols 2330,2317,2454 --heartbeat-interval-seconds 5
python -m app.cli ops-readiness --target-phase paper --max-age-days 3 --min-rows 5 --include-intraday-quote-review --fail-on-danger
python -m app.cli run-scheduler --notify both --available-cash 10000 --hour 13 --minute 40
python -m app.cli run-scheduler --enable-daily-radar --radar-hour 8 --radar-minute 45 --radar-universe index:tip_custom --radar-index-constituents-url https://backend.taiwanindex.com.tw/path/to/authorized_constituents.xlsx --radar-index-constituents-source tip_official_url --notify telegram --radar-notify-style premarket
python -m app.cli run-scheduler --enable-daily-radar --enable-trigger-monitor --radar-hour 8 --radar-minute 45 --trigger-monitor-hour 9 --trigger-monitor-minute 15 --notify telegram --trigger-monitor-notify-on near --trigger-monitor-price-source shioaji_snapshot --trigger-monitor-max-price-age-seconds 180 --trigger-monitor-notification-cooldown-minutes 240
python -m app.cli write-launchd-plist --hour 8 --minute 45 --universe curated_watchlist --notify telegram --notify-style premarket
python -m app.cli write-trigger-monitor-launchd-plist --hour 9 --minute 15 --notify telegram --notify-on near --price-source shioaji_streaming --streaming-seconds 3 --max-price-age-seconds 180 --notification-cooldown-minutes 240
python -m app.cli write-systemd-units --project-dir /opt/quant-trade --python /opt/quant-trade/venv/bin/python --hour 8 --minute 45 --notify telegram --notify-style premarket --include-health-monitor --health-use-ops-readiness --health-include-intraday-quote-review --include-trigger-monitor --trigger-monitor-notify telegram --trigger-monitor-notify-on near --trigger-monitor-price-source shioaji_streaming --trigger-monitor-streaming-seconds 3 --trigger-monitor-max-price-age-seconds 180 --trigger-monitor-notification-cooldown-minutes 240 --include-intraday-quote-service --intraday-quote-symbols 2330,2317,2454
python -m app.cli write-docker-compose --project-dir /opt/quant-trade --hour 8 --minute 45 --universe curated_watchlist --notify telegram --notify-style premarket --health-use-ops-readiness --health-include-intraday-quote-review --enable-trigger-monitor --trigger-monitor-notify-on near --trigger-monitor-price-source shioaji_streaming --trigger-monitor-streaming-seconds 3 --trigger-monitor-max-price-age-seconds 180 --trigger-monitor-notification-cooldown-minutes 240 --enable-intraday-quote-service --intraday-quote-symbols 2330,2317,2454
python -m app.cli live-buy --symbol 2330 --price 580
python -m app.cli paper-ledger --symbol 2330 --start-date 2024-01-01 --end-date 2025-01-01 --initial-cash 10000
```

備註：
- `FINMIND_API_KEY` 請放在 `.env`（命名已統一）
- `backtest` 需安裝 `backtrader`
- `run-signal-job` / `run-scheduler` 會以 MA60 + 量能 + RSI3/KD 進場條件計算訊號，並用絕對本金防線估算可買股數
- `run-daily-radar-job` 會執行每日雷達的一次性資料同步、注意股更新、標的排序、快照保存與 HTML/CSV/JSON 匯出；日常預設只同步核心雷達資料（日線、法人、產業指數），避免分點、處置、基本面與新聞等加值/擴展資料權限把雷達拖成 partial，需要完整研究時再加 `--include-extended-data`。匯出時會一併產生研究配置草稿、配置診斷、review queue、配置驗證與配置校準，讓排程 artifact 和控制台手動匯出使用同一套盤前決策資料。搭配 `--notify telegram|line|both` 會把 `decision_brief` 壓成通知摘要；`--notify-style premarket|intraday|postmarket` 可切換盤前完整 Brief、盤中精簡快訊或盤後回顧，觸發觀察標的會用短版觸發條件，避免通知文字重複。若帶 `--index-constituents-url`，會先同步官方/授權指數成分股快取再解析 `index:<key>`。`run-scheduler --enable-daily-radar --notify telegram|line|both` 可把同一流程排成平日固定工作，並可用 `--radar-notify-style` 決定排程通知密度；也可用 `--radar-index-constituents-url` 在每次雷達 job 前更新指數池；排程若要同步擴展資料可加 `--radar-include-extended-data`。
- `daily-radar-health` 會檢查最新每日雷達 run、資料新鮮度、候選數、可進場數與 `daily_radar` sync job 狀態；搭配 `--notify telegram|line|both` 與 `--notify-on danger|warn|always` 可主動送出告警，搭配 `--fail-on-danger` 可讓 VM、Docker 或 cron 直接用退出碼接監控告警。
- `daily-radar-trigger-monitor` 會檢查最新 `WATCH_WAIT_TRIGGER` 標的的 Entry Score、突破/回測價、ATR 熱度、資料 freshness 與阻擋標籤，分成「已觸發 / 接近觸發 / 等待中 / 先處理阻擋」；搭配 `--price-source shioaji_snapshot` 可用 Shioaji snapshot 覆蓋雷達快照價格，搭配 `--price-source shioaji_streaming --streaming-seconds 3` 則會短時間訂閱 Shioaji tick / bidask，優先使用 tick 最新成交價，沒有收到串流報價時會 fallback 到 snapshot。串流報價會先進 bounded quote buffer，payload 會保留 `quote_buffer_status`、tick/bidask 收到筆數、保留筆數、訂閱事件數，以及 `quote_manager` 生命週期狀態、`price_snapshot`、`price_source`、`intraday_price`、`radar_price` 與 `price_freshness_status`；控制台「觸發觀察監控」卡也會把報價來源、freshness、Tick/五檔筆數、連線/重連/重訂閱與 fallback 狀態顯示成報價證據列，並可直接切換雷達快照、Shioaji snapshot、Shioaji streaming、串流秒數、報價有效秒數與正式/模擬 Shioaji。控制台另提供 process-local「盤中行情服務」卡，可手動輸入代號啟動/停止共用 quote manager，狀態查詢不會登入 Shioaji，非盤中預設不啟動，需勾選允許非盤中測試才會訂閱；服務狀態會輸出 `health` 判讀，區分未啟動、正常、提醒與異常，並列出斷線、重新連線中、缺訂閱、尚未收到報價、非交易時段仍執行等問題與下一步，也會寫入 `data/cache/intraday_quote_service_status.json` 作為跨程序 health snapshot，可用 `intraday-quote-service-status --max-age-seconds 300 --fail-on-warn` 讓 VM、Docker 或 cron 檢查狀態檔是否過期或異常。外部報價若超過 `--max-price-age-seconds`（排程參數為 `--trigger-monitor-max-price-age-seconds`）會被標成 stale、降回雷達價格，且不會把標的推成已觸發或接近觸發；報價失敗時也會降回雷達價格並記錄錯誤。搭配 `--notify telegram|line|both` 與 `--notify-on ready|near|always` 可把真正達標或接近達標的觀察線送出提醒，控制台也提供 `/api/radar/trigger-monitor` 與「觸發觀察監控」卡片。通知預設用 `data/cache/daily_radar_trigger_monitor_state.json` 記錄已送出的觸發事件，並用 `--notification-cooldown-minutes 240` 抑制同一 run、同一批觸發代號的重複提醒；需要人工重送時可加 `--force-notify`。常駐排程可用 `run-scheduler --enable-trigger-monitor --trigger-monitor-price-source shioaji_streaming --trigger-monitor-streaming-seconds 3`，或由 launchd/systemd/Docker 產生器一併寫出固定監控工作；若明確要用正式 Shioaji 環境，可再加 `--shioaji-live` 或 `--trigger-monitor-shioaji-live`。
- `run-intraday-quote-service --symbols 2330,2317` 可啟動長時間 Shioaji streaming quote manager，依 `--heartbeat-interval-seconds` 持續更新 `data/cache/intraday_quote_service_status.json`，並可用 `--max-runtime-seconds` 做測試收尾；正式環境需明確加 `--shioaji-live`。
- `intraday-quote-service-review` 會用同一份 `data/cache/intraday_quote_service_status.json` 產生盤後健康回顧，不會登入券商；它會檢查 snapshot freshness、heartbeat 次數、本輪最差 health、最高報價覆蓋率、tick/五檔證據與最後連線狀態，輸出「盤後正常 / 盤後留意 / 盤後需處理」，可加 `--json` 給排程或報表歸檔，也可加 `--fail-on-warn` / `--fail-on-danger` 接到 VM 或 Docker health gate。控制台「營運總檢」的盤中行情服務卡也會顯示這份盤後回顧的 heartbeat、報價覆蓋、tick / 五檔與 snapshot freshness，讓隔日要不要信任觸發監控更容易判斷。
- `data-health-overview` 會在 CLI 輸出資料健康度摘要，包含日線 freshness、標的池覆蓋、正式產業分類覆蓋率與 `market_data_coverage` 的 TWSE/TPEX 普通股日線、法人買賣超、分點聚合覆蓋率；產業分類缺漏時會提示 `.venv/bin/python -m app.cli sync-twse-tpex-universe`，雷達與市場池驗池則會把暫用代號 proxy 的比例標出來。分點聚合會標示為「加值資料」，因 FinMind 官方文件標示 `TaiwanStockTradingDailyReportSecIdAgg` 需 sponsor 會員且查詢需帶券商代碼，預設不列入核心市場資料健康度，也不會被一般小批次修復自動補齊。`repair-market-data-coverage` 會依這份 audit 產生小批次修復計畫，預設乾跑，加 `--execute` 才實際同步，也可用 `--batches 3` 這類設定在每批完成後重新讀健康度並接續修下一批。若近期同步已確認某些代號查無資料，修復計畫會先略過它們並往後補下一批，避免卡在同一批無資料股票；若資料集需要加值權限或不同查詢策略，修復計畫會列在「略過加值/不可自動修復資料集」。控制台的資料源修復佇列與市場資料同步卡也提供「修復覆蓋缺口」入口，會重用同一份修復計畫並在完成後重新整理資料健康度與雷達品質摘要。`data-health-overview --json` 可取得完整結構，加 `--fail-on-danger` 可讓排程在資料覆蓋危險時用退出碼阻擋後續流程。
- `ops-readiness` 會彙整每日雷達健康檢查、收盤後對帳摘要、promotion readiness 與 `SHIOAJI_ENABLE_LIVE_ORDERS` / `CODEX_ADVISOR_ENABLED` 環境旗標，輸出整體 `ok / warn / danger`、阻擋原因與下一步；加上 `--include-intraday-quote-review` 時，也會把持久化盤中行情服務盤後回顧納入 CLI 總檢，讓 VM/cron 不開控制台也能用 heartbeat、報價覆蓋與 snapshot freshness 判斷觸發監控是否可信。控制台也提供 `/api/ops/readiness` 與首頁「營運總檢」卡，把每日雷達、收盤摘要、升級 Gate、環境旗標與 process-local 盤中行情服務五個元件、核心指標與第一個下一步直接放在日常工作台，並把阻擋/提醒做成 chips 與處理按鈕，可跳到雷達部署、交易驗收、升級 Gate、環境檢查或盤中行情服務區。每日雷達 partial 時會把 FinMind 權限/token、上游限流、Shioaji 注意股 API 等資料源問題壓成 `sync_issues` 摘要；首頁資料健康度也會依 sync job failure summary 產生「資料源修復佇列」，把資料集缺口、代號缺口、FinMind 用量檢查、Shioaji 環境檢查、資料源工具、重跑每日雷達與「重試小範圍同步」整理成可點選動作，同步完成後會自動重新整理資料健康度、營運總檢與雷達部署狀態，並顯示修復前後的待辦、資料集缺口與代號缺口差異，方便開盤前直接知道要修資料權限、重試同步，還是先保守處理注意股。搭配 `--fail-on-danger` 可做 VM 部署或開盤前總檢查。
- `eod-summary` 會彙整今日 execution、Preview 決策、最近 promotion gate、最近 reconciliation 與資料健康度，作為收盤後對帳 / paper-live 差異追蹤的第一版營運摘要。
- `import-index-constituents` / `sync-index-constituents-url` / `list-index-constituents` 可管理官方或自訂指數成分股快取，支援 CSV/TXT/XLSX，供 `--universe index:<key>` 使用；首頁資料健康度也會顯示快取 freshness。URL 同步只接受 HTTPS 並拒絕 localhost / 內網位址。
- `write-launchd-plist` 會產生 macOS `launchd` plist 到 `deploy/launchd/`，方便把每日雷達接到本機使用者排程；也支援 `--notify telegram|line|both` 在 job 完成後送通知摘要，並可用 `--notify-style premarket|intraday|postmarket` 切換盤前、盤中或盤後訊息密度，也可用 `--index-constituents-url` 固定同步指數池。`write-trigger-monitor-launchd-plist` 則可單獨產生觸發觀察監控 plist，並會帶上 `--price-source`、`--max-price-age-seconds`、通知冷卻與狀態檔參數。指令只寫檔，不會自動載入系統排程。
- `write-systemd-units` 會產生 Linux/VM 用的 systemd service + timer 到 `deploy/systemd/`；加上 `--notify telegram|line|both` 可讓 daily radar service 完成後送通知摘要，並可用 `--notify-style premarket|intraday|postmarket` 切換盤前、盤中或盤後訊息密度；加上 `--include-health-monitor --health-notify telegram|line|both` 會再產生每日雷達健康檢查 service + timer，排程後主動告警；若加 `--health-use-ops-readiness --health-include-intraday-quote-review`，健康檢查會改用 `ops-readiness` 並納入盤後行情回顧。加上 `--include-trigger-monitor --trigger-monitor-notify telegram|line|both` 會一併產生觸發觀察監控 service + timer，並支援 `--trigger-monitor-price-source`、`--trigger-monitor-max-price-age-seconds`、觸發監控通知冷卻與狀態檔參數；加上 `--include-intraday-quote-service --intraday-quote-symbols 2330,2317` 則會產生長時間盤中行情 service、health service 與 health timer，透過 `intraday-quote-service-status` 檢查 snapshot freshness。指令也支援 `--index-constituents-url`，只寫檔，不會自動安裝到 `/etc/systemd/system`。
- `write-docker-compose` 會產生 Docker Compose 與 Dockerfile 到 `deploy/docker/`，常駐跑 `run-scheduler --disable-signal-job --enable-daily-radar`，並預設加入不通知的 Docker healthcheck；`--health-use-ops-readiness --health-include-intraday-quote-review` 可把容器 healthcheck 升級成完整營運總檢並納入盤後行情回顧。`--enable-trigger-monitor` 可讓同一個容器排程也跑觸發觀察監控，並可用 `--trigger-monitor-price-source shioaji_streaming --trigger-monitor-streaming-seconds 3 --trigger-monitor-max-price-age-seconds 180` 讓突破/回測判斷優先使用短時間 Shioaji tick / bidask 報價，沒收到串流報價時自動 fallback snapshot，狀態檔會落在掛載的 `data/` 底下以抑制重複通知；`--enable-intraday-quote-service --intraday-quote-symbols 2330,2317` 會額外產生 `intraday-quote` 服務並用同一個 `data/cache/intraday_quote_service_status.json` 做 Docker healthcheck；`data/`、`reports/`、`logs/` 會掛載到 host，方便容器部署每日雷達，也支援 `--index-constituents-url`。若授權 URL 帶有敏感 token，部署檔案權限請一起控管。
- `sync-market-bundle` 會依序同步：日線 / 法人買賣超 / 分點聚合 / 處置股票（FinMind Sponsor 資料）；日線在 FinMind 失敗且沒有本機 parquet 時，會嘗試 TWSE 官方 `STOCK_DAY` 與 TPEx 官方 `tradingStock` 作為上市 / 上櫃股票 fallback，降低核心候選因 FinMind 403 完全缺日線的機率。若沒有 FinMind 加值權限，建議日常排程先用 `--no-include-broker-agg --no-include-disposition`，把核心日線與法人資料補穩；可加 `--include-attention` 透過 Shioaji `api.notice()` 同步當日注意股（需要 Shioaji API 憑證）。
- 控制台「一鍵候選」與「每日雷達」會在核心同步中帶入注意股同步；若 Shioaji 注意股同步失敗，流程會標為 degraded，資料健康度首頁也會提醒注意股 freshness。
- `rebuild-features` 會產生 `feature_snapshots`（技術 + 籌碼 + 處置狀態）
- `signal-preview` 可先在本地檢視「籌碼條件 + 處置濾網 + 本金防線」後的建議下單結果
- `live-buy` 會在 `place_order` 前做兩次可用餘額校驗（若超額直接拒絕，不送單）
- `paper-ledger` 會產生 T+2 模擬資金帳本（HTML/CSV/JSON）並輸出到 `reports/`

### 11) 執行測試

```bash
pytest -q
```

## API KEY / 憑證安全注意事項（非常重要）

**API KEY、SECRET、憑證密碼絕對不可進版控。**

- 請將 Shioaji 帳密放在 `.env` 檔案（已被 `.gitignore` 排除）
- 永遠不要提交 `.env` 或任何含有帳密的檔案

建議 `.env` 格式如下（本機自行建立，勿上傳）：

```
CONTROL_PANEL_TOKEN=your_long_random_control_panel_token
DATABASE_URL=sqlite:///data/quant_trade.db
SHIOAJI_APIKEY=your_key
SHIOAJI_SECRET=your_secret
SHIOAJI_CA_PATH=/path/to/ca
SHIOAJI_CA_PASSWORD=your_ca_password
SHIOAJI_CA_PERSON_ID=your_person_id
SHIOAJI_SIMULATION_CASH_FALLBACK=1000000
FINMIND_API_KEY=your_finmind_api_key  # FinMind 資料同步與策略 C
FINMIND_API_URL=https://api.finmindtrade.com/api/v4/data
FINMIND_USER_INFO_URL=https://api.web.finmindtrade.com/v2/user_info
CODEX_ADVISOR_ENABLED=        # 第一階段保持空白，使用 stub advisor
OPENAI_API_KEY=
OPENAI_ADVISOR_MODEL=
OPENAI_ADVISOR_TIMEOUT_SEC=30
```

在推上 GitHub 前，請確認：
- `git status` 無敏感檔案
- `.env` 未被追蹤
- `docs/shioaji/llms-full.txt` 未被追蹤
- 不要將 API KEY 寫進任何程式碼
- 第一階段不要設定 `CODEX_ADVISOR_ENABLED=1`

## 帳戶資訊與交易測試（正式帳戶）

Web 控制台已加入「交割帳戶餘額 / 可用買進額度 / 交割明細 / 持倉明細 / 一鍵帳戶診斷」查詢功能。需先設定以下環境變數：

```
SHIOAJI_APIKEY=your_key
SHIOAJI_SECRET=your_secret
```

若需要 CA 憑證：

```
SHIOAJI_CA_PATH=/path/to/ca
SHIOAJI_CA_PASSWORD=your_ca_password
SHIOAJI_CA_PERSON_ID=your_person_id
```

> 注意：正式帳戶會讀取真實資金資訊，請確認帳戶權限與風險。

帳戶資訊功能需要安裝 `shioaji` 套件（已加入 `requirements.txt`），若之前已安裝過依賴，請重新執行：

```bash
pip install -r requirements.txt
```

若使用正式帳戶，請確認你的 API Token 已取得「正式盤」權限；否則會出現
`Token doesn't have production permission` 的錯誤訊息。

控制台的一鍵帳戶診斷會顯示「正式盤權限」狀態，若未開通會提示改用模擬或申請權限。

控制台會顯示：
- 每次動作的進度、耗時秒數
- 查詢結果 JSON（若 API 回傳結構不同，會保留 raw 資料）
- 執行紀錄也會寫入 `reports/control_panel.log`

## VM 部署

第一階段建議先部署現有 FastAPI 控制台，不先改 React。部署架構：

- Nginx 對外提供 HTTPS
- FastAPI 只綁 `127.0.0.1:8766`
- `/api/*` 與 `/reports/*` 使用 `CONTROL_PANEL_TOKEN` 保護
- 既有 `cheng.tplinkdns.com` 根站台不動，Quant-Trade 建議掛在 `/quant-trade/`
- 瀏覽器在控制台「設定」頁輸入 token 後，API 與報表請求會自動帶入

完整步驟請看：

- `docs/vm_deployment.md`

## 依賴清單

- pandas, numpy
- yfinance
- matplotlib, seaborn
- plotly
- tqdm
- python-dotenv
- TA-Lib (可選)
- pyarrow（Parquet 用）
- pytest
- shioaji（選配，實盤/模擬用）

## 注意
- 本期流程以「回測 + 報表」為主
- 實盤交易仍屬保留功能，需自行確認 API 連線與風險機制
- `data/stock_data` 與 `reports` 目前被 `.gitignore` 排除，不會進版控

> *Shioaji 為選配套件，需自行安裝與設定憑證。*
