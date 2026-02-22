# quant-trade

台股量化交易系統（回測 + 報表優先），涵蓋「資料 → 策略 → 回測 → 報表」完整流程。實盤交易（Shioaji）保留於專案中但目前不作為本期重點流程。

## 功能重點

- 多資料來源（YFinance / Shioaji*）
- 可擴充策略架構，策略輸出統一為：
  - `position`：目標持倉（-1/0/1）
  - `signal`：進出場事件（`position.diff()`）
- 回測引擎：支援交易成本（手續費、交易稅、滑價）
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
├── strategies/           # 策略
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
- 短期投資 Dashboard：Top N / 流動性預篩數量 / 回溯天數
- 波段報表：開始日 / 結束日 / 回溯天數
- AI 協作中心：是否強制同步文件
- Shioaji 測試中心：登入測試 / 證券下單測試 / 期貨下單測試 / 一鍵模擬整套測試
- 正式環境切換檢核：檢查 `production permission`、帳戶 `signed` 狀態、CA 設定

控制台會顯示後端心跳與 Log，執行紀錄同時寫入：
- `reports/control_panel.log`

若要以 Python 直接啟動（適合 IDE Run/Stop）：

```bash
python run_web.py
python run_web.py --reload
python run_web.py --host 0.0.0.0 --port 8080
```

若帳戶查詢無回應，請避免同時點多個帳戶查詢（帳務 API 需要序列化），可先看 `reports/control_panel.log` 追蹤進度。

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

### 9) Shioaji 環境檢查（CLI）

```bash
python tools/shioaji_check.py
```

### 10) 執行測試

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
```

在推上 GitHub 前，請確認：
- `git status` 無敏感檔案
- `.env` 未被追蹤
- `docs/shioaji/llms-full.txt` 未被追蹤
- 不要將 API KEY 寫進任何程式碼

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
