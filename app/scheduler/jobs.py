from __future__ import annotations

from datetime import date, datetime, timedelta

from app.alerts.notifier_base import Notifier
from app.alerts.notifier_line import LineNotifyNotifier
from app.alerts.notifier_telegram import TelegramNotifier
from app.alerts.signal_engine import compute_daily_signal_suggestions
from app.db.repository import TradingRepository


def build_notifiers(channels: tuple[str, ...] = ("telegram",)) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if "telegram" in channels:
        tg = TelegramNotifier()
        if tg.bot_token and tg.chat_id:
            notifiers.append(tg)
    if "line" in channels:
        line = LineNotifyNotifier()
        if line.token:
            notifiers.append(line)
    return notifiers


def _resolve_trade_date(trade_date_mode: str = "T") -> date:
    mode = str(trade_date_mode or "T").strip().upper()
    today = datetime.now().date()
    if mode in {"T-1", "T_MINUS_1", "PREV"}:
        day = today - timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day
    return today


def _parse_min_timestamp(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def run_daily_signal_job(
    repo: TradingRepository,
    notifiers: list[Notifier] | None = None,
    available_cash: float = 10_000,
    *,
    trade_date_mode: str = "T",
    min_data_timestamp: date | datetime | str | None = None,
    rank_by: str = "score",
    max_open_positions: int | None = None,
) -> dict:
    trade_date = _resolve_trade_date(trade_date_mode)
    suggestions = compute_daily_signal_suggestions(
        repo=repo,
        trade_date=trade_date,
        available_cash=float(available_cash),
        require_chip=True,
        block_disposition=True,
        rank_by=rank_by,
        max_open_positions=max_open_positions,
        data_freshness_required=True,
        chip_threshold_mode="zscore",
    )
    min_data_date = _parse_min_timestamp(min_data_timestamp)
    rejected_by_timestamp = 0
    if min_data_date is not None:
        filtered = []
        for item in suggestions:
            try:
                last_day = datetime.strptime(str(item.last_bar_date), "%Y-%m-%d").date()
            except Exception:
                last_day = date.min
            if last_day < min_data_date:
                rejected_by_timestamp += 1
                continue
            filtered.append(item)
        suggestions = filtered

    sent = 0
    failed = 0
    notifiers = notifiers or []
    if notifiers:
        for item in suggestions:
            name = repo.get_instrument_name(item.symbol)
            symbol_text = f"{item.symbol} {name}".strip()
            message = (
                f"[訊號] {symbol_text}, 現價 {item.price:.2f}, 符合進場, "
                f"建議買進 {item.qty} 股, 預估總成本 {item.estimated_total_cost:.0f} 元 (含預估手續費), "
                f"RSI3={item.rsi3:.2f}, K={item.k:.2f}, D={item.d:.2f}, "
                f"法人5日淨買超={item.chip_net_buy_5d:.0f}, 籌碼連3增={item.chip_concentration_up3}, "
                f"score={item.score:.2f}, risk_score={item.risk_score:.2f}"
            )
            for notifier in notifiers:
                try:
                    notifier.send(message)
                    sent += 1
                except Exception:
                    failed += 1
    return {
        "suggestions": len(suggestions),
        "sent": sent,
        "failed": failed,
        "channels": [type(n).__name__ for n in notifiers],
        "available_cash": float(available_cash),
        "trade_date_mode": str(trade_date_mode or "T"),
        "trade_date": trade_date.isoformat(),
        "rank_by": rank_by,
        "max_open_positions": max_open_positions,
        "min_data_timestamp": min_data_date.isoformat() if min_data_date else None,
        "rejected_by_min_data_timestamp": rejected_by_timestamp,
    }


def start_scheduler(
    repo: TradingRepository,
    notifiers: list[Notifier] | None = None,
    available_cash: float = 10_000,
    hour: int = 13,
    minute: int = 40,
    trade_date_mode: str = "T",
    rank_by: str = "score",
    max_open_positions: int | None = None,
) -> None:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("apscheduler is not installed. Please `pip install apscheduler`.") from exc

    scheduler = BlockingScheduler(timezone="Asia/Taipei")
    scheduler.add_job(
        lambda: run_daily_signal_job(
            repo=repo,
            notifiers=notifiers,
            available_cash=available_cash,
            trade_date_mode=trade_date_mode,
            rank_by=rank_by,
            max_open_positions=max_open_positions,
        ),
        trigger="cron",
        day_of_week="mon-fri",
        hour=int(hour),
        minute=int(minute),
        id="daily_signal_job",
        replace_existing=True,
    )
    scheduler.start()
