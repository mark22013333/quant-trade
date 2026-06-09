# TradingAdvisor 決策輔助操作手冊

## 定位

本專案第一版定位為「AI 決策輔助 + 人類確認送單」，不是自動化交易系統。`TradingAdvisor` 只負責整理資料、提出可驗證提案、保存理由與風險；是否送單一定由人類在控制台確認。

第一階段預設使用 `stub` advisor，不啟用真 LLM，不啟用正式下單。`CodexAdvisor` 已預留介面，但預設失敗關閉。

## 每日 Stub 模擬流程

1. 啟動控制台：

```bash
.venv/bin/python run_web.py --host 127.0.0.1 --port 8766
```

2. 開啟 [http://127.0.0.1:8766/#shioaji](http://127.0.0.1:8766/#shioaji)。
3. 確認 Shioaji 環境提示顯示模擬模式已讀取 `APIKEY/SECRET`，且 CA 已設定或模擬測試可繼續。
4. 在「AI 決策」頁籤跑一鍵候選流程或每日雷達，取得候選股票。
5. 回到「交易驗收」頁籤，在「AI 提案工作台」輸入股票代號、交易日期、可用資金與目前持股。
6. `Advisor Provider` 選 `stub`，勾選「產生下單 Preview」，按「產生 AI 提案」。
7. 檢查提案內容：動作、價格、數量、信心分數、理由、風險與資料品質。
8. 若不採納，按「婉拒提案」。這只會更新 decision record，不會呼叫 Shioaji。
9. 若要測試送單，勾選「我確認要送出預覽委託」，再按「確認送單」。控制台會帶入 `manual_confirmed=true` 與 `promotion_gate_accepted=true`，並且只依 preview 還原委託內容。
10. 到「交易紀錄」或 audit API 檢查 advisor decision、order preview 與 execution record 是否可追蹤。

## Advisor 回測流程

控制台提供 `Advisor 回測` 與 `匯出 Advisor 回測`。回測規則如下：

- 使用隔離暫存 SQLite，只從正式資料庫讀取 K 棒快照，不寫回正式資料庫。
- 第 `d` 天決策只餵到第 `d` 天為止的 K 棒、雷達與資料品質。
- 成交價使用下一個交易日 open。
- 股票賣出款使用 T+2 現金交割；未交割賣出款列入 equity，但不立即回到 cash。
- 買賣成本使用台股手續費與證交稅；賣出實現損益用 FIFO lot 配對。
- 輸出包含 equity curve、交易明細、費用、稅金、勝率、期望值與 advisor rationale。

## 永豐模擬限制

- 永豐模擬帳戶可能不支援 `account_balance`，此時台股模擬下單會使用 `SHIOAJI_SIMULATION_CASH_FALLBACK`，預設 `1000000`。pre-trade checks 會標記 `simulation_cash_fallback`，不會假裝是真實帳戶餘額。
- 如果模擬帳戶沒有可用期貨帳戶，`一鍵模擬整套測試` 會把期貨下單標示為 `skipped`，不影響台股模擬驗證。
- 模擬流程以台股驗證為主；期貨 skipped 不是台股流程失敗。

## 正式環境前置條件

正式環境預設鎖住。進入正式環境前必須同時滿足：

- `SHIOAJI_ENABLE_LIVE_ORDERS=1` 已明確設定。
- 若有設定 `SHIOAJI_LIVE_ORDER_NONCE`，請求必須帶入相同 nonce。
- Preview gate 通過，且 preview 的 symbol、side、price、quantity、environment 不可被切換。
- `manual_confirmed=true`。
- `promotion_gate_accepted=true`。
- advisor decision、order preview、execution record 都可追蹤。
- 連續 3 到 5 個交易日的 stub 模擬觀察沒有流程中斷或紀錄缺漏。

## CodexAdvisor 啟用規則

第一階段不要啟用真 LLM。若進入觀察階段，才設定：

```bash
CODEX_ADVISOR_ENABLED=1
OPENAI_API_KEY=...
OPENAI_ADVISOR_MODEL=...
```

啟用後仍只允許它產生提案，不應放寬人工確認、preview gate 或 promotion gate。

## 驗證指令

```bash
.venv/bin/python -m pytest
```

永豐模擬健康檢查可透過控制台或 API 執行：

```bash
curl "http://127.0.0.1:8766/api/tw-live/health?simulation=true"
```

模擬整套測試請在控制台「交易驗收」頁籤按「一鍵模擬整套測試」。
