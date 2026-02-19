# quant-trade

台股量化交易系統（回測 + 報表優先），涵蓋「資料 → 策略 → 回測 → 報表」完整流程。實盤交易（Shioaji）保留於專案中但目前不作為本期重點流程。

## 功能重點

- 多資料來源（YFinance / Shioaji*）
- 可擴充策略架構，策略輸出統一為：
  - `position`：目標持倉（-1/0/1）
  - `signal`：進出場事件（`position.diff()`）
- 回測引擎：支援交易成本（手續費、交易稅、滑價）
- 報表：產生 HTML 互動報表與 CSV 原始資料
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
├── tests/                # 測試
├── main.py               # CLI 入口
├── swing_analysis.py     # 波段分析報表
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

### 6) 執行測試

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
- 不要將 API KEY 寫進任何程式碼

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
