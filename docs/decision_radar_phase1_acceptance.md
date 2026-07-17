# 決策雷達 Phase 1 驗收清單

更新日期：2026-07-15

本階段的目標是先把專案停在一個可測、可回報、可暫停的位置：每日雷達要能產生更好的投資標的候選，控制台與通知要能讓使用者快速判斷「可下手 / 只觀察 / 不碰」。Phase 1 通過後，先由使用者實測；未通過前不進入下一階段的大規模介面重構或策略最佳化。

## 階段範圍

Phase 1 名稱：日常決策雷達驗收版。

本階段只驗收台股日常決策工作流，不驗收正式 live 下單擴張，不做大型前端框架重寫，不要求全市場資料 100% 補齊。

本階段已包含：

- 每日雷達候選排序與風險品質層。
- 盤前 Brief 與通知的「可下手 / 只觀察 / 不碰」行動分級。
- 候選品質、資料缺口、門檻穩健性、研究配置草稿與 review queue。
- 營運總檢、每日雷達部署健康、盤中觸發觀察與盤後行情服務回顧的第一版檢查。

本階段不包含：

- 大規模跨市場池平行最佳化。
- 真正跨程序的長時間行情 daemon 實機壓力測試。
- 當沖、融資融券、特殊交割等進階 live 交易邊界。
- 控制台 HTML/CSS/JS 元件化重構。

## 使用者驗收任務

請至少用 2 到 3 個交易日跑完以下流程。若遇到資料源權限或交易日假期，可保留截圖與 JSON/Brief 輸出作為替代證據。

建議同步填寫 `docs/decision_radar_phase1_test_journal.md`，把每天的 run_id、三類分級、ops 狀態與人工判斷記下來；測完後可直接用那份紀錄回報 Phase 1 通過或未通過。

1. 執行每日雷達，建議先用日常預設池：

```bash
.venv/bin/python -m app.cli run-daily-radar-job \
  --universe curated_watchlist \
  --risk-profile balanced \
  --notify none
```

2. 打開控制台，確認首頁能在第一屏附近回答：

- 今天有沒有「可下手」標的。
- 哪些標的只適合「只觀察」。
- 哪些標的應該「不碰」或先處理阻擋。
- 資料健康度是否影響今天候選。

3. 檢查最新 JSON 與 Markdown Brief：

- `decision_brief.action_grade.counts` 有 `可下手`、`只觀察`、`不碰`。
- Markdown Brief 開頭有 `行動分級`。
- 每條 review lane 的代表標的有風險層與分級。

4. 預覽通知，不一定要真的送出：

- 控制台通知預覽或排程通知文字能直接看到三類分級。
- 訊息長度可讀，不需要打開 HTML 才知道今天先看什麼。

5. 執行營運總檢：

```bash
.venv/bin/python -m app.cli ops-readiness \
  --target-phase paper \
  --include-intraday-quote-review
```

6. 執行 Phase 1 驗收摘要：

```bash
.venv/bin/python -m app.cli decision-radar-phase1-acceptance --json
```

重點檢查：

- `ready_for_user_testing=true`。
- `requires_user_confirmation=true`。
- `blocking_reasons=[]`。
- `action_grade.counts` 有 `可下手`、`只觀察`、`不碰`。

控制台首頁也會顯示「日常決策雷達驗收」卡，可按「重新整理 Phase 1」讀取同一份 `/api/radar/phase1-acceptance` 結果；若卡片顯示「先修再測」，先處理阻擋原因再開始 2 到 3 個交易日的人工驗收。

7. 若有 `WATCH_WAIT_TRIGGER` 標的，執行一次觸發觀察監控：

```bash
.venv/bin/python -m app.cli daily-radar-trigger-monitor \
  --price-source shioaji_snapshot \
  --notify none
```

8. 驗證沒有正式下單誤觸：

- `SHIOAJI_ENABLE_LIVE_ORDERS` 不設定為 `1`。
- 不因「可下手」分級自動建立 live intent。
- 建立 Preview 前仍需人工確認、價格保護與 promotion gate。

## 跨交易日營運證據

以下證據不能用單元測試或 seed 資料代替，必須在實際操作後留下紀錄。

### 收盤對帳

先從系統與券商後台分別確認現金及庫存，部位一律填股數，不可混用張數：

```bash
.venv/bin/python -m app.cli reconcile \
  --expected-cash <系統現金> \
  --actual-cash <券商現金> \
  --expected-positions <系統代號:股數,...> \
  --actual-positions <券商代號:股數,...>

.venv/bin/python -m app.cli eod-summary
```

沒有部位時可省略兩個 `--*-positions`。不得用相同的推測值同時填入 expected 與 actual 來製造 matched。

### 盤中行情實機驗證

只在台股正式交易時段啟動，`--shioaji-live` 僅切換到正式行情環境，不代表允許正式下單；`SHIOAJI_ENABLE_LIVE_ORDERS` 仍須保持關閉：

```bash
.venv/bin/python -m app.cli run-intraday-quote-service \
  --symbols 2330,2409,3481,2891 \
  --shioaji-live \
  --heartbeat-interval-seconds 30 \
  --fail-on-not-running
```

讓服務持續到收盤後手動停止，再執行：

```bash
.venv/bin/python -m app.cli intraday-quote-service-review \
  --min-heartbeat-count 10 \
  --min-quote-coverage 0.8 \
  --fail-on-danger
```

驗收時記錄 heartbeat、tick、五檔、最大報價覆蓋率、最後連線狀態及 stale fallback。若當日沒有真實斷線事件，不可只憑單元測試宣稱重連實機通過；保留到後續交易日觀察，或在不影響券商工作階段的受控情境驗證。

### 人工 observation

只有完成控制台判讀、Brief 檢查與 Preview 決策後，才可新增當日 observation。以下是「人工婉拒、未送模擬單」範例，欄位必須依當日實際結果調整：

```bash
.venv/bin/python -m app.cli advisor-observation-add \
  --observation-date YYYY-MM-DD \
  --symbol 2330 \
  --advisor-name stub \
  --human-action rejected \
  --simulation-result skipped \
  --data-quality fresh \
  --notes "依當日人工判讀填寫"

.venv/bin/python -m app.cli advisor-observation-summary \
  --advisor-name stub \
  --min-days 3
```

不得為了解除 `observation_days_insufficient` 而補寫未實際執行的日期。

## 通過條件

Phase 1 可結束的標準如下：

- 每日雷達能成功產出 HTML / CSV / JSON / Markdown Brief。
- JSON 有 `decision_brief.action_grade`，且三類數量和代表代號符合當天清單。
- `decision-radar-phase1-acceptance` 回傳 `ready_for_user_testing=true`，且仍保留 `requires_user_confirmation=true`。
- 控制台或 Brief 能讓使用者在 1 分鐘內說出今天先看哪幾檔、哪些只觀察、哪些不碰。
- `ops-readiness` 沒有未解釋的 `danger`；若有 `warn`，原因可接受且已記錄。
- 觸發觀察監控不會因報價失敗把標的誤判成已觸發。
- 盤前通知預覽可讀，且不需要另外查 raw JSON 才能理解。
- 連續 2 到 3 個交易日使用後，使用者確認這個流程足夠支撐日常觀察。
- `docs/decision_radar_phase1_test_journal.md` 至少填滿 2 個交易日，且沒有未解釋的誤判或安全疑慮。

## 不通過時先修

若任一條發生，Phase 1 不結束：

- `可下手 / 只觀察 / 不碰` 和使用者人工判斷明顯相反。
- 候選常被資料缺口誤導，但控制台沒有清楚提示。
- 通知太長、太混亂，無法盤前快速讀完。
- 控制台第一屏仍要翻很多區塊才知道今天該看什麼。
- ops readiness 顯示 `danger`，且沒有可接受的背景原因。
- 任何流程繞過人工確認直接接近正式下單。

## 下一階段觸發條件

只有當使用者明確回覆 Phase 1 通過後，才進入 Phase 2。

Phase 2 建議二選一：

- 介面工程階段：拆分控制台大型 HTML/CSS/JS、補行動版與 accessibility、把日常決策區做成更清楚的工作台。
- 研究最佳化階段：做大規模跨市場池 / 多期間 / rolling walk-forward 參數搜尋，擴大投資標的品質驗證。

若使用者沒有指定，預設先做介面工程階段，因為 Phase 1 已經累積很多投資決策訊號，下一步最需要讓日常使用更輕。
