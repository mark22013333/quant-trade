# quant-trade

台股量化交易初版架構

## 資料夾結構

```
quant-trade/
│
├── data/                  # 存放下載的歷史資料
├── strategies/            # 各種交易策略
│     └── ma_cross.py      # 均線交叉策略範例
├── backtest/              # 回測模組
│     └── backtest_engine.py
├── broker/                # 模擬下單、未來串API
│     └── paper_broker.py
├── main.py                # 主程式，整合所有功能
├── requirements.txt       # 依賴套件
└── README.md              # 專案說明
```

## 初版功能
- 下載台股歷史資料
- 均線交叉策略
- 回測績效計算
- 模擬下單
- 預留API串接介面

---

請參考各資料夾與檔案內註解，逐步擴充功能。
