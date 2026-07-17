# 決策雷達 Phase 1 測試紀錄表

更新日期：2026-07-17

這份表格用來記錄 Phase 1 的 2 到 3 個交易日人工驗收。目標不是追求每天都有可下手標的，而是確認每日雷達、控制台與 Brief 能不能快速說清楚「今天可下手 / 只觀察 / 不碰」，並且沒有繞過人工確認接近正式下單。

## 測試前狀態

開始測試前先確認：

- `.venv/bin/python -m app.cli decision-radar-phase1-acceptance --json` 回傳 `ready_for_user_testing=true`。
- `requires_user_confirmation=true`。
- `blocking_reasons=[]`。
- `SHIOAJI_ENABLE_LIVE_ORDERS` 未設定為 `1`。

若上述任一項不成立，先回到 `docs/decision_radar_phase1_acceptance.md` 的「不通過時先修」。

## 每日紀錄

| 交易日 | run_id | 可下手 | 只觀察 | 不碰 | 控制台 1 分鐘內可判讀 | Brief/通知可讀 | ops 狀態 | 主要 warn / danger | 是否有誤判或卡住 | 今日結論 |
| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |
| YYYY-MM-DD | radar_ |  |  |  | 是/否 | 是/否 | ok/warn/danger |  |  | 通過/需修 |
| YYYY-MM-DD | radar_ |  |  |  | 是/否 | 是/否 | ok/warn/danger |  |  | 通過/需修 |
| YYYY-MM-DD | radar_ |  |  |  | 是/否 | 是/否 | ok/warn/danger |  |  | 通過/需修 |

## 自動化前置證據（不計入人工驗收日）

- 2026-07-17：最新雷達 `radar_20260717_b7073583` 已產生 15 檔候選，正式下單維持關閉。
- 核心市場資料回補後，資料健康度為 89/100；日線覆蓋 96%、法人買賣超覆蓋 99%。剩餘缺口為官方近期查無資料或日期不足，不再無限重試。
- 2026-07-17 已使用 Shioaji 1.2.9 的永豐券商正式帳戶只讀快照建立 Phase 1 初始對帳基準；現金、庫存與未成交委託對帳一致，`cash_diff=0`、`position_diffs=[]`、`open_order_diffs=[]`、`blocking_reasons=[]`。使用者已明確同意採用本次查詢結果作為初始基準。
- 2026-07-17 盤後正式行情環境彩排已連線成功，4 檔標的完成 Tick 與五檔共 8 個訂閱，記錄 10 次服務 heartbeat 並正常停止；因非交易時段無串流報價、覆蓋率為 0%，僅列為連線前置證據，不視為盤中行情驗收通過。
- 此日尚未完成控制台與 Brief 的使用者人工判讀，也沒有正式交易時段行情 heartbeat 證據，因此不能填入上方每日紀錄或算作 Phase 1 通過日。

## 每日檢查問題

每個交易日跑完後，用很短的句子回答：

1. 今天第一眼最該看的標的是哪些？
2. 系統把哪些標的歸成「不碰」，理由是否合理？
3. 如果沒有可下手標的，系統有沒有清楚說明是在等價格、Entry Score、風險降溫、資料 freshness，還是其他原因？
4. `ops-readiness` 若是 `warn`，原因是否可接受且不影響今天人工觀察？
5. 是否有任何流程讓你覺得會誤觸正式下單？

## 最終回報格式

測完 2 到 3 個交易日後，回覆其中一種：

```text
Phase 1 通過。
測試日期：YYYY-MM-DD, YYYY-MM-DD, YYYY-MM-DD
備註：...
```

或：

```text
Phase 1 未通過。
測試日期：YYYY-MM-DD, YYYY-MM-DD
不順項目：
- ...
希望先修：
- ...
```

只有明確回覆「Phase 1 通過」後，才進入 Phase 2。
