# 台股程式交易策略研究與 AI 計劃書素材

更新日期：2026-06-08

本文整理外部程式交易策略實務、台股市場特性與本專案後續重構方向。用途不是直接保證獲利，而是提供給 AI 或工程師產生下一版「台股策略研究、回測、紙上交易、上線」計劃書的結構化輸入。

## 一、研究結論摘要

台股主線應先採「中低頻、日線到週/月再平衡」策略，而不是高頻或複雜 ML。理由如下：

- 專案目前已建立安全交易管線，下一階段最缺的是策略研究治理、資料品質、回測驗證與股票部位風控。
- 台股有容易取得且具在地特色的資料：OHLCV、法人買賣超、券商分點、融資融券、處置股、注意股、產業分類、月營收。
- 外部實務與研究都提醒：策略從 backtest 到 live 的主要失敗點不是語法，而是過度最佳化、資料外洩、交易成本低估、流動性不足、部位控管不完整。
- 因此應優先建立「策略研究框架」：假說、資料需求、回測假設、OOS/walk-forward、paper 驗證、live 小額驗證、停用條件。

建議優先研究四條策略線：

1. 量價趨勢 / 動能策略
2. 籌碼與法人 order imbalance 策略
3. 均值回歸 / 超跌反彈策略
4. 基本面加技術突破策略

## 二、外部策略類型整理

### 1. 趨勢與動能

核心假說：

- 強勢股在一段時間內可能延續強勢。
- 可用 20/60/120 日均線、多日新高、相對強弱、成交量放大、產業輪動作為訊號。

台股適配：

- 適合大中型股、ETF 成分股、流動性充足標的。
- 應避開處置股、低成交量股、漲停附近追價。

可實作訊號：

- `close > ma20 > ma60`
- `close == rolling_high(60)`
- `volume > avg_volume_20 * 1.5`
- `relative_strength_rank >= 80`
- `industry_momentum_rank >= 70`

主要風險：

- 盤整期容易來回停損。
- 追高遇到漲跌幅限制時，回測成交價可能過度樂觀。

### 2. 籌碼與法人 order imbalance

核心假說：

- 台股的法人、融資融券、分點資料具資訊含量。
- order imbalance 與法人交易方向可能比單純價格動能更貼近台股投資人行為。

可實作訊號：

- 外資連買 N 日，且買超占成交量比例大於門檻。
- 投信連買，且股價站上中期均線。
- 外資、投信同步買超，融資餘額沒有失控增加。
- 券商分點集中度上升，但不過度集中於單一分點。
- 高融資使用率或高券資比作為風險扣分。

主要風險：

- 籌碼資料延遲或缺漏時不可產生 live order。
- 法人買超可能已反映在價格，需用 OOS 驗證避免追逐雜訊。

### 3. 均值回歸 / 超跌反彈

核心假說：

- 短期過度下跌後，若基本面與流動性未惡化，可能有反彈。

可實作訊號：

- `close < lower_bollinger_band`
- `rsi_14 < 30`
- `close / ma20 < 0.9`
- 近 N 日跌幅位於市場前 10%，但成交量未異常萎縮。
- 避開跌停、處置股、融資斷頭疑慮標的。

主要風險：

- 接刀風險高，需要嚴格停損與最大持有天數。
- 台股跌停會造成賣不掉，回測必須模擬跌停不可成交。

### 4. 基本面加技術突破

核心假說：

- 營收成長或品質因子提供中期方向，技術突破提供進場時機。

可實作訊號：

- 月營收 3 個月均線大於 12 個月均線。
- YoY / MoM 營收成長。
- ROE、毛利率、營益率或低本益比作為加分條件。
- 股價突破 20/60 日均線或創 N 日新高。

主要風險：

- 財報與營收公布有時間落差，資料時間戳必須嚴格避免 look-ahead bias。
- 基本面策略週期較長，不應用日內成交假設包裝成短線策略。

### 5. ML / AI 混合策略

核心假說：

- 技術指標、基本面、籌碼、情緒或新聞可合併成特徵，由模型估計排序或勝率。

本專案建議定位：

- 短期不要讓 ML 直接產生下單。
- 先用 ML 作為候選股排序、風險分層、策略 regime filter。
- 所有 ML 輸出仍必須轉成可解釋 `Signal`，再進入 `OrderIntent`。

必要防線：

- 訓練 / 驗證 / OOS 嚴格切分。
- 禁止使用未來資料、修正後資料造成資料外洩。
- 模型版本、特徵版本、資料範圍必須寫入回測與報表。

## 三、台股專用資料需求

必要資料：

- 日 OHLCV
- 交易日曆
- 漲跌停價格或可推導規則
- 零股 / 整股成交規則
- ETF / 台灣 50 / 中型 100 / 產業分類
- 法人買賣超
- 融資融券
- 處置股 / 注意股
- 月營收與財報資料

每筆進入策略或下單的資料都應帶：

- `latest_bar_date`
- `freshness_status`
- `missing_ohlcv_count`
- `chip_data_status`
- `source`
- `insecure_transport`
- `partial_failure`
- `degraded_reasons`

資料品質規則：

- live order 不可使用過期資料。
- live order 不可使用 insecure TLS 取得的資料。
- 策略若依賴籌碼，籌碼資料缺漏時必須拒絕或降級。
- 報表需顯示資料期間、缺漏率、資料來源與同步時間。

## 四、回測與驗證框架

每個策略必須有固定驗證階段：

1. 假說定義
2. 資料探索
3. 初版回測
4. 參數敏感度分析
5. 流動性與處置股分析
6. 年度穩定性分析
7. Alpha/Beta 或 benchmark attribution
8. Out-of-sample 測試
9. Walk-forward 測試
10. Paper trading
11. 小額 live 驗證
12. 例行績效追蹤與停用條件

必要回測假設：

- 手續費、最低手續費、交易稅。
- 滑價與成交延遲。
- T+1 訊號成交。
- T+2 交割。
- 漲停買不到、跌停賣不掉。
- 流動性不足不成交或部分成交。
- 最大單檔部位、最大產業曝險、最大總曝險。

報表必要指標：

- CAGR / 年化報酬
- Sharpe / Sortino
- Max drawdown
- Calmar
- Win rate
- Profit factor
- Turnover
- 平均持有天數
- 交易成本占報酬比例
- 容量估計
- OOS degradation
- 年度穩定性

## 五、風控需求

策略層風控：

- 單檔權重上限
- 產業權重上限
- 最大持股數
- 最大換手率
- 最小成交量
- 停損 / 停利 / 移動停損
- 最大持有天數
- 大盤 regime filter

下單層風控：

- live unlock
- 交易時段
- tick size
- 漲跌幅限制
- 資金檢查
- 部位檢查
- 重複下單檢查
- 每日下單上限
- 處置股阻擋
- 流動性門檻

營運層風控：

- 策略版本鎖定
- 參數變更需留下紀錄
- paper 與 live 差異追蹤
- 異常成交、拒單、timeout 告警
- 每日收盤後對帳

## 六、對目前專案的重構建議

### A. 台股策略研究層

新增或整理：

- `app.signals.registry`
- `app.signals.base`
- `app.signals.tw_momentum`
- `app.signals.tw_chip`
- `app.signals.tw_mean_reversion`
- `app.signals.tw_fundamental_breakout`

每個策略只輸出：

- `Signal`
- `SignalScore`
- `SignalReason`
- `RequiredData`

不可直接下單。

### B. 策略驗證層

新增：

- `StrategyResearchSpec`
- `BacktestValidationReport`
- `WalkForwardResult`
- `PaperPromotionDecision`

每個策略要能回答：

- 使用哪些資料？
- 是否可能有 look-ahead bias？
- OOS 是否通過？
- 是否通過流動性檢查？
- 是否可進 paper？
- 是否可進小額 live？

### C. 台股交易安全層

本專案已具備股票 `OrderIntent -> TradingExecutionService -> ShioajiGateway` 主幹。下一步建議補：

- 賣出單部位檢查。
- 委託價格保護：不可偏離最新參考價超過 N ticks 或 N%。
- 漲跌停成交限制更精準化。
- 處置股與注意股阻擋接正式資料表。
- live order 必須帶 strategy version / signal id。

### D. 報表與文件層

每次策略回測輸出：

- 策略假說
- 資料品質摘要
- execution model 設定
- 成本設定
- OOS 結果
- 風險摘要
- 是否建議進 paper
- 不建議進 live 的原因

## 七、AI 計劃書 Prompt 草稿

可將以下 prompt 交給 AI 產生下一份實作計劃：

```text
請根據本文件，為 quant-trade 專案設計一份「台股策略研究與上線治理」重構計劃。

限制：
- 本階段只做台股，不做期貨。
- 策略不可直接下單，只能輸出 Signal。
- 所有下單仍必須走 OrderIntent -> TradingExecutionService -> ExecutionResult。
- live order 必須經過資料品質、交易時段、tick size、漲跌幅、資金、部位、流動性、處置股與重複下單檢查。
- 回測、paper、live 的成本與成交假設必須一致。

請輸出：
1. 目標架構
2. 模組拆分
3. 資料模型
4. 策略介面
5. 回測驗證流程
6. paper promotion 規則
7. live promotion 規則
8. 測試計劃
9. 交付順序
10. 可讓 AI 逐步實作的任務清單
```

## 八、參考資料

- FinLab Research to Production workflow：策略假說、資料探索、最佳化、流動性、OOS、live deployment 與績效追蹤流程。
  - https://finlab.finance/docs/en/workflows/complete_strategy_workflow/
- TWSE day trading 說明：台股當沖資格、標的限制、處置股不可當沖、交易時段與賣出後買回風險管理。
  - https://www.twse.com.tw/en/products/system/day-trading.html
- Short sale and stock returns: Evidence from the Taiwan Stock Exchange：台灣市場融券與未來報酬關係，可作為融資融券因子研究素材。
  - https://www.sciencedirect.com/science/article/abs/pii/S1062976908000811
- Order Imbalance and Daily Momentum Investing: Evidence from Taiwan：台股 order imbalance 與動能研究，可作為法人 / 籌碼策略假說來源。
  - https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6288.2012.00343.x
- Hybrid AI-driven trading system：技術指標、均值回歸、情緒與 ML regime filter 的混合策略參考。
  - https://arxiv.org/abs/2601.19504
- Sizing Strategies for Algorithmic Trading in Volatile Markets：部位 sizing 與高波動風控參考。
  - https://arxiv.org/abs/2309.09094
