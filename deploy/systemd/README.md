# systemd：每日雷達排程

產生 service / timer：

```bash
python -m app.cli write-systemd-units \
  --project-dir /opt/quant-trade \
  --python /opt/quant-trade/venv/bin/python \
  --hour 8 \
  --minute 45 \
  --universe curated_watchlist \
  --notify telegram \
  --notify-style premarket \
  --include-health-monitor \
  --health-use-ops-readiness \
  --health-include-intraday-quote-review \
  --health-hour 9 \
  --health-minute 30 \
  --health-notify telegram \
  --include-trigger-monitor \
  --trigger-monitor-price-source shioaji_streaming \
  --include-intraday-quote-service \
  --intraday-quote-symbols 2330,2317,2454
```

預設輸出：

```text
deploy/systemd/quant-trade-daily-radar.service
deploy/systemd/quant-trade-daily-radar.timer
deploy/systemd/quant-trade-daily-radar-health.service
deploy/systemd/quant-trade-daily-radar-health.timer
```

安裝到 systemd：

```bash
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar.service /etc/systemd/system/
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar.timer /etc/systemd/system/
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-health.service /etc/systemd/system/
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-health.timer /etc/systemd/system/
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-trigger-monitor.service /etc/systemd/system/
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-trigger-monitor.timer /etc/systemd/system/
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-intraday-quote.service /etc/systemd/system/
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-intraday-quote-health.service /etc/systemd/system/
sudo cp /opt/quant-trade/deploy/systemd/quant-trade-intraday-quote-health.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quant-trade-daily-radar.timer
sudo systemctl enable --now quant-trade-daily-radar-health.timer
sudo systemctl enable --now quant-trade-daily-radar-trigger-monitor.timer
sudo systemctl enable --now quant-trade-intraday-quote.service
sudo systemctl enable --now quant-trade-intraday-quote-health.timer
```

查看狀態與 log：

```bash
systemctl status quant-trade-daily-radar.timer --no-pager
systemctl status quant-trade-daily-radar-health.timer --no-pager
systemctl list-timers quant-trade-daily-radar.timer quant-trade-daily-radar-health.timer --no-pager
journalctl -u quant-trade-daily-radar.service -n 100 --no-pager
journalctl -u quant-trade-daily-radar-health.service -n 100 --no-pager
journalctl -u quant-trade-daily-radar-trigger-monitor.service -n 100 --no-pager
journalctl -u quant-trade-intraday-quote.service -n 100 --no-pager
journalctl -u quant-trade-intraday-quote-health.service -n 100 --no-pager
```

盤後營運總檢：

```bash
cd /opt/quant-trade
/opt/quant-trade/venv/bin/python -m app.cli intraday-quote-service-review \
  --state-path data/cache/intraday_quote_service_status.json \
  --max-age-seconds 3600

/opt/quant-trade/venv/bin/python -m app.cli ops-readiness \
  --target-phase paper \
  --max-age-days 3 \
  --min-rows 5 \
  --include-intraday-quote-review \
  --intraday-quote-state-path data/cache/intraday_quote_service_status.json \
  --fail-on-danger
```

盤中行情服務重啟 runbook：

```bash
sudo systemctl status quant-trade-intraday-quote.service --no-pager
sudo journalctl -u quant-trade-intraday-quote.service -n 200 --no-pager
sudo systemctl restart quant-trade-intraday-quote.service
sleep 15
/opt/quant-trade/venv/bin/python -m app.cli intraday-quote-service-status \
  --state-path data/cache/intraday_quote_service_status.json \
  --max-age-seconds 60 \
  --fail-on-warn
```

判讀原則：
- `intraday-quote-service-review` 若是「盤後需處理」，隔日觸發監控先保守，不要只靠 streaming 報價推升級。
- `ops-readiness --include-intraday-quote-review` 若因 `intraday_quote_review` 變成 danger，先修行情服務或暫改用 snapshot，再重新跑總檢。
- `heartbeat` 太少、報價覆蓋偏低、最後連線是 disconnected/reconnecting，都要先看 Shioaji 登入、網路與訂閱代號。

移除排程：

```bash
sudo systemctl disable --now quant-trade-daily-radar.timer
sudo systemctl disable --now quant-trade-daily-radar-health.timer
sudo systemctl disable --now quant-trade-daily-radar-trigger-monitor.timer
sudo systemctl disable --now quant-trade-intraday-quote.service
sudo systemctl disable --now quant-trade-intraday-quote-health.timer
sudo rm -f /etc/systemd/system/quant-trade-daily-radar.service /etc/systemd/system/quant-trade-daily-radar.timer
sudo rm -f /etc/systemd/system/quant-trade-daily-radar-health.service /etc/systemd/system/quant-trade-daily-radar-health.timer
sudo rm -f /etc/systemd/system/quant-trade-daily-radar-trigger-monitor.service /etc/systemd/system/quant-trade-daily-radar-trigger-monitor.timer
sudo rm -f /etc/systemd/system/quant-trade-intraday-quote.service /etc/systemd/system/quant-trade-intraday-quote-health.service /etc/systemd/system/quant-trade-intraday-quote-health.timer
sudo systemctl daemon-reload
```

注意事項：
- unit 只保存執行命令與排程時間，不保存 Shioaji、FinMind 或控制台 token。
- 預設讀取 `/opt/quant-trade/.env`，請用 `chmod 600` 保護。
- timer 預設平日 08:45 執行 `run-daily-radar-job`，會同步資料、更新注意股、排序每日雷達、保存快照並匯出報表。
- 健康檢查 timer 預設可設在平日 09:30 執行 `daily-radar-health --fail-on-danger`，可搭配 `--health-notify telegram|line|both` 主動告警。
- 若使用 `--health-use-ops-readiness --health-include-intraday-quote-review`，健康檢查會改跑 `ops-readiness`，同時檢查每日雷達、收盤摘要、升級 Gate、環境旗標與盤後行情回顧。
