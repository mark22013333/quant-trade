# quant-trade

台股量化交易系統（回測 + 報表優先），涵蓋「資料 → 策略 → 回測 → 報表」完整流程。實盤交易（Shioaji）保留於專案中但目前不作為本期重點流程。

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

## 快速開始

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

### 7-1) 多策略回測資料欄位

輸入 DataFrame 至少需要：
- `Open`, `High`, `Low`, `Close`, `Volume`

策略 C（台股籌碼）會額外使用：
- `Foreign_Net_Buy`
- `InvestmentTrust_Net_Buy`
- `Chip_Concentration_Proxy`

若你沒有自行提供籌碼欄位，系統會嘗試透過 FinMind 載入（失敗時自動降級，不阻斷策略 A/B）。

### 7-2) 回測匯出 API（CSV/JSON）

可直接呼叫 API 進行回測並輸出 artifacts 到 `reports/`：

`POST /api/strategy/backtest/export`

請求 body 可沿用控制台的多策略參數（`symbol`, `market`, `enabled`, `weights`, `threshold`, `risk_config`, `backtest_config`）。

回應會包含：
- `export.files`: 匯出的檔名（summary JSON / trades CSV / signals CSV / equity CSV）
- `export.urls`: 可直接開啟下載的 `/reports/...` 路徑

### 7-3) TradingAdvisor 決策輔助工作流

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
- `POST /api/tw-live/order-approve-execute`：以 `preview_id` 還原委託，必須有 `manual_confirmed=true` 與 `promotion_gate_accepted=true`。

Advisor 回測規則：
- 使用隔離暫存 SQLite，不寫正式資料庫。
- 第 `d` 天只使用第 `d` 天以前資料。
- 成交價使用下一交易日 open。
- 賣出款 T+2 才回到 cash。
- FIFO lot、手續費、證交稅、勝率、期望值都會輸出。

操作手冊與驗收清單：
- `docs/advisor_decision_assist_runbook.md`
- `docs/advisor_acceptance_checklist.md`

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
python -m app.cli sync-bars --start-date 2024-01-01
python -m app.cli sync-chip --start-date 2024-01-01
python -m app.cli sync-broker-agg --start-date 2024-01-01
python -m app.cli sync-disposition --start-date 2024-01-01
python -m app.cli sync-market-bundle --start-date 2024-01-01 --symbols 2330,2317
python -m app.cli rebuild-features --start-date 2024-01-01 --symbols 2330
python -m app.cli signal-preview --trade-date 2025-01-03 --available-cash 10000
python -m app.cli list-0050
python -m app.cli backtest --symbol 2330 --start-date 2024-01-01 --end-date 2025-01-01
python -m app.cli run-signal-job --notify telegram --available-cash 10000
python -m app.cli run-scheduler --notify both --available-cash 10000 --hour 13 --minute 40
python -m app.cli live-buy --symbol 2330 --price 580
python -m app.cli paper-ledger --symbol 2330 --start-date 2024-01-01 --end-date 2025-01-01 --initial-cash 10000
```

備註：
- `FINMIND_API_KEY` 請放在 `.env`（命名已統一）
- `backtest` 需安裝 `backtrader`
- `run-signal-job` / `run-scheduler` 會以 MA60 + 量能 + RSI3/KD 進場條件計算訊號，並用絕對本金防線估算可買股數
- `sync-market-bundle` 會依序同步：日線 / 法人買賣超 / 分點聚合 / 處置股票（FinMind Sponsor 資料）
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
