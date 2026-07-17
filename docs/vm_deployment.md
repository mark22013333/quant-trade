# Quant-Trade VM 部署手冊

本手冊用於第一階段部署：保留現有 FastAPI 控制台，不建立 React 前端。Nginx 對外提供 HTTPS，FastAPI 只綁 `127.0.0.1:8766`。交易控制台入口由 Nginx IP 白名單與 Basic Auth 保護，API 與報表再由 `CONTROL_PANEL_TOKEN` 保護。

目前 `cheng.tplinkdns.com` 已有既有站台，因此 Quant-Trade 掛在子路徑：

```text
https://cheng.tplinkdns.com/quant-trade/
```

## 前置安全檢查

- 不要把密碼、API key、`CONTROL_PANEL_TOKEN` 寫進 Git、Nginx 或 systemd unit。
- 不要把 Basic Auth 帳密寫進 Git；帳密放在 VM 的 `/etc/nginx/.htpasswd`，備份資訊放 root-only 檔案。
- 使用 SSH key 登入 VM；若曾貼出密碼，請先輪替。
- 第一階段不要設定 `SHIOAJI_ENABLE_LIVE_ORDERS=1`。
- 部署前本機先跑：

```bash
.venv/bin/python -m pytest
git status --short
```

## VM 唯讀檢查

登入 VM 後先查目前環境，不要先改檔：

```bash
lsb_release -a
nginx -v
systemctl status nginx --no-pager
nginx -T
ss -ltnp
```

確認 80/443 可對外，`8766` 不應對外開放。

## 安裝系統套件

CentOS Stream 8 / RHEL-like：

```bash
dnf install -y git nginx python3.11 python3.11-pip python3.11-devel gcc gcc-c++ make
```

Ubuntu/Debian：

```bash
apt update
apt install -y python3-venv python3-pip git nginx certbot python3-certbot-nginx
```

## 部署專案

```bash
mkdir -p /opt
git clone https://github.com/mark22013333/quant-trade.git /opt/quant-trade
cd /opt/quant-trade
python3.11 -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
mkdir -p data reports .cache
```

`TA-Lib` 是可選套件。若 VM 已安裝 TA-Lib 系統函式庫，再另外安裝：

```bash
venv/bin/python -m pip install TA-Lib
```

建立 `/opt/quant-trade/.env`，權限設為 `600`：

```bash
CONTROL_PANEL_TOKEN=<long-random-token>
CONTROL_PANEL_TRUST_PROXY_AUTH=1
CONTROL_PANEL_TRUST_PROXY_AUTH_SOURCES=192.168.3.235
DATABASE_URL=sqlite:////opt/quant-trade/data/quant_trade.db
SHIOAJI_ENABLE_LIVE_ORDERS=

SHIOAJI_APIKEY=
SHIOAJI_SECRET=
SHIOAJI_CA_PATH=
SHIOAJI_CA_PASSWORD=
SHIOAJI_CA_PERSON_ID=
SHIOAJI_SIMULATION_CASH_FALLBACK=1000000

FINMIND_API_KEY=
FINMIND_API_URL=https://api.finmindtrade.com/api/v4/data
FINMIND_USER_INFO_URL=https://api.web.finmindtrade.com/v2/user_info

CODEX_ADVISOR_ENABLED=
OPENAI_API_KEY=
OPENAI_ADVISOR_MODEL=
OPENAI_ADVISOR_TIMEOUT_SEC=30
```

```bash
chmod 600 /opt/quant-trade/.env
/opt/quant-trade/venv/bin/python -m app.cli init-db
```

## systemd

```bash
cp /opt/quant-trade/deploy/systemd/quant-trade-web.service /etc/systemd/system/quant-trade-web.service
systemctl daemon-reload
systemctl enable --now quant-trade-web
systemctl status quant-trade-web --no-pager
journalctl -u quant-trade-web -n 100 --no-pager
```

每日雷達平日排程可先產生 systemd service / timer，再安裝：

```bash
/opt/quant-trade/venv/bin/python -m app.cli write-systemd-units \
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
  --trigger-monitor-streaming-seconds 3 \
  --trigger-monitor-max-price-age-seconds 180 \
  --include-intraday-quote-service \
  --intraday-quote-symbols 2330,2317,2454

cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar.service /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar.timer /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-health.service /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-health.timer /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-trigger-monitor.service /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-trigger-monitor.timer /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-intraday-quote.service /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-intraday-quote-health.service /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-intraday-quote-health.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now quant-trade-daily-radar.timer
systemctl enable --now quant-trade-daily-radar-health.timer
systemctl enable --now quant-trade-daily-radar-trigger-monitor.timer
systemctl enable --now quant-trade-intraday-quote.service
systemctl enable --now quant-trade-intraday-quote-health.timer
systemctl list-timers quant-trade-daily-radar.timer --no-pager
journalctl -u quant-trade-daily-radar-health.service -n 100 --no-pager
journalctl -u quant-trade-daily-radar.service -n 100 --no-pager
```

每日雷達 timer 會執行 `run-daily-radar-job`，同步市場資料、更新注意股、排序每日雷達、保存快照並匯出報表；匯出的 HTML/CSV/JSON/Brief 會包含研究配置草稿、配置診斷、review queue、配置驗證與配置校準。若 service 帶有 `--notify telegram|line|both`，完成後會把 `decision_brief` 壓成通知摘要；`--notify-style premarket|intraday|postmarket` 可切換盤前完整 Brief、盤中精簡快訊或盤後回顧。若要固定使用授權指數成分股池，可在 `write-systemd-units` 加上 `--universe index:<key> --index-constituents-url <官方HTTPS下載URL>`，timer 每次跑雷達前會先更新 `data/cache/index_constituents/<key>.csv`。健康檢查 timer 會在 09:30 執行 `ops-readiness --include-intraday-quote-review --fail-on-danger`，用每日雷達、收盤摘要、升級 Gate、環境旗標與盤後行情回顧判斷是否告警。unit 只保存命令與路徑，不保存 API key；若官方 URL 本身含 token，請控管 unit 檔案權限。

盤後總檢與行情服務驗收：

```bash
cd /opt/quant-trade
/opt/quant-trade/venv/bin/python -m app.cli intraday-quote-service-review \
  --state-path data/cache/intraday_quote_service_status.json \
  --max-age-seconds 3600

/opt/quant-trade/venv/bin/python -m app.cli ops-readiness \
  --target-phase paper \
  --include-intraday-quote-review \
  --fail-on-danger

systemctl status quant-trade-intraday-quote.service --no-pager
journalctl -u quant-trade-intraday-quote.service -n 200 --no-pager
```

若要讓盤中觸發觀察也固定跑，可在產生 units 時加上 `--include-trigger-monitor --trigger-monitor-price-source shioaji_streaming --trigger-monitor-streaming-seconds 3`，短時間訂閱 Shioaji tick / bidask 覆蓋每日雷達快照價格，沒有收到串流報價時會 fallback snapshot；若只想用一次性快照，也可改成 `--trigger-monitor-price-source shioaji_snapshot`。外部報價會套用 `--trigger-monitor-max-price-age-seconds` freshness gate，過期或時間不可判讀時會自動降回雷達價格，不會把標的推成已觸發或接近觸發。報價失敗時也會在 payload 留下錯誤，避免通知流程直接中斷。正式 Shioaji 環境可再加 `--trigger-monitor-shioaji-live`，但請先確認 `.env` 與帳戶權限。

```bash
/opt/quant-trade/venv/bin/python -m app.cli write-systemd-units \
  --project-dir /opt/quant-trade \
  --python /opt/quant-trade/venv/bin/python \
  --include-trigger-monitor \
  --trigger-monitor-hour 9 \
  --trigger-monitor-minute 15 \
  --trigger-monitor-notify telegram \
  --trigger-monitor-notify-on near \
  --trigger-monitor-price-source shioaji_streaming \
  --trigger-monitor-streaming-seconds 3 \
  --trigger-monitor-max-price-age-seconds 180 \
  --trigger-monitor-notification-cooldown-minutes 240

cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-trigger-monitor.service /etc/systemd/system/
cp /opt/quant-trade/deploy/systemd/quant-trade-daily-radar-trigger-monitor.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now quant-trade-daily-radar-trigger-monitor.timer
```

本機驗證：

```bash
curl http://127.0.0.1:8766/api/ping -H "Authorization: Bearer <CONTROL_PANEL_TOKEN>"
```

## Nginx + HTTPS

這台 VM 目前使用 `/etc/nginx/conf.d/proxy-ssl.conf` 服務 `cheng.tplinkdns.com`，且根路徑已提供既有站台。請把 `/opt/quant-trade/deploy/nginx/quant-trade-location.conf` 內的 `location` block 插入到既有 `server_name cheng.tplinkdns.com` 的 HTTPS server 內，並放在既有 `location /` 之前。

如果 HTTP server 也直接提供內容，請在 HTTP server 的既有 `location /` 之前加入同樣 block，或只保留既有 HTTP→HTTPS redirect。

若此 VM 位於中央閘道後方，`allow` 白名單看到的可能是閘道或內網來源 IP，不一定是真實訪客 IP。真實外部 IP 白名單應優先在中央閘道設定；若要在本機 Nginx 判斷真實 IP，必須先正確設定可信任的 `set_real_ip_from` 與 `real_ip_header`。

套用後檢查：

```bash
nginx -t
systemctl reload nginx
```

如果憑證尚未建立，再執行：

```bash
certbot --nginx -d cheng.tplinkdns.com
```

驗證：

```bash
curl -I https://cheng.tplinkdns.com/quant-trade/
curl https://cheng.tplinkdns.com/quant-trade/api/ping
curl -u '<BASIC_AUTH_USER>:<BASIC_AUTH_PASSWORD>' https://cheng.tplinkdns.com/quant-trade/api/ping
curl -u '<BASIC_AUTH_USER>:<BASIC_AUTH_PASSWORD>' https://cheng.tplinkdns.com/quant-trade/api/ping -H "X-Control-Panel-Token: <CONTROL_PANEL_TOKEN>"
```

預期：

- 未通過 Basic Auth 的 `/quant-trade/` 與 `/quant-trade/api/*` 回 `401`。
- 若已設定 `CONTROL_PANEL_TRUST_PROXY_AUTH=1`、`CONTROL_PANEL_TRUST_PROXY_AUTH_SOURCES=<trusted-proxy-ip>`，且 Nginx 傳遞 `X-Authenticated-User`，通過 Basic Auth 後可直接操作 API，不必在瀏覽器再輸入 token。
- 通過 Basic Auth 且帶 token 的 `/api/ping` 回 `{"status":"ok",...}`。
- 瀏覽器打開 `https://cheng.tplinkdns.com/quant-trade/` 後，輸入 Basic Auth 帳密即可操作；「設定」中的 `CONTROL_PANEL_TOKEN` 保留給本機直連或除錯。

## 更新部署

```bash
cd /opt/quant-trade
git pull --ff-only origin main
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m app.cli init-db
systemctl restart quant-trade-web
```

更新後再跑一次：

```bash
systemctl status quant-trade-web --no-pager
curl http://127.0.0.1:8766/api/ping -H "Authorization: Bearer <CONTROL_PANEL_TOKEN>"
```
