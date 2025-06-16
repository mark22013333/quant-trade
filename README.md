# quant-trade

台股量化交易系統

## 系統架構

此專案實作了完整、模組化的量化交易系統架構，涵蓋從資料獲取、策略開發到回測及實時交易的完整流程。系統設計以高內聚、低耦合為原則，方便擴展與維護。

### 資料夾結構

```
quant-trade/
│
├── config/                # 全域設定與交易標的定義
│     ├── settings.py      # 系統全域設定（回測、風險管理等）
│     └── symbols.py       # 交易標的設定（股票、ETF等）
│
├── data/                  # 資料處理相關模組
│     ├── market_data.py   # 市場資料管理器
│     ├── providers/       # 資料來源提供者
│     │     ├── base_provider.py    # 資料提供者介面
│     │     ├── yfinance_provider.py # Yahoo Finance 資料提供者
│     │     └── shioaji_provider.py  # 永豐金證券資料提供者
│     └── processors/      # 資料處理器
│           └── feature_engineering.py # 特徵工程與技術指標
│
├── strategies/            # 交易策略
│     ├── base_strategy.py # 策略基礎類別
│     ├── momentum/        # 動能策略
│     │     ├── macd.py    # MACD策略
│     │     ├── rsi.py     # RSI策略
│     │     └── simplified_swing.py # 簡化版波段交易策略
│     ├── mean_reversion/  # 均值回歸策略
│     │     └── bollinger_bands.py # 布林通道策略
│     └── portfolio/       # 投資組合策略
│           └── equal_weight.py # 等權重策略
│
├── backtest/              # 回測模組
│     └── backtest_engine.py # 回測引擎
│
├── broker/                # 券商介面
│     ├── broker_interface.py # 券商介面基礎類別
│     ├── paper_broker.py    # 模擬交易券商
│     └── shioaji_broker.py  # 永豐金證券介面
│
├── live_trading/          # 實時交易模組
│     ├── trader.py        # 交易主控
│     └── risk_manager.py  # 風險管理
│
├── reports/               # 分析報表輸出目錄
│     └── *.html           # HTML格式分析報表
│
├── test_shioaji_api/      # API測試範例
│     └── run_api_test.py  # 永豐金API測試
│
├── web_report_strategy.py # 網頁報表分析策略
├── web_report_strategy2.py # 簡化版網頁報表分析策略
├── html_report_generator.py # HTML報表產生器
├── swing_analysis.py      # 波段交易適合度分析主程式
├── main.py                # 主程式進入點
├── requirements.txt       # 依賴套件
└── README.md              # 專案說明
```

## 已實作功能

### 1. 資料處理
- **資料提供者抽象介面**：定義統一的資料獲取介面
- **多資料來源支援**：
  - YFinance資料提供者 (歷史、即時和基本面資料)
  - Shioaji永豐金證券資料提供者 (台股專用)
- **市場資料管理**：自動處理快取、標準化格式、技術指標計算
- **特徵工程**：實作多種技術指標（MACD、RSI、布林通道等）

### 2. 交易策略
- **策略基礎類別**：提供統一的參數管理和訊號產生介面
- **動能策略**：
  - MACD策略：根據MACD與訊號線交叉產生買賣訊號
  - RSI策略：根據RSI超買超賣區間產生訊號，支援背離確認
  - **波段交易策略**：結合多種技術指標分析股票波段操作適合度
- **均值回歸策略**：
  - 布林通道策略：價格突破通道上下軌產生反轉訊號
- **投資組合策略**：
  - 等權重策略：依照指定頻率自動平衡投資組合權重

### 3. 券商介面
- **虛擬交易券商**：支援模擬交易與回測
- **永豐金證券介面**：支援連接永豐金API進行實盤交易

### 4. 回測引擎
- **基礎回測功能**：執行策略、計算基本績效指標
- **績效視覺化**：資金曲線及回撤分析

### 5. 實時交易
- **交易主控**：協調策略、資料和券商之間的互動
- **風險管理**：提供完整的風險控制功能，包括：
  - 部位大小管理
  - 資金分配控制
  - 移動停損
  - 回撤控制
  - 波動率監控

### 6. 波段交易分析
- **股票適合度評分**：分析股票是否適合波段操作，綜合考量：
  - 波動率指標
  - 趨勢強度
  - 成交量變化
  - 回測勝率
- **產業群組分析**：支援按產業進行分組分析
- **簡化分析模型**：經過優化的簡化模型，避免維度不匹配問題

### 7. 互動式網頁報表
- **HTML報表生成**：基於 Plotly 的互動式圖表
- **視覺化分析**：
  - 各產業股票適合度分布圖
  - 波動率與評分散點圖
  - 勝率與平均回報散點圖
- **結果匯出**：
  - 完整 HTML 網頁報表
  - CSV 格式原始資料
- **自動開啟報表**：生成報表後自動在瀏覽器中開啟

## 使用方式

### 波段交易分析

執行以下命令，分析股票是否適合波段操作並生成報表：

```bash
# 啟動虛擬環境
source venv/bin/activate

# 執行波段交易分析
python swing_analysis.py
```

分析結果會在 `./reports/` 目錄下生成 HTML 報表及 CSV 原始資料檔案。

## 環境需求

- Python 3.7+
- pandas, numpy, matplotlib, seaborn
- plotly (互動式圖表)
- yfinance (Yahoo Finance API)
- shioaji (永豐金證券API)
- tqdm (進度條)
- python-dotenv (環境變數管理)
- TA-Lib (技術指標函式庫)
