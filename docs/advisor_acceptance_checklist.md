# Advisor 輔助交易本機驗收清單

## 觀察週期

至少連續 3 到 5 個交易日使用 `stub` advisor 觀察。期間不啟用 `CodexAdvisor`，不啟用正式下單。

## 每日紀錄欄位

| 日期 | 股票代號 | Advisor Provider | 是否建立 Preview | 婉拒或確認 | 模擬送單結果 | 資料品質 | 備註 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| YYYY-MM-DD | 2330 | stub | yes/no | rejected/confirmed | submitted/skipped/rejected | fresh/missing/unknown | |

## 每日驗收步驟

1. 啟動控制台：

```bash
.venv/bin/python run_web.py --host 127.0.0.1 --port 8766
```

2. 開啟 [http://127.0.0.1:8766/#shioaji](http://127.0.0.1:8766/#shioaji)。
3. 確認 Shioaji 環境提示正常。
4. 執行 simulation health check，確認 `api_key`、`secret`、`ca_path`、`ca_password`、`login`、`stock_account` 通過。
5. 執行一鍵模擬整套測試。
6. 產生一筆 `stub` advisor 提案。
7. 對至少一筆提案執行婉拒，確認不會呼叫 execution。
8. 對至少一筆可行提案建立 preview，勾選人工確認後送入 simulation。
9. 檢查 advisor decision、order preview、execution record 都可追蹤。
10. 執行 Advisor 回測，確認結果包含 isolation、T+1 open、T+2、FIFO、費用、稅金、勝率、期望值。

## 通過條件

- 控制台流程不中斷。
- `stub` 提案、preview、婉拒、確認送單都有紀錄。
- 沒有誤建立 live intent。
- preview 的 simulation/live environment 不可被切換。
- 缺資料會標記或降級，不會被當成好訊號。
- 永豐模擬帳戶不支援餘額時，台股測試會標示 simulation cash fallback。
- 沒有期貨帳戶時，期貨測試會 skipped，不會讓台股驗證失敗。
- 完整測試維持全綠。

## 上線前不得放寬的條件

- 不設定 `CODEX_ADVISOR_ENABLED=1`，除非已完成 stub 觀察週期。
- 不設定 `SHIOAJI_ENABLE_LIVE_ORDERS=1`，除非正式環境切換計畫已另外核准。
- 不提交 `.env`、資料庫、reports、`__pycache__` 或任何 API key。
