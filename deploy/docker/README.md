# Docker Compose：每日雷達排程

產生 Compose 與 Dockerfile：

```bash
python -m app.cli write-docker-compose \
  --project-dir /opt/quant-trade \
  --hour 8 \
  --minute 45 \
  --universe curated_watchlist \
  --notify telegram \
  --notify-style premarket \
  --health-use-ops-readiness \
  --health-include-intraday-quote-review \
  --enable-trigger-monitor \
  --trigger-monitor-price-source shioaji_streaming \
  --enable-intraday-quote-service \
  --intraday-quote-symbols 2330,2317,2454
```

預設輸出：

```text
deploy/docker/docker-compose.daily-radar.yml
deploy/docker/Dockerfile.daily-radar
```

啟動：

```bash
docker compose -f deploy/docker/docker-compose.daily-radar.yml build
docker compose -f deploy/docker/docker-compose.daily-radar.yml up -d
```

查看 log：

```bash
docker compose -f deploy/docker/docker-compose.daily-radar.yml logs -f daily-radar
docker compose -f deploy/docker/docker-compose.daily-radar.yml logs -f intraday-quote
```

查看容器健康狀態：

```bash
docker compose -f deploy/docker/docker-compose.daily-radar.yml ps
```

驗收最新每日雷達：

```bash
docker compose -f deploy/docker/docker-compose.daily-radar.yml exec daily-radar \
  python -m app.cli ops-readiness --target-phase paper --max-age-days 3 --min-rows 5 \
    --include-intraday-quote-review \
    --intraday-quote-state-path data/cache/intraday_quote_service_status.json \
    --fail-on-danger
```

盤後行情回顧：

```bash
docker compose -f deploy/docker/docker-compose.daily-radar.yml exec daily-radar \
  python -m app.cli intraday-quote-service-review \
    --state-path data/cache/intraday_quote_service_status.json \
    --max-age-seconds 3600
```

盤中行情服務重啟 runbook：

```bash
docker compose -f deploy/docker/docker-compose.daily-radar.yml ps
docker compose -f deploy/docker/docker-compose.daily-radar.yml logs --tail=200 intraday-quote
docker compose -f deploy/docker/docker-compose.daily-radar.yml restart intraday-quote
sleep 15
docker compose -f deploy/docker/docker-compose.daily-radar.yml exec daily-radar \
  python -m app.cli intraday-quote-service-status \
    --state-path data/cache/intraday_quote_service_status.json \
    --max-age-seconds 60 \
    --fail-on-warn
```

停止：

```bash
docker compose -f deploy/docker/docker-compose.daily-radar.yml down
```

注意事項：
- Compose 常駐執行 `run-scheduler --disable-signal-job --enable-daily-radar`，由 APScheduler 以 Asia/Taipei 時區排程。
- 預設平日 08:45 執行每日雷達，會同步資料、更新注意股、排序每日雷達、保存快照並匯出報表。
- Compose 會加入不通知的 Docker healthcheck，預設定期執行 `daily-radar-health --fail-on-danger`；若使用 `--health-use-ops-readiness --health-include-intraday-quote-review`，healthcheck 會改成完整營運總檢，並把盤後行情回顧納入判斷。
- `intraday-quote-service-review` 若顯示「盤後需處理」，隔日觸發監控先保守；優先檢查 Shioaji 登入、網路、訂閱代號與 `data/cache/intraday_quote_service_status.json` freshness。
- 預設讀取 `<project-dir>/.env`，請用 `chmod 600` 保護。
- `data/`、`reports/`、`logs/` 會掛載到容器，讓 SQLite 資料庫、匯出報表與 log 留在 host。
