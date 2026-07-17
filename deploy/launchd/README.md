# macOS launchd：每日雷達排程

產生 plist：

```bash
python -m app.cli write-launchd-plist --hour 8 --minute 45 --universe curated_watchlist
```

預設輸出：

```text
deploy/launchd/com.quant-trade.daily-radar.plist
```

載入到目前使用者的 launchd：

```bash
launchctl bootstrap gui/$(id -u) deploy/launchd/com.quant-trade.daily-radar.plist
```

查看狀態：

```bash
launchctl print gui/$(id -u)/com.quant-trade.daily-radar
```

移除排程：

```bash
launchctl bootout gui/$(id -u) deploy/launchd/com.quant-trade.daily-radar.plist
```

注意事項：
- plist 只保存執行命令與排程時間，不保存 Shioaji、FinMind 或控制台 token。
- launchd 不會載入你的 shell profile；請把環境變數放在專案支援的 `.env`，或使用已設定好的 Python / working directory。
- 預設平日 08:45 執行 `run-daily-radar-job`，會同步資料、更新注意股、排序每日雷達、保存快照並匯出報表。
