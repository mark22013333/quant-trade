from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.broker.shioaji_account_service import ShioajiAccountService
from app.execution import OrderIntent
from app.execution.order_preview import OrderPreviewService
from app.execution.promotion_gate import PromotionGate
from app.security import redact_sensitive
from web.services import (
    ShioajiGateway,
    ShioajiWorkflowService,
    StrategyRunConfig,
    StrategyWorkflowService,
)
from web.services.shioaji_workflow import OrderTestConfig

CONTROL_PANEL_HTML = Path("web/control_panel.html")
CONTROL_PANEL_CSS = Path("web/control_panel.css")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports"
LOG_PATH = REPORTS_DIR / "control_panel.log"
ENV_PATH = PROJECT_ROOT / ".env"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _append_log_line(action: str, message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {action} | {message}\n")
    except Exception:
        return


@asynccontextmanager
async def _lifespan(_: FastAPI):
    _append_log_line("server", "control panel started")
    try:
        from app.db.session import init_db

        init_db()
        _append_log_line("server", "app DB schema initialized")
    except Exception as exc:  # noqa: BLE001
        _append_log_line("server", f"app DB schema init skipped: {exc}")
    yield


app = FastAPI(title="Quant-Trade Control Panel", lifespan=_lifespan)


def _control_panel_token() -> str:
    return os.getenv("CONTROL_PANEL_TOKEN", "").strip()


def _is_loopback_host(host: str | None) -> bool:
    value = str(host or "").split(":")[0].strip().lower()
    return value in {"", "127.0.0.1", "localhost", "::1", "testclient", "testserver"}


def _request_token(request: Request) -> str:
    auth = str(request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.headers.get("x-control-panel-token") or "").strip()


def _with_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


def _safe_error_text(exc: Exception) -> str:
    redacted = redact_sensitive(str(exc))
    return redacted if isinstance(redacted, str) and redacted else "internal_error"


def _safe_error_response(code: str = "internal_error", status_code: int = 500) -> JSONResponse:
    return JSONResponse({"status": "error", "error": code}, status_code=status_code)


@app.middleware("http")
async def _security_guard(request: Request, call_next):
    token = _control_panel_token()
    client_host = request.client.host if request.client else ""
    bind_host = os.getenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    external_bind = not _is_loopback_host(bind_host)
    external_client = not _is_loopback_host(client_host)

    if token:
        protected = request.url.path.startswith("/api") or request.url.path.startswith("/reports")
        if protected and _request_token(request) != token:
            return _with_security_headers(JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401))
    elif external_bind or external_client:
        return _with_security_headers(
            JSONResponse(
                {"status": "error", "error": "control panel requires CONTROL_PANEL_TOKEN outside localhost"},
                status_code=403,
            )
        )
    response = await call_next(request)
    if request.url.path.startswith("/api") or request.url.path.startswith("/reports"):
        return _with_security_headers(response)
    return response


@app.middleware("http")
async def _log_http(request: Request, call_next):
    start = time.time()
    _append_log_line("http", f"START {request.method} {request.url.path}")
    try:
        response = await call_next(request)
    except Exception as exc:
        _append_log_line("http", f"{request.method} {request.url.path} ERROR {exc}")
        raise
    duration_ms = int((time.time() - start) * 1000)
    _append_log_line("http", f"END {request.method} {request.url.path} {response.status_code} {duration_ms}ms")
    return response


class ShortTermRequest(BaseModel):
    top_n: int = Field(default=20, ge=5, le=200)
    preselect_n: int = Field(default=300, ge=50, le=1000)
    lookback_days: int = Field(default=90, ge=30, le=365)


class SwingReportRequest(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    lookback_days: int = Field(default=365, ge=60, le=730)


class AIAssistantRequest(BaseModel):
    force: bool = False


class AccountBalanceRequest(BaseModel):
    simulation: bool = False


class ShioajiTestRequest(BaseModel):
    simulation: bool = True
    allow_live_order: bool = False
    live_order_nonce: str = Field(default="", max_length=120)
    stock_code: str = Field(default="2890", min_length=3, max_length=12)
    stock_quantity: int = Field(default=1, ge=1, le=10)
    stock_price: float | None = Field(default=None, gt=0)
    futures_code: str = Field(default="TXF", min_length=2, max_length=12)
    futures_quantity: int = Field(default=1, ge=1, le=10)
    futures_price: float | None = Field(default=None, gt=0)
    interval_sec: float = Field(default=1.1, ge=1.0, le=5.0)


class StockOrderPreviewRequest(BaseModel):
    simulation: bool = True
    symbol: str = Field(default="2330", min_length=2, max_length=24)
    side: str = Field(default="buy", pattern="^(buy|sell)$")
    price: float = Field(gt=0)
    quantity: int = Field(ge=1, le=1_000_000)
    available_cash: float | None = Field(default=None, ge=0)
    position_before: int = Field(default=0, ge=0)
    strategy_name: str = Field(default="manual", max_length=80)
    strategy_version: str = Field(default="manual", max_length=80)
    signal_id: str = Field(default="manual", max_length=120)


class AdvisorProposalRequest(BaseModel):
    symbol: str = Field(default="2330", min_length=2, max_length=24)
    trade_date: str | None = None
    available_cash: float = Field(default=10_000, ge=0, le=50_000_000)
    position_qty: int = Field(default=0, ge=0, le=1_000_000)
    create_preview: bool = True
    simulation: bool = True
    advisor_provider: str = Field(default="stub", pattern="^(stub|codex)$")


class AdvisorRejectRequest(BaseModel):
    decision_id: str = Field(min_length=8, max_length=80)
    reason: str = Field(default="manual_rejected", max_length=500)


class AdvisorBacktestRequest(BaseModel):
    symbol: str = Field(default="2330", min_length=2, max_length=24)
    start_date: str
    end_date: str
    initial_cash: float = Field(default=10_000, ge=1_000, le=50_000_000)
    max_days: int = Field(default=20, ge=2, le=60)
    advisor_provider: str = Field(default="stub", pattern="^(stub|codex)$")


class OrderApproveExecuteRequest(BaseModel):
    preview_id: str = Field(min_length=8, max_length=80)
    manual_confirmed: bool = False
    promotion_gate_accepted: bool = False
    live_order_nonce: str = Field(default="", max_length=120)
    simulation: bool = True


class PromotionGateRequest(BaseModel):
    strategy_name: str = Field(default="manual", max_length=80)
    strategy_version: str = Field(default="manual", max_length=80)
    paper_days: int = Field(default=0, ge=0)
    paper_trades: int = Field(default=0, ge=0)
    max_drawdown: float = Field(default=0.0, ge=0.0, le=1.0)
    slippage_report: Dict[str, Any] = Field(default_factory=dict)
    data_quality_blocked: bool = False
    reconciliation_matched: bool = False
    single_order_value: float = Field(default=0.0, ge=0.0)
    daily_order_value: float = Field(default=0.0, ge=0.0)
    daily_order_count: int = Field(default=0, ge=0)


class StrategyBacktestRequest(BaseModel):
    symbol: str = Field(default="2330.TW", min_length=2, max_length=24)
    market: str = Field(default="TW", min_length=2, max_length=8)
    start_date: str | None = None
    end_date: str | None = None
    enabled: Dict[str, bool] = Field(
        default_factory=lambda: {
            "momentum_trend": True,
            "mean_reversion": True,
            "chip_flow": True,
        }
    )
    weights: Dict[str, float] = Field(
        default_factory=lambda: {
            "momentum_trend": 0.4,
            "mean_reversion": 0.3,
            "chip_flow": 0.3,
        }
    )
    threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    strategy_params: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    risk_config: Dict[str, Any] = Field(default_factory=dict)
    backtest_config: Dict[str, Any] = Field(default_factory=dict)


class PaperLedgerRequest(BaseModel):
    symbol: str = Field(default="2330", min_length=2, max_length=24)
    start_date: str | None = None
    end_date: str | None = None
    initial_cash: float = Field(default=10_000, ge=1_000, le=5_000_000)
    hold_days: int = Field(default=5, ge=1, le=20)
    settlement_days: int = Field(default=2, ge=1, le=5)
    force_close_end: bool = True


class DataSyncRequest(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    symbols: str = Field(default="", max_length=5000)
    include_bars: bool = True
    include_chip: bool = True
    include_broker_agg: bool = True
    include_disposition: bool = True


class FeatureRebuildRequest(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    symbols: str = Field(default="", max_length=5000)


class SignalPreviewRequest(BaseModel):
    trade_date: str | None = None
    trade_date_mode: str = "T"
    available_cash: float = Field(default=10_000, ge=1_000, le=5_000_000)
    max_symbols: int = Field(default=50, ge=1, le=300)
    require_chip: bool = True
    block_disposition: bool = True
    rank_by: str = "score"
    max_open_positions: int | None = Field(default=None, ge=1, le=30)
    data_freshness_required: bool = False
    chip_threshold_mode: str = "absolute"


class OneClickPipelineRequest(BaseModel):
    trade_date: str | None = None
    target_count: int = Field(default=8, ge=5, le=10)
    risk_profile: str = Field(default="balanced")
    universe: str = Field(default="twse_tpex")
    data_freshness_required: bool = True
    symbols_csv: str = Field(default="", max_length=5000)


class DailyRadarRequest(BaseModel):
    trade_date: str | None = None
    target_count: int = Field(default=15, ge=10, le=20)
    risk_profile: str = Field(default="balanced")
    universe: str = Field(default="twse_tpex")
    data_freshness_required: bool = True
    symbols_csv: str = Field(default="", max_length=5000)


def _normalize_tw_symbol(raw: str) -> str:
    code = str(raw or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


def _parse_symbols_csv(text: str) -> list[str] | None:
    items = [item.strip() for item in str(text or "").split(",") if item.strip()]
    if not items:
        return None
    return sorted({_normalize_tw_symbol(item) for item in items if _normalize_tw_symbol(item)})


_status_lock = threading.RLock()
_status: Dict[str, Dict[str, object]] = {}
_active_action_lock = threading.RLock()
_active_actions: set[str] = set()
MAX_ACTIVE_ACTIONS = max(1, int(os.getenv("CONTROL_PANEL_MAX_ACTIVE_ACTIONS", "3")))
SHIOAJI_LOCK = threading.Lock()
SHIOAJI_LOCK_TIMEOUT_SEC = 16

SHIOAJI_GATEWAY = ShioajiGateway(ENV_PATH)
SHIOAJI_SERVICE = ShioajiWorkflowService(SHIOAJI_GATEWAY)
SHIOAJI_ACCOUNT_SERVICE = ShioajiAccountService(env_path=str(ENV_PATH))
ORDER_PREVIEW_SERVICE = OrderPreviewService()
PROMOTION_GATE = PromotionGate()
STRATEGY_SERVICE = StrategyWorkflowService()


def _init_action(action: str) -> None:
    _status.setdefault(
        action,
        {
            "state": "idle",
            "progress": 0,
            "message": "尚未執行",
            "log": [],
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "started_at": None,
            "result": None,
        },
    )


def _update(
    action: str,
    *,
    state: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    append_log: str | None = None,
    result: Dict[str, object] | None = None,
) -> None:
    with _status_lock:
        _init_action(action)
        entry = _status[action]
        if state is not None:
            entry["state"] = state
            if state == "running" and entry.get("started_at") is None:
                entry["started_at"] = time.time()
            if state in {"done", "error"}:
                entry["finished_at"] = time.time()
        if progress is not None:
            entry["progress"] = progress
        if message is not None:
            entry["message"] = redact_sensitive(message)
        if append_log:
            safe_log = _safe_error_text(Exception(append_log))
            entry.setdefault("log", []).insert(0, safe_log)
            entry["log"] = entry["log"][:80]
            _append_log_line(action, safe_log)
        if result is not None:
            entry["result"] = redact_sensitive(result)
        entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _snapshot(action: str) -> Dict[str, object]:
    with _status_lock:
        _init_action(action)
        snap = dict(_status[action])
        started_at = snap.get("started_at")
        if started_at:
            snap["elapsed"] = int(time.time() - started_at)
        else:
            snap["elapsed"] = 0
        return snap


def _is_running(action: str) -> bool:
    return _snapshot(action).get("state") == "running"


def _acquire_shioaji_lock(action: str) -> bool:
    _update(action, state="running", progress=5, message="等待交易 API 佇列…", append_log="等待 Shioaji 鎖")
    acquired = SHIOAJI_LOCK.acquire(timeout=SHIOAJI_LOCK_TIMEOUT_SEC)
    if not acquired:
        _update(action, state="error", progress=0, message="交易 API 忙碌中，請稍後重試", append_log="等待 Shioaji 鎖超時")
        return False
    _update(action, progress=10, message="取得交易 API 鎖", append_log="取得 Shioaji 鎖")
    return True


def _release_shioaji_lock() -> None:
    if SHIOAJI_LOCK.locked():
        try:
            SHIOAJI_LOCK.release()
        except RuntimeError:
            return


def _finalize_test_result(action: str, result: Dict[str, object]) -> None:
    passed = result.get("passed")
    message = str(result.get("message") or "執行完成")
    next_steps = result.get("next_steps")
    first_step = ""
    if isinstance(next_steps, list) and next_steps:
        first_step = str(next_steps[0])
    if passed is True:
        _update(
            action,
            state="done",
            progress=100,
            message=f"測試通過：{message}",
            append_log=f"PASS | {message}",
            result=result,
        )
        return
    if passed is False:
        tip_suffix = f"｜建議：{first_step}" if first_step else ""
        _update(
            action,
            state="done",
            progress=100,
            message=f"測試未通過：{message}{tip_suffix}",
            append_log=f"FAIL | {message}",
            result=result,
        )
        return
    _update(action, state="done", progress=100, message=message, result=result)


def _persist_execution_payload(payload: Dict[str, object] | None) -> None:
    if not isinstance(payload, dict):
        return
    intent = payload.get("intent")
    execution = payload.get("execution")
    if not isinstance(intent, dict) or not isinstance(execution, dict):
        return
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            repo.add_trading_execution_record(intent=intent, result=execution)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        _append_log_line("execution-record", f"persist skipped: {exc}")


def _run_short_term(req: ShortTermRequest) -> None:
    action = "short-term"
    try:
        from analysis.short_term_ranker import run_short_term_ranking
        from dashboard_generator import generate_dashboard

        _update(action, state="running", progress=5, message="準備資料來源…")

        def progress_fn(current: int, total: int) -> None:
            pct = 5 + int(80 * current / max(total, 1))
            _update(action, progress=pct, message=f"計算中 {current}/{total}")

        output = run_short_term_ranking(
            top_n=req.top_n,
            preselect_n=req.preselect_n,
            lookback_days=req.lookback_days,
            progress_fn=progress_fn,
        )
        _update(action, progress=90, message="產生 Dashboard…")
        html_path = generate_dashboard(output.full_df, output.top20_df)
        _update(
            action,
            state="done",
            progress=100,
            message="短期投資 Dashboard 完成",
            append_log=f"報表: /reports/{html_path.name}",
        )
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_swing(req: SwingReportRequest) -> None:
    action = "swing-report"
    try:
        from swing_analysis import generate_swing_report

        _update(action, state="running", progress=10, message="啟動波段分析…")
        report_path = generate_swing_report(
            open_browser=False,
            start_date=req.start_date,
            end_date=req.end_date,
            lookback_days=req.lookback_days,
        )
        if not report_path:
            raise RuntimeError("無法產生波段報表")
        report_path = Path(report_path)
        _update(action, progress=90, message="整理報表…")
        _update(
            action,
            state="done",
            progress=100,
            message="波段報表完成",
            append_log=f"報表: /reports/{report_path.name}",
        )
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_ai_assistant(req: AIAssistantRequest) -> None:
    action = "ai-assistant"
    try:
        from ai_assistant_dashboard import generate_ai_dashboard
        from tools.shioaji_ai_sync import sync_ai_docs

        _update(action, state="running", progress=20, message="同步官方文件…")
        sync_ai_docs(force=req.force)
        _update(action, progress=70, message="產生 AI 協作中心…")
        html_path = generate_ai_dashboard()
        _update(
            action,
            state="done",
            progress=100,
            message="AI 協作中心完成",
            append_log=f"報表: /reports/{html_path.name}",
        )
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_strategy_backtest(req: StrategyBacktestRequest) -> None:
    action = "strategy-backtest"
    try:
        _update(action, state="running", progress=10, message=f"載入資料：{req.symbol}…", append_log="載入策略回測資料")
        cfg = _build_strategy_config(req)
        _update(action, progress=40, message="執行多策略回測…", append_log="執行多策略回測")
        result = STRATEGY_SERVICE.run_multi_strategy_backtest(cfg)
        passed = bool(result.get("passed"))
        metrics = result.get("metrics", {})
        trade_count = metrics.get("trade_count", 0)
        total_return = float(metrics.get("total_return") or 0.0)
        if passed:
            msg = f"回測完成：交易 {trade_count} 筆，總報酬 {total_return:.2%}"
        else:
            msg = str(result.get("message") or "回測完成")
        _update(
            action,
            state="done",
            progress=100,
            message=msg,
            append_log=msg,
            result=result,
        )
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _build_strategy_config(req: StrategyBacktestRequest) -> StrategyRunConfig:
    return StrategyRunConfig(
        symbol=req.symbol,
        market=req.market,
        start_date=req.start_date,
        end_date=req.end_date,
        enabled=req.enabled,
        weights=req.weights,
        threshold=req.threshold,
        strategy_params=req.strategy_params,
        risk_config=req.risk_config,
        backtest_config=req.backtest_config,
    )


def _run_strategy_backtest_export(req: StrategyBacktestRequest) -> None:
    action = "strategy-backtest-export"
    try:
        _update(action, state="running", progress=10, message=f"載入資料：{req.symbol}…", append_log="載入策略回測資料")
        cfg = _build_strategy_config(req)
        _update(action, progress=45, message="執行回測與匯出…", append_log="執行回測與匯出 artifacts")
        result = STRATEGY_SERVICE.run_multi_strategy_backtest_export(cfg, output_dir=REPORTS_DIR)
        files = result.get("export", {}).get("files", {})
        message = f"匯出完成（{len(files)} 個檔案）" if files else str(result.get("message") or "匯出完成")
        _update(
            action,
            state="done",
            progress=100,
            message=message,
            append_log=message,
            result=result,
        )
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_data_sync(req: DataSyncRequest) -> None:
    action = "data-sync"
    try:
        _update(action, state="running", progress=10, message="初始化資料庫與同步任務…", append_log="init data sync")
        try:
            from app.data.sync_service import SyncService
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        end_date = datetime.strptime(req.end_date, "%Y-%m-%d").date() if req.end_date else datetime.now().date()
        start_date = datetime.strptime(req.start_date, "%Y-%m-%d").date() if req.start_date else (end_date - timedelta(days=365))
        symbols = _parse_symbols_csv(req.symbols)

        _update(action, progress=45, message="同步 FinMind 市場資料…", append_log=f"{start_date}~{end_date} symbols={symbols or '0050'}")
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            service = SyncService(repo=repo)
            result = service.sync_market_bundle(
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
                include_bars=bool(req.include_bars),
                include_institutional=bool(req.include_chip),
                include_broker_agg=bool(req.include_broker_agg),
                include_disposition=bool(req.include_disposition),
            )
            session.commit()

        total = int(result.get("rows_upserted_total", 0))
        partial = bool(result.get("partial_failure"))
        msg = f"資料同步完成：{total} 筆" if not partial else f"資料同步完成（部分失敗）：{total} 筆"
        _update(action, state="done", progress=100, message=msg, append_log=msg, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_feature_rebuild(req: FeatureRebuildRequest) -> None:
    action = "feature-rebuild"
    try:
        _update(action, state="running", progress=10, message="準備重建特徵快照…", append_log="init feature rebuild")
        try:
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
            from app.features.snapshot_builder import rebuild_feature_snapshots
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        end_date = datetime.strptime(req.end_date, "%Y-%m-%d").date() if req.end_date else datetime.now().date()
        start_date = datetime.strptime(req.start_date, "%Y-%m-%d").date() if req.start_date else (end_date - timedelta(days=365))
        symbols = _parse_symbols_csv(req.symbols)

        _update(action, progress=55, message="計算技術/籌碼/處置特徵…", append_log=f"{start_date}~{end_date} symbols={symbols or '0050'}")
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            result = rebuild_feature_snapshots(
                repo=repo,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
            )
            session.commit()

        rows = int(result.get("rows_upserted", 0))
        failed_count = int(result.get("failed_count", 0))
        msg = f"特徵重建完成：{rows} 筆" if failed_count == 0 else f"特徵重建完成（失敗 {failed_count} 檔）"
        _update(action, state="done", progress=100, message=msg, append_log=msg, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_signal_preview(req: SignalPreviewRequest) -> None:
    action = "signal-preview"
    try:
        _update(action, state="running", progress=15, message="計算今日訊號建議…", append_log="run signal preview")
        try:
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
            from app.services.trading_workflow import SignalPreviewWorkflow, build_signal_preview_payload
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        trade_date_mode = str(req.trade_date_mode or "T").strip().upper()
        if trade_date_mode not in {"T", "T-1"}:
            trade_date_mode = "T"
        if req.trade_date:
            trade_date = datetime.strptime(req.trade_date, "%Y-%m-%d").date()
        else:
            offset_days = 1 if trade_date_mode == "T-1" else 0
            trade_date = (datetime.now() - timedelta(days=offset_days)).date()
        rank_by = str(req.rank_by or "score").strip()
        chip_threshold_mode = str(req.chip_threshold_mode or "absolute").strip().lower()
        max_open_positions = int(req.max_open_positions) if req.max_open_positions is not None else None
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            result = build_signal_preview_payload(
                repo=repo,
                request=SignalPreviewWorkflow(
                    trade_date=trade_date,
                    available_cash=float(req.available_cash),
                    max_symbols=int(req.max_symbols),
                    require_chip=bool(req.require_chip),
                    block_disposition=bool(req.block_disposition),
                    rank_by=rank_by,
                    max_open_positions=max_open_positions,
                    data_freshness_required=bool(req.data_freshness_required),
                    chip_threshold_mode=chip_threshold_mode,
                ),
            )
        result["trade_date_mode"] = trade_date_mode if not req.trade_date else "explicit"
        _update(action, state="done", progress=100, message=result["message"], append_log=result["message"], result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_paper_ledger(req: PaperLedgerRequest) -> None:
    action = "paper-ledger"
    try:
        _update(action, state="running", progress=10, message="準備帳本模擬參數…", append_log="初始化 paper ledger")
        try:
            from app.data.sync_service import SyncService
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
            from app.paper.ledger import PaperLedgerConfig, export_paper_ledger_report, run_symbol_paper_ledger
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        # Ensure DB schema exists (daily_bars / instruments / ...).
        init_db()

        end_date = datetime.strptime(req.end_date, "%Y-%m-%d").date() if req.end_date else datetime.now().date()
        start_date = datetime.strptime(req.start_date, "%Y-%m-%d").date() if req.start_date else (end_date - timedelta(days=365))
        config = PaperLedgerConfig(
            initial_cash=float(req.initial_cash),
            hold_days=int(req.hold_days),
            settlement_days=int(req.settlement_days),
            force_close_end=bool(req.force_close_end),
        )

        _update(action, progress=35, message="讀取資料庫歷史資料…", append_log=f"symbol={req.symbol} {start_date}~{end_date}")
        session_factory = get_session_factory()
        auto_sync_result: dict[str, Any] | None = None
        with session_factory() as session:
            repo = TradingRepository(session)
            simulation = run_symbol_paper_ledger(
                repo=repo,
                symbol=req.symbol,
                start_date=start_date,
                end_date=end_date,
                config=config,
            )

        if not simulation.get("passed"):
            message = str(simulation.get("message") or "模擬失敗")
            if message == "no_data":
                symbol_code = _normalize_tw_symbol(req.symbol)
                _update(action, progress=55, message="找不到歷史資料，嘗試自動同步 daily bars…", append_log=f"auto-sync {symbol_code}")
                with session_factory() as session:
                    repo = TradingRepository(session)
                    sync_service = SyncService(repo=repo)
                    auto_sync_result = sync_service.sync_daily_bars(
                        start_date=start_date,
                        end_date=end_date,
                        symbols=[symbol_code],
                    )
                    session.commit()
                synced_rows = int(auto_sync_result.get("rows_upserted") or 0)
                failed_count = int(auto_sync_result.get("failed_count") or 0)
                _update(
                    action,
                    progress=70,
                    message=f"自動同步完成：{synced_rows} 筆",
                    append_log=f"auto-sync rows={synced_rows}, failed={failed_count}",
                )

                if synced_rows > 0:
                    _update(action, progress=80, message="重新執行帳本模擬…", append_log="retry paper ledger")
                    with session_factory() as session:
                        repo = TradingRepository(session)
                        simulation = run_symbol_paper_ledger(
                            repo=repo,
                            symbol=req.symbol,
                            start_date=start_date,
                            end_date=end_date,
                            config=config,
                        )

                if not simulation.get("passed"):
                    simulation["next_steps"] = [
                        "確認 data/stock_data 內有對應 parquet（例如 2330.TW_1d.parquet）",
                        "手動同步：python -m app.cli sync-bars --start-date 2024-01-01 --symbols 2330",
                        "重新執行模擬本金帳本",
                    ]
                    message = "資料庫無該標的歷史資料，且自動同步未取得可用資料"

            if not simulation.get("passed"):
                _update(action, state="done", progress=100, message=message, append_log=message, result=simulation)
                return

        _update(action, progress=75, message="輸出 HTML/CSV/JSON 報表…", append_log="寫入 reports 檔案")
        export = export_paper_ledger_report(simulation, output_dir=REPORTS_DIR, symbol=req.symbol)
        summary = simulation.get("summary", {})
        result = {
            "passed": True,
            "symbol": summary.get("symbol", req.symbol),
            "message": "模擬帳本已完成，請查看 html_report",
            "summary": summary,
            "export": export,
            "trades_preview": simulation.get("trades", [])[-10:],
            "snapshots_preview": simulation.get("snapshots", [])[-10:],
        }
        if auto_sync_result is not None:
            result["sync_result"] = auto_sync_result
        html_url = export.get("urls", {}).get("html_report", "")
        done_msg = f"模擬帳本完成：{summary.get('trade_count', 0)} 筆交易"
        _update(action, state="done", progress=100, message=done_msg, append_log=f"{done_msg} | {html_url}", result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_invest_pipeline(req: OneClickPipelineRequest) -> None:
    action = "invest-pipeline"
    try:
        _update(action, state="running", progress=8, message="初始化一鍵流程…", append_log="init one-click pipeline")
        try:
            from app.data.sync_service import SyncService
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
            from app.pipeline import InvestPipelineService
            from app.pipeline import OneClickPipelineRequest as PipelineRequest
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        session_factory = get_session_factory()

        def _progress(progress: int, message: str, append_log: str | None = None) -> None:
            _update(action, progress=progress, message=message, append_log=append_log)

        with session_factory() as session:
            repo = TradingRepository(session)
            sync_service = SyncService(repo=repo)
            service = InvestPipelineService(repo=repo, sync_service=sync_service)
            symbols = _parse_symbols_csv(req.symbols_csv)
            result = service.run(
                PipelineRequest(
                    trade_date=req.trade_date,
                    target_count=int(req.target_count),
                    risk_profile=str(req.risk_profile),
                    universe=str(req.universe),
                    data_freshness_required=bool(req.data_freshness_required),
                    symbols=symbols,
                ),
                progress_hook=_progress,
            )
            session.commit()
        count = len(result.get("candidates") or [])
        message = f"一鍵流程完成：輸出 {count} 檔候選"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_daily_radar(req: DailyRadarRequest) -> None:
    action = "daily-radar"
    try:
        _update(action, state="running", progress=6, message="初始化每日雷達…", append_log="init daily radar")
        try:
            from app.data.sync_service import SyncService
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
            from app.pipeline import DailyRadarRequest as RadarRequest
            from app.pipeline import DailyRadarService
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        session_factory = get_session_factory()

        def _progress(progress: int, message: str, append_log: str | None = None) -> None:
            _update(action, progress=progress, message=message, append_log=append_log)

        with session_factory() as session:
            repo = TradingRepository(session)
            sync_service = SyncService(repo=repo)
            service = DailyRadarService(repo=repo, sync_service=sync_service)
            symbols = _parse_symbols_csv(req.symbols_csv)
            result = service.run(
                RadarRequest(
                    trade_date=req.trade_date,
                    target_count=int(req.target_count),
                    risk_profile=str(req.risk_profile),
                    universe=str(req.universe),
                    data_freshness_required=bool(req.data_freshness_required),
                    symbols=symbols,
                ),
                progress_hook=_progress,
            )
            session.commit()

        count = len(result.get("items") or [])
        labels = result.get("label_counts") or {}
        ready = int(labels.get("ENTRY_READY", 0) or 0)
        watch = int(labels.get("WATCH_WAIT_TRIGGER", 0) or 0)
        message = f"每日雷達完成：{count} 檔（可進場 {ready} / 觀察 {watch}）"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_account_balance(req: AccountBalanceRequest) -> None:
    action = "account-balance"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=40, message="查詢交割帳戶餘額…", append_log="執行 account_balance")
        result = SHIOAJI_SERVICE.query_account_balance(req.simulation)
        amount = result.get("acc_balance")
        mode = result.get("mode", "帳戶")
        if isinstance(amount, (int, float)):
            message = f"{mode} 餘額：{amount:,.0f}"
        else:
            message = f"{mode} 餘額查詢完成"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _run_account_funds(req: AccountBalanceRequest) -> None:
    action = "account-funds"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=40, message="查詢可用買進額度…", append_log="執行 account_funds")
        result = SHIOAJI_SERVICE.query_account_funds(req.simulation)
        mode = result.get("mode", "帳戶")
        amount = result.get("available_amount")
        if isinstance(amount, (int, float)):
            message = f"{mode} 可用買進額度：{amount:,.0f}"
        else:
            message = f"{mode} 可用買進額度查詢完成"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _run_settlements(req: AccountBalanceRequest) -> None:
    action = "settlements"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=45, message="查詢交割明細…", append_log="執行 settlements")
        result = SHIOAJI_SERVICE.query_settlements(req.simulation)
        message = f"{result.get('mode', '帳戶')} 交割明細：{result.get('summary', '完成')}"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _run_positions(req: AccountBalanceRequest) -> None:
    action = "positions"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=45, message="查詢持倉明細…", append_log="執行 positions")
        result = SHIOAJI_SERVICE.query_positions(req.simulation)
        message = f"{result.get('mode', '帳戶')} 持倉明細：{result.get('summary', '完成')}"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _run_account_diagnose(req: AccountBalanceRequest) -> None:
    action = "account-diagnose"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=30, message="執行帳戶診斷…", append_log="執行 account_diagnose")
        result = SHIOAJI_SERVICE.run_account_diagnose(req.simulation)
        message = "診斷完成"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _build_test_config(req: ShioajiTestRequest) -> OrderTestConfig:
    return OrderTestConfig(
        simulation=req.simulation,
        stock_code=req.stock_code,
        stock_quantity=req.stock_quantity,
        stock_price=req.stock_price,
        futures_code=req.futures_code,
        futures_quantity=req.futures_quantity,
        futures_price=req.futures_price,
        interval_sec=req.interval_sec,
        allow_live_order=req.allow_live_order,
        live_order_nonce=req.live_order_nonce,
    )


def _run_shioaji_login_test(req: ShioajiTestRequest) -> None:
    action = "shioaji-login-test"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=35, message="執行登入測試…", append_log="執行 login test")
        result = SHIOAJI_SERVICE.run_login_test(req.simulation)
        _finalize_test_result(action, result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _run_shioaji_stock_test(req: ShioajiTestRequest) -> None:
    action = "shioaji-stock-test"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=35, message="執行證券下單測試…", append_log="執行 stock order test")
        result = SHIOAJI_SERVICE.run_stock_order_test(_build_test_config(req))
        _persist_execution_payload(result)
        _finalize_test_result(action, result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _run_shioaji_futures_test(req: ShioajiTestRequest) -> None:
    action = "shioaji-futures-test"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=35, message="執行期貨下單測試…", append_log="執行 futures order test")
        result = SHIOAJI_SERVICE.run_futures_order_test(_build_test_config(req))
        _finalize_test_result(action, result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _run_shioaji_simulation_suite(req: ShioajiTestRequest) -> None:
    action = "shioaji-simulation-suite"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=25, message="執行模擬整套測試…", append_log="執行 simulation suite")
        result = SHIOAJI_SERVICE.run_simulation_suite(_build_test_config(req))
        checks = result.get("checks") if isinstance(result, dict) else {}
        if isinstance(checks, dict):
            _persist_execution_payload(checks.get("stock_order"))
        _finalize_test_result(action, result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _run_shioaji_verify_production(_req: ShioajiTestRequest) -> None:
    action = "shioaji-verify-production"
    try:
        if not _acquire_shioaji_lock(action):
            return
        _update(action, progress=30, message="檢核正式環境切換條件…", append_log="執行 verify production")
        result = SHIOAJI_SERVICE.verify_production_ready()
        _finalize_test_result(action, result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))
    finally:
        _release_shioaji_lock()


def _tail_log(lines: int = 200) -> List[str]:
    if not LOG_PATH.exists():
        return []
    try:
        content = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    return content[-lines:]


def _start_action(action: str, target, req, busy_error: str, accepted_message: str) -> JSONResponse:
    if _is_running(action):
        return JSONResponse({"status": "error", "error": busy_error})

    with _active_action_lock:
        if len(_active_actions) >= MAX_ACTIVE_ACTIONS:
            return JSONResponse(
                {
                    "status": "error",
                    "error": "control panel task queue is full",
                    "code": "task_queue_full",
                },
                status_code=429,
            )
        _active_actions.add(action)

    def _wrapped_target(payload) -> None:
        try:
            target(payload)
        finally:
            with _active_action_lock:
                _active_actions.discard(action)

    thread = threading.Thread(target=_wrapped_target, args=(req,), daemon=True)
    thread.start()
    _update(action, state="running", progress=1, message="已排程…", append_log=f"收到 {action} 請求")
    return JSONResponse({"status": "ok", "message": accepted_message})


@app.get("/")
def index() -> HTMLResponse:
    html = CONTROL_PANEL_HTML.read_text(encoding="utf-8")
    css = CONTROL_PANEL_CSS.read_text(encoding="utf-8")
    return HTMLResponse(
        html.replace("{{INLINE_CSS}}", css),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/reports")
def list_reports() -> JSONResponse:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        REPORTS_DIR.glob("*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    payload = []
    for path in files[:20]:
        updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        payload.append(
            {
                "name": path.name,
                "url": f"/reports/{path.name}",
                "updated": updated,
            }
        )
    return JSONResponse({"reports": payload})


@app.get("/api/ping")
def ping() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pid": os.getpid(),
        }
    )


@app.get("/api/log")
def get_log() -> JSONResponse:
    return JSONResponse({"status": "ok", "lines": _tail_log(200)})


@app.get("/api/shioaji/env")
def shioaji_env() -> JSONResponse:
    return JSONResponse({"status": "ok", "data": redact_sensitive(SHIOAJI_GATEWAY.env_status())})


@app.get("/api/tw-live/health")
def tw_live_health(simulation: bool = True) -> JSONResponse:
    try:
        result = SHIOAJI_ACCOUNT_SERVICE.run_health_check(simulation=bool(simulation))
        return JSONResponse({"status": "ok", "data": result.to_dict()})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-health", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/tw-live/account-snapshot")
def tw_live_account_snapshot(simulation: bool = True) -> JSONResponse:
    try:
        result = SHIOAJI_ACCOUNT_SERVICE.get_account_snapshot(simulation=bool(simulation))
        return JSONResponse({"status": "ok", "data": result.to_dict()})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-account-snapshot", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/tw-live/order-preview")
def tw_live_order_preview(req: StockOrderPreviewRequest) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        intent = OrderIntent(
            source="web",
            environment="simulation" if req.simulation else "live",
            symbol=req.symbol,
            side=req.side,  # type: ignore[arg-type]
            price=float(req.price),
            quantity=int(req.quantity),
            strategy_name=req.strategy_name,
            signal_id=req.signal_id,
            metadata={"strategy_version": req.strategy_version},
        )
        estimated_total_cost = float(req.price) * int(req.quantity) if req.side == "buy" else 0.0
        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            preview = ORDER_PREVIEW_SERVICE.create_preview(
                intent=intent,
                estimated_total_cost=estimated_total_cost,
                available_cash=req.available_cash,
                position_before=int(req.position_before),
                checks=[{"name": "web_preview", "passed": True}],
                strategy_version=req.strategy_version,
                signal_id=req.signal_id,
                repository=repo,
            )
            session.commit()
        return JSONResponse({"status": "ok", "data": preview.to_dict()})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-order-preview", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/advisor/proposals")
def advisor_proposals(req: AdvisorProposalRequest) -> JSONResponse:
    try:
        from app.advisor import AdvisorWorkflowService, build_advisor
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        trade_date = datetime.strptime(req.trade_date, "%Y-%m-%d").date() if req.trade_date else datetime.now().date()
        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            service = AdvisorWorkflowService(repo=repo, advisor=build_advisor(req.advisor_provider), preview_service=ORDER_PREVIEW_SERVICE)
            data = service.create_proposal(
                symbol=req.symbol,
                trade_date=trade_date,
                available_cash=float(req.available_cash),
                position_qty=int(req.position_qty),
                create_preview=bool(req.create_preview),
                environment="simulation" if req.simulation else "live",
            )
            session.commit()
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("advisor-proposals", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/advisor/reject")
def advisor_reject(req: AdvisorRejectRequest) -> JSONResponse:
    try:
        from app.advisor import AdvisorWorkflowService
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = AdvisorWorkflowService(repo=repo, preview_service=ORDER_PREVIEW_SERVICE).reject_decision(
                decision_id=req.decision_id,
                reason=req.reason,
            )
            session.commit()
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("advisor-reject", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/advisor/backtest")
def advisor_backtest(req: AdvisorBacktestRequest) -> JSONResponse:
    try:
        from app.advisor import AdvisorBacktestService, build_advisor
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        start_date = datetime.strptime(req.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(req.end_date, "%Y-%m-%d").date()
        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = AdvisorBacktestService(repo=repo, advisor=build_advisor(req.advisor_provider)).run_isolated(
                symbol=req.symbol,
                start_date=start_date,
                end_date=end_date,
                initial_cash=float(req.initial_cash),
                max_days=int(req.max_days),
            )
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("advisor-backtest", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/advisor/backtest/export")
def advisor_backtest_export(req: AdvisorBacktestRequest) -> JSONResponse:
    try:
        from app.advisor import AdvisorBacktestService, build_advisor
        from app.advisor.backtest import export_advisor_backtest_report
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        start_date = datetime.strptime(req.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(req.end_date, "%Y-%m-%d").date()
        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = AdvisorBacktestService(repo=repo, advisor=build_advisor(req.advisor_provider)).run_isolated(
                symbol=req.symbol,
                start_date=start_date,
                end_date=end_date,
                initial_cash=float(req.initial_cash),
                max_days=int(req.max_days),
            )
        export = export_advisor_backtest_report(data, output_dir=REPORTS_DIR, symbol=req.symbol)
        return JSONResponse({"status": "ok", "data": {"backtest": data, "export": export}})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("advisor-backtest-export", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/tw-live/order-approve-execute")
def tw_live_order_approve_execute(req: OrderApproveExecuteRequest) -> JSONResponse:
    try:
        from app.broker.shioaji_gateway import ShioajiConfig, ShioajiGateway as CoreShioajiGateway
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.execution import TradingExecutionService

        if not bool(req.manual_confirmed):
            return JSONResponse({"status": "error", "error": "manual_confirmation_required"}, status_code=400)
        if not bool(req.promotion_gate_accepted):
            return JSONResponse({"status": "error", "error": "promotion_gate_required"}, status_code=400)

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            preview = repo.get_order_preview_record(req.preview_id)
            if preview is None:
                return JSONResponse({"status": "error", "error": "preview_not_found"}, status_code=404)
            requested_environment = "simulation" if req.simulation else "live"
            preview_intent = preview.get("intent") if isinstance(preview.get("intent"), dict) else {}
            preview_environment = str(preview_intent.get("environment") or requested_environment)
            if preview_environment != requested_environment:
                return JSONResponse(
                    {
                        "status": "error",
                        "error": "preview_environment_mismatch",
                        "preview_environment": preview_environment,
                        "requested_environment": requested_environment,
                    },
                    status_code=400,
                )
            intent = OrderIntent(
                source="web",
                environment=requested_environment,
                symbol=str(preview["symbol"]),
                side=str(preview["side"]),  # type: ignore[arg-type]
                price=float(preview["price"]),
                quantity=int(preview["quantity"]),
                strategy_name=str(preview["strategy_name"]),
                signal_id=str(preview["signal_id"]),
                metadata={
                    "strategy_version": str(preview["strategy_version"]),
                    "preview_id": str(req.preview_id),
                    "manual_confirmed": bool(req.manual_confirmed),
                    "promotion_gate_accepted": bool(req.promotion_gate_accepted),
                },
            )
            gateway = CoreShioajiGateway(
                ShioajiConfig(
                    simulation=bool(req.simulation),
                    allow_live_order=not bool(req.simulation),
                    live_order_nonce=str(req.live_order_nonce or ""),
                )
            )
            result = TradingExecutionService(
                gateway=gateway,
                repository=repo,
                preview_service=ORDER_PREVIEW_SERVICE,
            ).execute_intent(intent)
            session.commit()
        return JSONResponse({"status": "ok", "data": result.to_dict()})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-order-approve-execute", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/tw-live/promotion-gate")
def tw_live_promotion_gate(req: PromotionGateRequest) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            result = PROMOTION_GATE.evaluate(
                strategy_name=req.strategy_name,
                strategy_version=req.strategy_version,
                paper_days=req.paper_days,
                paper_trades=req.paper_trades,
                max_drawdown=req.max_drawdown,
                slippage_report=req.slippage_report,
                data_quality_blocked=req.data_quality_blocked,
                reconciliation_matched=req.reconciliation_matched,
                single_order_value=req.single_order_value,
                daily_order_value=req.daily_order_value,
                daily_order_count=req.daily_order_count,
                repository=repo,
            )
            session.commit()
        return JSONResponse({"status": "ok", "data": result.to_dict()})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-promotion-gate", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/tw-live/audit")
def tw_live_audit(limit: int = 20) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            safe_limit = max(1, min(100, int(limit)))
            data = {
                "executions": repo.list_recent_trading_execution_records(limit=safe_limit),
                "previews": repo.list_recent_order_preview_records(limit=safe_limit),
                "advisor_decisions": repo.list_recent_advisor_decision_records(limit=safe_limit),
                "promotion_gates": repo.list_recent_promotion_gate_records(limit=safe_limit),
                "reconciliations": repo.list_recent_reconciliation_records(limit=safe_limit),
            }
        return JSONResponse({"status": "ok", "data": redact_sensitive(data)})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-audit", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/finmind/usage")
def finmind_usage() -> JSONResponse:
    try:
        from app.data.finmind_client import FinMindClient

        client = FinMindClient()
        data = client.fetch_user_info()
        return JSONResponse({"status": "ok", "data": data})
    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", "") or str(exc)
        return JSONResponse({"status": "error", "error": f"缺少依賴：{missing}，請安裝 requirements.txt"}, status_code=500)
    except Exception as exc:  # noqa: BLE001
        _append_log_line("finmind-usage", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/data/health")
def data_health(symbol: str = "2330") -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        code = _normalize_tw_symbol(symbol)
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            bars = repo.get_daily_bars(symbol=code)
            chip = repo.get_institutional_chip(symbol=code) if hasattr(repo, "get_institutional_chip") else []
            broker = repo.get_broker_agg(symbol=code) if hasattr(repo, "get_broker_agg") else []
            disposition = repo.get_disposition_periods(symbol=code) if hasattr(repo, "get_disposition_periods") else []
            features = repo.get_feature_snapshots(symbol=code) if hasattr(repo, "get_feature_snapshots") else []
        data = {
            "symbol": code,
            "daily_bars_count": len(bars),
            "institutional_chip_count": len(chip),
            "broker_agg_count": len(broker),
            "disposition_periods_count": len(disposition),
            "feature_snapshots_count": len(features),
            "latest_bar_date": bars[-1].date.isoformat() if bars else None,
            "latest_feature_date": features[-1].date.isoformat() if features else None,
        }
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("data-health", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/candidates/latest")
def latest_candidates(count: int = 10) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        limit = max(1, min(50, int(count or 10)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_latest_candidates(count=limit)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-candidates", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/latest")
def latest_radar(count: int = 20) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        limit = max(1, min(50, int(count or 20)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_latest_daily_radar(count=limit)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-radar", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/watch-pool")
def watch_pool(limit: int = 50, active_only: bool = True) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        max_rows = max(1, min(200, int(limit or 50)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_watch_pool(limit=max_rows, active_only=bool(active_only))
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("watch-pool", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/pipeline/runs")
def pipeline_runs(limit: int = 20) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        max_rows = max(1, min(100, int(limit or 20)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_pipeline_runs(limit=max_rows)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("pipeline-runs", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/strategy/default")
def strategy_default() -> JSONResponse:
    return JSONResponse({"status": "ok", "data": STRATEGY_SERVICE.default_payload()})


@app.post("/api/strategy/backtest/export")
def strategy_backtest_export(req: StrategyBacktestRequest) -> JSONResponse:
    try:
        cfg = _build_strategy_config(req)
        data = STRATEGY_SERVICE.run_multi_strategy_backtest_export(cfg, output_dir=REPORTS_DIR)
        if data.get("error") == "invalid_config":
            return JSONResponse({"status": "error", "error": data["message"], "code": "invalid_config"}, status_code=400)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("strategy-backtest-export", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/echo")
async def echo(request: Request) -> JSONResponse:
    client_host = request.client.host if request.client else ""
    debug_enabled = os.getenv("CONTROL_PANEL_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    if not debug_enabled or not _is_loopback_host(client_host):
        return JSONResponse({"status": "error", "error": "debug endpoint disabled"}, status_code=404)
    payload = await request.json()
    return JSONResponse({"status": "ok", "data": payload})


@app.get("/api/status/{action}")
def status(action: str) -> JSONResponse:
    data = _snapshot(action)
    return JSONResponse({"status": "ok", "data": data})


@app.post("/api/run/short-term")
def run_short_term(req: ShortTermRequest) -> JSONResponse:
    return _start_action("short-term", _run_short_term, req, "短期 Dashboard 正在執行中", "短期 Dashboard 開始執行")


@app.post("/api/run/swing-report")
def run_swing_report(req: SwingReportRequest) -> JSONResponse:
    return _start_action("swing-report", _run_swing, req, "波段報表正在執行中", "波段報表開始執行")


@app.post("/api/run/ai-assistant")
def run_ai_assistant(req: AIAssistantRequest) -> JSONResponse:
    return _start_action("ai-assistant", _run_ai_assistant, req, "AI 協作中心正在執行中", "AI 協作中心開始執行")


@app.post("/api/run/strategy-backtest")
def run_strategy_backtest(req: StrategyBacktestRequest) -> JSONResponse:
    return _start_action(
        "strategy-backtest",
        _run_strategy_backtest,
        req,
        "多策略回測正在執行中",
        "多策略回測開始執行",
    )


@app.post("/api/run/strategy-backtest-export")
def run_strategy_backtest_export(req: StrategyBacktestRequest) -> JSONResponse:
    return _start_action(
        "strategy-backtest-export",
        _run_strategy_backtest_export,
        req,
        "回測匯出正在執行中",
        "回測匯出開始執行",
    )


@app.post("/api/run/data-sync")
def run_data_sync(req: DataSyncRequest) -> JSONResponse:
    return _start_action(
        "data-sync",
        _run_data_sync,
        req,
        "資料同步正在執行中",
        "資料同步開始執行",
    )


@app.post("/api/run/feature-rebuild")
def run_feature_rebuild(req: FeatureRebuildRequest) -> JSONResponse:
    return _start_action(
        "feature-rebuild",
        _run_feature_rebuild,
        req,
        "特徵重建正在執行中",
        "特徵重建開始執行",
    )


@app.post("/api/run/signal-preview")
def run_signal_preview(req: SignalPreviewRequest) -> JSONResponse:
    return _start_action(
        "signal-preview",
        _run_signal_preview,
        req,
        "訊號預覽正在執行中",
        "訊號預覽開始執行",
    )


@app.post("/api/run/paper-ledger")
def run_paper_ledger(req: PaperLedgerRequest) -> JSONResponse:
    return _start_action(
        "paper-ledger",
        _run_paper_ledger,
        req,
        "模擬帳本正在執行中",
        "模擬帳本開始執行",
    )


@app.post("/api/run/invest-pipeline")
def run_invest_pipeline(req: OneClickPipelineRequest) -> JSONResponse:
    return _start_action(
        "invest-pipeline",
        _run_invest_pipeline,
        req,
        "一鍵流程正在執行中",
        "一鍵流程開始執行",
    )


@app.post("/api/run/daily-radar")
def run_daily_radar(req: DailyRadarRequest) -> JSONResponse:
    return _start_action(
        "daily-radar",
        _run_daily_radar,
        req,
        "每日雷達正在執行中",
        "每日雷達開始執行",
    )


@app.post("/api/run/account-balance")
def run_account_balance(req: AccountBalanceRequest) -> JSONResponse:
    return _start_action("account-balance", _run_account_balance, req, "餘額查詢正在執行中", "餘額查詢開始執行")


@app.post("/api/run/account-funds")
def run_account_funds(req: AccountBalanceRequest) -> JSONResponse:
    return _start_action("account-funds", _run_account_funds, req, "可用額度查詢正在執行中", "可用額度查詢開始執行")


@app.post("/api/run/settlements")
def run_settlements(req: AccountBalanceRequest) -> JSONResponse:
    return _start_action("settlements", _run_settlements, req, "交割明細查詢正在執行中", "交割明細查詢開始執行")


@app.post("/api/run/positions")
def run_positions(req: AccountBalanceRequest) -> JSONResponse:
    return _start_action("positions", _run_positions, req, "持倉查詢正在執行中", "持倉查詢開始執行")


@app.post("/api/run/account-diagnose")
def run_account_diagnose(req: AccountBalanceRequest) -> JSONResponse:
    return _start_action("account-diagnose", _run_account_diagnose, req, "帳戶診斷正在執行中", "帳戶診斷開始執行")


@app.post("/api/run/shioaji-login-test")
def run_shioaji_login_test(req: ShioajiTestRequest) -> JSONResponse:
    return _start_action("shioaji-login-test", _run_shioaji_login_test, req, "登入測試正在執行中", "登入測試開始執行")


@app.post("/api/run/shioaji-stock-test")
def run_shioaji_stock_test(req: ShioajiTestRequest) -> JSONResponse:
    return _start_action("shioaji-stock-test", _run_shioaji_stock_test, req, "證券下單測試正在執行中", "證券下單測試開始執行")


@app.post("/api/run/shioaji-futures-test")
def run_shioaji_futures_test(req: ShioajiTestRequest) -> JSONResponse:
    return _start_action("shioaji-futures-test", _run_shioaji_futures_test, req, "期貨下單測試正在執行中", "期貨下單測試開始執行")


@app.post("/api/run/shioaji-simulation-suite")
def run_shioaji_simulation_suite(req: ShioajiTestRequest) -> JSONResponse:
    return _start_action(
        "shioaji-simulation-suite",
        _run_shioaji_simulation_suite,
        req,
        "模擬整套測試正在執行中",
        "模擬整套測試開始執行",
    )


@app.post("/api/run/shioaji-verify-production")
def run_shioaji_verify_production(req: ShioajiTestRequest) -> JSONResponse:
    return _start_action(
        "shioaji-verify-production",
        _run_shioaji_verify_production,
        req,
        "正式環境檢核正在執行中",
        "正式環境檢核開始執行",
    )


app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")
