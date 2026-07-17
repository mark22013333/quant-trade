from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.broker.shioaji_account_service import ShioajiAccountService
from app.execution import OrderIntent
from app.execution.service import TradingExecutionService
from app.execution.order_preview import OrderPreviewService
from app.execution.promotion_gate import PromotionGate
from app.security import redact_sensitive
from app.services.intraday_quote_service import (
    DEFAULT_INTRADAY_QUOTE_SERVICE_STATUS_PATH,
    IntradayQuoteService,
    build_intraday_quote_service_review,
)
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
SYMBOL_EXCLUSIONS_FILE = PROJECT_ROOT / "data" / "cache" / "excluded_symbols.txt"

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
INTRADAY_QUOTE_SERVICE = IntradayQuoteService(status_path=DEFAULT_INTRADAY_QUOTE_SERVICE_STATUS_PATH)


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


def _trusted_proxy_user(request: Request) -> str:
    if os.getenv("CONTROL_PANEL_TRUST_PROXY_AUTH", "").strip() != "1":
        return ""
    client_host = request.client.host if request.client else ""
    if not _is_trusted_proxy_auth_source(client_host):
        return ""
    return str(request.headers.get("x-authenticated-user") or "").strip()


def _is_trusted_proxy_auth_source(host: str | None) -> bool:
    value = str(host or "").split(":")[0].strip().lower()
    if _is_loopback_host(value):
        return True

    raw_sources = os.getenv("CONTROL_PANEL_TRUST_PROXY_AUTH_SOURCES", "").strip()
    if not raw_sources:
        return False

    try:
        candidate = ip_address(value)
    except ValueError:
        return value in {item.strip().lower() for item in raw_sources.split(",") if item.strip()}

    for item in (part.strip() for part in raw_sources.split(",")):
        if not item:
            continue
        try:
            if "/" in item:
                if candidate in ip_network(item, strict=False):
                    return True
            elif candidate == ip_address(item):
                return True
        except ValueError:
            if value == item.lower():
                return True
    return False


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


def _advisor_name_from_strategy(strategy_name: str) -> str:
    text = str(strategy_name or "").strip()
    if text.lower().startswith("advisor:"):
        advisor_name = text.split(":", 1)[1].strip()
        return advisor_name or "stub"
    return "stub"


@app.middleware("http")
async def _security_guard(request: Request, call_next):
    token = _control_panel_token()
    client_host = request.client.host if request.client else ""
    bind_host = os.getenv("CONTROL_PANEL_BIND_HOST", "127.0.0.1")
    external_bind = not _is_loopback_host(bind_host)
    external_client = not _is_loopback_host(client_host)

    if token:
        protected = request.url.path.startswith("/api") or request.url.path.startswith("/reports")
        if protected and _request_token(request) != token and not _trusted_proxy_user(request):
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


class IntradayQuoteServiceRequest(BaseModel):
    symbols: List[str] = Field(default_factory=list)
    symbols_csv: str = Field(default="", max_length=5000)
    simulation: bool = True
    include_bidask: bool = True
    allow_outside_session: bool = False


class StockOrderPreviewRequest(BaseModel):
    simulation: bool = True
    symbol: str = Field(default="2330", min_length=2, max_length=24)
    side: str = Field(default="buy", pattern="^(buy|sell)$")
    price: float = Field(gt=0)
    quantity: int = Field(ge=1, le=1_000_000)
    order_lot: str = Field(default="IntradayOdd", pattern="^(IntradayOdd|Common)$")
    available_cash: float | None = Field(default=None, ge=0)
    position_before: int = Field(default=0, ge=0)
    sellable_quantity: int | None = Field(default=None, ge=0)
    strategy_name: str = Field(default="manual", max_length=80)
    strategy_version: str = Field(default="manual", max_length=80)
    signal_id: str = Field(default="manual", max_length=120)
    reference_price: float | None = Field(default=None, gt=0)
    limit_up_price: float | None = Field(default=None, gt=0)
    limit_down_price: float | None = Field(default=None, gt=0)
    disposition_active: bool = False
    attention_active: bool = False
    max_price_deviation_ticks: int | None = Field(default=None, ge=0, le=100)
    max_price_deviation_pct: float | None = Field(default=None, ge=0.0, le=0.20)
    current_position_value: float | None = Field(default=None, ge=0)
    portfolio_equity: float | None = Field(default=None, ge=0)
    current_portfolio_exposure: float | None = Field(default=None, ge=0)
    share_quantity: int | None = Field(default=None, ge=0)
    max_single_position_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    max_portfolio_exposure_pct: float | None = Field(default=None, ge=0.0, le=2.0)
    realized_pnl: float | None = Field(default=None)
    unrealized_pnl: float | None = Field(default=None)
    daily_pnl: float | None = Field(default=None)
    pnl_source: str | None = Field(default=None, max_length=80)
    max_daily_loss_pct: float | None = Field(default=None, ge=0.0, le=1.0)


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


class PromotionReadinessRequest(BaseModel):
    advisor_name: str = Field(default="stub", max_length=80)
    strategy_name: str = Field(default="manual", max_length=80)
    strategy_version: str = Field(default="manual", max_length=80)
    target_phase: str = Field(default="paper", pattern="^(paper|small_live|live)$")
    min_observation_days: int = Field(default=3, ge=1, le=120)
    promotion_gate_limit: int = Field(default=100, ge=1, le=500)


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
    validation_config: Dict[str, Any] = Field(default_factory=dict)


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
    include_attention: bool = False
    include_industry_indices: bool = False


class MarketDataRepairRequest(BaseModel):
    as_of: str | None = None
    freshness_days: int = Field(default=3, ge=1, le=30)
    lookback_days: int = Field(default=10, ge=1, le=365)
    datasets: str = Field(default="all", max_length=200)
    markets: str = Field(default="", max_length=50)
    symbols: str = Field(default="", max_length=5000)
    max_symbols: int = Field(default=20, ge=1, le=100)
    execute: bool = True


class Tw0050SyncRequest(BaseModel):
    pass


class Mid100SyncRequest(BaseModel):
    pass


class IndustryIndexSyncRequest(BaseModel):
    end_date: str | None = None
    lookback_days: int = Field(default=45, ge=2, le=365)


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
    universe: str = Field(default="curated_watchlist")
    data_freshness_required: bool = True
    include_attention: bool = False
    include_extended_chip: bool = False
    symbols_csv: str = Field(default="", max_length=5000)


class DailyRadarRequest(BaseModel):
    trade_date: str | None = None
    target_count: int = Field(default=15, ge=10, le=20)
    risk_profile: str = Field(default="balanced")
    threshold_offset: float = Field(default=0.0, ge=-10.0, le=10.0)
    universe: str = Field(default="curated_watchlist")
    data_freshness_required: bool = True
    include_attention: bool = False
    include_extended_data: bool = False
    prefer_official_bulk: bool = True
    symbols_csv: str = Field(default="", max_length=5000)
    threshold_adoption: Dict[str, Any] = Field(default_factory=dict)


class RadarThresholdOptimizationRunRequest(BaseModel):
    execute: bool = False
    max_runs: int = Field(default=3, ge=1, le=6)
    lookback_runs: int = Field(default=8, ge=2, le=24)
    horizons: str = Field(default="5,10,20", max_length=40)
    count: int = Field(default=50, ge=1, le=100)
    base_profile: str | None = None
    trade_date_mode: str = Field(default="T", max_length=3)
    grid_universes: str = Field(default="", max_length=200)
    grid_profiles: str = Field(default="", max_length=80)
    grid_offsets: str = Field(default="", max_length=80)
    candidate_source: str = Field(default="mixed", max_length=16)


class RadarBriefNotificationRequest(BaseModel):
    notify: str = Field(default="none", max_length=16)
    send: bool = False
    count: int = Field(default=50, ge=1, le=50)


class RadarAlertPlanRequest(BaseModel):
    notify: str = Field(default="none", max_length=16)
    send: bool = False
    count: int = Field(default=50, ge=1, le=50)


class SymbolExclusionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    reason: str = Field(default="manual_exclusion", max_length=240)


class IndexConstituentsImportRequest(BaseModel):
    index_key: str = Field(default="custom", min_length=1, max_length=80)
    source: str = Field(default="control_panel_text", max_length=80)
    content: str | None = Field(default=None, max_length=200_000)
    url: str | None = Field(default=None, max_length=1000)


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


def _quote_service_symbols(req: IntradayQuoteServiceRequest) -> list[str]:
    raw_items = list(req.symbols or [])
    if req.symbols_csv:
        raw_items.extend(str(req.symbols_csv or "").replace("\n", ",").replace(" ", ",").split(","))
    symbols: list[str] = []
    for item in raw_items:
        symbol = _normalize_tw_symbol(str(item or ""))
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


OPS_READINESS_STATUS_RANK = {"ok": 0, "warn": 1, "danger": 2}
OPS_READINESS_STATUS_TEXT = {"ok": "正常", "warn": "需留意", "danger": "需處理"}


def _ops_readiness_status_worst(*statuses: Any) -> str:
    normalized = [str(status or "warn").lower() for status in statuses]
    return max(normalized or ["warn"], key=lambda item: OPS_READINESS_STATUS_RANK.get(item, 1))


def _quote_health_component_status(health_status: Any) -> str:
    value = str(health_status or "idle").lower()
    if value == "danger":
        return "danger"
    if value == "warn":
        return "warn"
    if value in {"ok", "idle"}:
        return "ok"
    return "warn"


def _intraday_quote_service_component() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        status = INTRADAY_QUOTE_SERVICE.status()
    except Exception as exc:  # noqa: BLE001
        message = _safe_error_text(exc)
        _append_log_line("ops-readiness", f"intraday quote service status failed: {message}")
        status = {
            "state": "error",
            "running": False,
            "symbols": [],
            "symbol_count": 0,
            "last_error": message,
            "health": {
                "status": "danger",
                "label": "狀態不可用",
                "summary": "盤中行情服務狀態讀取失敗。",
                "issues": [{"code": "quote_service_status_error", "severity": "danger", "message": message}],
                "next_actions": ["檢查控制台 log，確認盤中行情服務物件是否正常。"],
            },
            "market_session": {},
            "manager": None,
        }

    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    manager = status.get("manager") if isinstance(status.get("manager"), dict) else {}
    session = status.get("market_session") if isinstance(status.get("market_session"), dict) else {}
    review = build_intraday_quote_service_review(DEFAULT_INTRADAY_QUOTE_SERVICE_STATUS_PATH, max_age_seconds=3600)
    review_evidence = review.get("evidence") if isinstance(review.get("evidence"), dict) else {}
    issues = health.get("issues") if isinstance(health.get("issues"), list) else []
    blocking_reasons = [
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and str(issue.get("severity") or "").lower() == "danger" and issue.get("code")
    ]
    warnings = [
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and str(issue.get("severity") or "").lower() == "warn" and issue.get("code")
    ]
    component_status = _quote_health_component_status(health.get("status"))
    state = str(status.get("state") or "unknown")
    state_label = {
        "running": "執行中",
        "stopped": "未啟動",
        "blocked_outside_session": "非盤中阻擋",
        "error": "狀態錯誤",
    }.get(state, state)
    connection = str(manager.get("connection_status") or ("未連線" if not manager else "未知"))
    subscription_count = int(manager.get("subscription_count") or 0)
    market_label = "盤中" if bool(session.get("is_open")) else "非盤中"
    symbol_count = int(status.get("symbol_count") or 0)
    meta = f"{state_label}，{symbol_count} 檔，{market_label}，連線 {connection}，訂閱 {subscription_count} 路。"
    if review.get("status") in {"warn", "danger"}:
        meta = f"{meta} 盤後回顧：{review.get('label') or review.get('status')}。"
    component = {
        "key": "intraday_quote_service",
        "label": "盤中行情服務",
        "status": component_status,
        "value": health.get("label") or ("正常" if component_status == "ok" else "需留意"),
        "meta": meta,
        "details": {
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "health_status": health.get("status"),
            "health_summary": health.get("summary"),
            "health_issues": issues,
            "next_actions": health.get("next_actions") if isinstance(health.get("next_actions"), list) else [],
            "state": state,
            "symbols": status.get("symbols") if isinstance(status.get("symbols"), list) else [],
            "connection_status": connection,
            "subscription_count": subscription_count,
            "market_session": session,
            "post_session_review": {
                "status": review.get("status"),
                "label": review.get("label"),
                "summary": review.get("summary"),
                "issues": review.get("issues") if isinstance(review.get("issues"), list) else [],
                "next_actions": review.get("next_actions") if isinstance(review.get("next_actions"), list) else [],
                "evidence": review_evidence,
            },
        },
    }
    return component, status


def _merge_intraday_quote_service_readiness(data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data)
    component, status = _intraday_quote_service_component()
    components = list(payload.get("components") if isinstance(payload.get("components"), list) else [])
    components.append(component)
    payload["components"] = components

    old_status = str(payload.get("status") or "warn").lower()
    new_status = _ops_readiness_status_worst(old_status, component.get("status"))
    payload["status"] = new_status
    payload["status_text"] = OPS_READINESS_STATUS_TEXT.get(new_status, "需留意")

    blocking_reasons = list(payload.get("blocking_reasons") if isinstance(payload.get("blocking_reasons"), list) else [])
    warnings = list(payload.get("warnings") if isinstance(payload.get("warnings"), list) else [])
    if component.get("status") == "danger" and "intraday_quote_service" not in blocking_reasons:
        blocking_reasons.append("intraday_quote_service")
    elif component.get("status") == "warn" and "intraday_quote_service" not in warnings:
        warnings.append("intraday_quote_service")
    payload["blocking_reasons"] = blocking_reasons
    payload["warnings"] = warnings

    details = component.get("details") if isinstance(component.get("details"), dict) else {}
    next_actions = details.get("next_actions") if isinstance(details.get("next_actions"), list) else []
    next_steps = list(payload.get("next_steps") if isinstance(payload.get("next_steps"), list) else [])
    if component.get("status") != "ok":
        next_steps.extend(str(action) for action in next_actions if action)
    payload["next_steps"] = list(dict.fromkeys(next_steps))

    metrics = dict(payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {})
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    manager = status.get("manager") if isinstance(status.get("manager"), dict) else {}
    metrics.update(
        {
            "intraday_quote_health": health.get("status") or "idle",
            "intraday_quote_running": bool(status.get("running")),
            "intraday_quote_symbol_count": int(status.get("symbol_count") or 0),
            "intraday_quote_connection_status": manager.get("connection_status") if manager else "",
            "intraday_quote_review_status": (details.get("post_session_review") or {}).get("status") if isinstance(details.get("post_session_review"), dict) else "",
            "intraday_quote_review_coverage": ((details.get("post_session_review") or {}).get("evidence") or {}).get("max_quote_coverage")
            if isinstance(details.get("post_session_review"), dict)
            and isinstance((details.get("post_session_review") or {}).get("evidence"), dict)
            else None,
        }
    )
    payload["metrics"] = metrics
    payload["intraday_quote_service"] = status

    if old_status != "danger" and component.get("status") == "danger":
        brief = dict(payload.get("decision_brief") if isinstance(payload.get("decision_brief"), dict) else {})
        brief.update(
            {
                "state": "blocked",
                "title": "盤中行情服務需要先處理",
                "summary": health.get("summary")
                or "盤中行情服務異常；先處理報價服務，再使用觸發觀察或即時價格判讀。",
                "primary_action": {"key": "intraday_quote_service", "label": "查看盤中行情"},
                "next_step": next_steps[0] if next_steps else "先處理盤中行情服務，再重新整理總檢。",
            }
        )
        payload["decision_brief"] = brief
    return payload


def _parse_csv_items(text: str) -> list[str]:
    return [item.strip() for item in str(text or "").split(",") if item.strip()]


def _parse_csv_floats(text: str, *, limit: int) -> list[float]:
    values: list[float] = []
    for item in _parse_csv_items(text):
        try:
            values.append(float(item))
        except ValueError:
            continue
        if len(values) >= max(1, int(limit or 1)):
            break
    return values


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
        validation_config=req.validation_config,
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
                include_attention=bool(req.include_attention),
                include_industry_indices=bool(req.include_industry_indices),
            )
            session.commit()

        total = int(result.get("rows_upserted_total", 0))
        partial = bool(result.get("partial_failure"))
        msg = f"資料同步完成：{total} 筆" if not partial else f"資料同步完成（部分失敗）：{total} 筆"
        _update(action, state="done", progress=100, message=msg, append_log=msg, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_market_data_repair(req: MarketDataRepairRequest) -> None:
    action = "market-data-repair"
    try:
        _update(action, state="running", progress=10, message="讀取市場資料覆蓋缺口…", append_log="init market data repair")
        try:
            from app.cli import MARKET_DATASET_FLAGS, build_market_data_repair_plan
            from app.data.sync_service import SyncService
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        as_of = datetime.strptime(req.as_of, "%Y-%m-%d").date() if req.as_of else datetime.now().date()
        dataset_items = _parse_csv_items(req.datasets)
        dataset_keys = {item.lower() for item in dataset_items}
        selected_datasets = list(MARKET_DATASET_FLAGS) if not dataset_items or "all" in dataset_keys else dataset_items
        selected_markets = _parse_csv_items(req.markets)
        manual_symbols = _parse_symbols_csv(req.symbols) or []

        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            before = repo.get_data_health_overview(as_of=as_of, freshness_days=int(req.freshness_days))
            plan = build_market_data_repair_plan(
                before,
                datasets=selected_datasets,
                markets=selected_markets,
                symbols=manual_symbols,
                as_of=as_of,
                freshness_days=int(req.freshness_days),
                lookback_days=int(req.lookback_days),
                max_symbols=int(req.max_symbols),
            )
            result = None
            after = None
            if bool(req.execute) and plan.get("status") == "ready":
                _update(
                    action,
                    progress=45,
                    message=f"同步缺口代號：{', '.join(plan.get('symbols') or [])}",
                    append_log=f"repair symbols={plan.get('symbols') or []}",
                )
                service = SyncService(repo=repo)
                result = service.sync_market_bundle(
                    start_date=datetime.strptime(str(plan["start_date"]), "%Y-%m-%d").date(),
                    end_date=datetime.strptime(str(plan["end_date"]), "%Y-%m-%d").date(),
                    symbols=list(plan.get("symbols") or []),
                    include_bars=bool(plan.get("include_bars")),
                    include_institutional=bool(plan.get("include_institutional")),
                    include_broker_agg=bool(plan.get("include_broker_agg")),
                    include_disposition=False,
                    include_attention=False,
                    include_industry_indices=False,
                    prefer_official_bulk=True,
                )
                session.commit()
                _update(action, progress=80, message="重新整理資料健康度…", append_log="refresh data overview after repair")
                after = repo.get_data_health_overview(as_of=as_of, freshness_days=int(req.freshness_days))
            else:
                after = before

        payload = {
            "dry_run": not bool(req.execute),
            "plan": plan,
            "result": result,
            "before": {
                "status": before.get("status"),
                "score": before.get("score"),
                "market_data_coverage": before.get("market_data_coverage"),
            },
            "after": {
                "status": after.get("status") if isinstance(after, dict) else None,
                "score": after.get("score") if isinstance(after, dict) else None,
                "market_data_coverage": after.get("market_data_coverage") if isinstance(after, dict) else None,
            },
        }
        if plan.get("status") != "ready":
            message = "市場資料覆蓋目前沒有可用的小批次缺口"
        elif not bool(req.execute):
            message = f"市場資料覆蓋修復乾跑完成：{len(plan.get('symbols') or [])} 檔"
        else:
            total = int((result or {}).get("rows_upserted_total") or 0)
            partial = bool((result or {}).get("partial_failure"))
            message = f"市場資料覆蓋修復完成：{total} 筆" if not partial else f"市場資料覆蓋修復完成（部分失敗）：{total} 筆"
        _update(action, state="done", progress=100, message=message, append_log=message, result=payload)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_industry_index_sync(req: IndustryIndexSyncRequest) -> None:
    action = "industry-index-sync"
    try:
        _update(action, state="running", progress=10, message="初始化外部市場/產業指數同步…", append_log="init external index context sync")
        try:
            from app.data.sync_service import SyncService
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        end_date = datetime.strptime(req.end_date, "%Y-%m-%d").date() if req.end_date else datetime.now().date()
        lookback_days = max(2, min(365, int(req.lookback_days or 45)))
        start_date = end_date - timedelta(days=lookback_days)
        _update(action, progress=35, message=f"同步 TWSE/TPEx 外部指數：{start_date} ~ {end_date}", append_log="fetch TWSE MI_INDEX and TPEx indexInfo")
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            service = SyncService(repo=repo)
            result = service.sync_external_index_context(start_date=start_date, end_date=end_date)
            session.commit()

        rows = int(result.get("rows_upserted_total", 0) or 0)
        partial = bool(result.get("partial_failure"))
        msg = f"外部指數同步完成：{rows} 筆" if not partial else f"外部指數同步完成（部分來源失敗）：{rows} 筆"
        _update(action, state="done", progress=100, message=msg, append_log=msg, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_mid100_sync(_req: Mid100SyncRequest) -> None:
    action = "mid100-sync"
    try:
        _update(action, state="running", progress=10, message="讀取元大 0051 PCF…", append_log="sync Yuanta 0051 PCF")
        try:
            from app.data.sync_service import SyncService
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        session_factory = get_session_factory()
        _update(action, progress=55, message="更新中型 100 快取與股票名稱…", append_log="write tw_mid100_symbols cache")
        with session_factory() as session:
            repo = TradingRepository(session)
            service = SyncService(repo=repo)
            result = service.sync_mid100_universe()
            session.commit()

        count = int(result.get("symbols_total", 0) or 0)
        trade_date = result.get("trade_date") or "unknown"
        message = f"中型 100 同步完成：{count} 檔（PCF {trade_date}）"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
    except Exception as exc:  # noqa: BLE001
        _update(action, state="error", progress=0, message=str(exc), append_log=str(exc))


def _run_0050_sync(_req: Tw0050SyncRequest) -> None:
    action = "0050-sync"
    try:
        _update(action, state="running", progress=10, message="讀取元大 0050 PCF…", append_log="sync Yuanta 0050 PCF")
        try:
            from app.data.sync_service import SyncService
            from app.db.repository import TradingRepository
            from app.db.session import get_session_factory, init_db
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or str(exc)
            raise RuntimeError(f"缺少依賴：{missing}，請使用同一個 Python 環境安裝 requirements.txt") from exc

        init_db()
        session_factory = get_session_factory()
        _update(action, progress=55, message="更新台灣 50 快取、股票名稱與 snapshot…", append_log="write tw0050_symbols cache")
        with session_factory() as session:
            repo = TradingRepository(session)
            service = SyncService(repo=repo)
            result = service.sync_0050_universe()
            session.commit()

        count = int(result.get("symbols_total", 0) or result.get("symbols", 0) or 0)
        trade_date = result.get("trade_date") or result.get("snapshot_date") or "unknown"
        source = str(result.get("source") or "unknown")
        suffix = "（遠端失敗，已使用本機快取）" if result.get("remote_error") else ""
        message = f"台灣 50 同步完成：{count} 檔（{source} {trade_date}）{suffix}"
        _update(action, state="done", progress=100, message=message, append_log=message, result=result)
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
                    include_attention=bool(req.include_attention),
                    include_extended_chip=bool(req.include_extended_chip),
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
                    threshold_offset=float(req.threshold_offset or 0.0),
                    universe=str(req.universe),
                    data_freshness_required=bool(req.data_freshness_required),
                    include_attention=bool(req.include_attention),
                    include_extended_data=bool(req.include_extended_data),
                    prefer_official_bulk=bool(req.prefer_official_bulk),
                    symbols=symbols,
                    threshold_adoption=dict(req.threshold_adoption or {}),
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


@app.get("/control_panel.css")
def control_panel_css() -> FileResponse:
    return FileResponse(
        CONTROL_PANEL_CSS,
        media_type="text/css",
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


@app.get("/api/quotes/intraday/status")
def intraday_quote_service_status() -> JSONResponse:
    return JSONResponse({"status": "ok", "data": INTRADAY_QUOTE_SERVICE.status()})


@app.get("/api/quotes/intraday/snapshot")
def intraday_quote_service_snapshot() -> JSONResponse:
    return JSONResponse({"status": "ok", "data": INTRADAY_QUOTE_SERVICE.snapshot()})


@app.post("/api/quotes/intraday/start")
def intraday_quote_service_start(req: IntradayQuoteServiceRequest) -> JSONResponse:
    try:
        symbols = _quote_service_symbols(req)
        if not symbols:
            return JSONResponse({"status": "error", "error": "symbols_required"}, status_code=400)
        data = INTRADAY_QUOTE_SERVICE.start(
            symbols,
            simulation=bool(req.simulation),
            include_bidask=bool(req.include_bidask),
            allow_outside_session=bool(req.allow_outside_session),
        )
        _append_log_line("intraday-quote-service", f"start {','.join(symbols)} state={data.get('state')}")
        return JSONResponse({"status": "ok", "data": data})
    except ValueError as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        _append_log_line("intraday-quote-service", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/quotes/intraday/stop")
def intraday_quote_service_stop() -> JSONResponse:
    try:
        data = INTRADAY_QUOTE_SERVICE.stop()
        _append_log_line("intraday-quote-service", "stop")
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("intraday-quote-service", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/tw-live/account-snapshot")
def tw_live_account_snapshot(simulation: bool = True) -> JSONResponse:
    try:
        result = SHIOAJI_ACCOUNT_SERVICE.get_account_snapshot(simulation=bool(simulation))
        return JSONResponse({"status": "ok", "data": result.to_dict()})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-account-snapshot", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/tw-live/reference-price")
def tw_live_reference_price(symbol: str, simulation: bool = True) -> JSONResponse:
    gateway = None
    try:
        from app.broker.shioaji_gateway import ShioajiConfig, ShioajiGateway as CoreShioajiGateway

        code = _normalize_tw_symbol(symbol)
        if not code:
            return _safe_error_response("symbol_required", status_code=400)
        gateway = CoreShioajiGateway(ShioajiConfig(simulation=bool(simulation)))
        if hasattr(gateway, "get_stock_price_reference"):
            data = gateway.get_stock_price_reference(symbol=code)
        else:
            data = {
                "symbol": code,
                "reference_price": float(gateway.resolve_order_price(symbol=code)),
                "limit_up_price": None,
                "limit_down_price": None,
            }
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    **data,
                    "simulation": bool(simulation),
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-reference-price", _safe_error_text(exc))
        return _safe_error_response("reference_price_unavailable", status_code=502)
    finally:
        api = getattr(gateway, "api", None)
        logout = getattr(api, "logout", None)
        if callable(logout):
            try:
                logout()
            except Exception:
                pass


@app.post("/api/tw-live/order-preview")
def tw_live_order_preview(req: StockOrderPreviewRequest) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        default_ticks, default_pct = TradingExecutionService.default_price_deviation_limits(req.order_lot)
        max_ticks = int(req.max_price_deviation_ticks) if req.max_price_deviation_ticks is not None else int(default_ticks)
        max_pct = float(req.max_price_deviation_pct) if req.max_price_deviation_pct is not None else float(default_pct)
        metadata = {
            "strategy_version": req.strategy_version,
            "position_quantity": int(req.position_before),
            "max_price_deviation_ticks": int(max_ticks),
            "max_price_deviation_pct": float(max_pct),
            "price_deviation_profile": str(req.order_lot),
        }
        if req.sellable_quantity is not None:
            metadata["sellable_quantity"] = int(req.sellable_quantity)
        if req.reference_price is not None:
            metadata["reference_price"] = float(req.reference_price)
        if req.limit_up_price is not None:
            metadata["limit_up_price"] = float(req.limit_up_price)
        if req.limit_down_price is not None:
            metadata["limit_down_price"] = float(req.limit_down_price)
        if req.disposition_active:
            metadata["disposition_active"] = True
        if req.attention_active:
            metadata["attention_active"] = True
        if req.current_position_value is not None:
            metadata["current_position_value"] = float(req.current_position_value)
        if req.portfolio_equity is not None:
            metadata["portfolio_equity"] = float(req.portfolio_equity)
        if req.current_portfolio_exposure is not None:
            metadata["current_portfolio_exposure"] = float(req.current_portfolio_exposure)
        if req.max_single_position_pct is not None:
            metadata["max_single_position_pct"] = float(req.max_single_position_pct)
        if req.max_portfolio_exposure_pct is not None:
            metadata["max_portfolio_exposure_pct"] = float(req.max_portfolio_exposure_pct)
        if req.realized_pnl is not None:
            metadata["realized_pnl"] = float(req.realized_pnl)
        if req.unrealized_pnl is not None:
            metadata["unrealized_pnl"] = float(req.unrealized_pnl)
        if req.daily_pnl is not None:
            metadata["daily_pnl"] = float(req.daily_pnl)
        if req.pnl_source is not None:
            metadata["pnl_source"] = str(req.pnl_source)
        if req.max_daily_loss_pct is not None:
            metadata["max_daily_loss_pct"] = float(req.max_daily_loss_pct)
        symbol_code = _normalize_tw_symbol(req.symbol)
        share_quantity = TradingExecutionService.stock_order_quantity_to_shares(req.quantity, req.order_lot)
        metadata["share_quantity"] = int(share_quantity)
        estimated_total_cost = float(req.price) * int(share_quantity) if req.side == "buy" else 0.0
        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            if not req.simulation:
                trade_date = datetime.now().date()
                active_disposition = repo.get_active_disposition(symbol=symbol_code, on_date=trade_date)
                if bool(active_disposition.get("active")):
                    metadata["disposition_active"] = True
                    metadata["is_disposition_or_attention"] = True
                    metadata["disposition_periods"] = list(active_disposition.get("periods") or [])
                active_attention = repo.get_active_attention(symbol=symbol_code, on_date=trade_date)
                if bool(active_attention.get("active")):
                    metadata["attention_active"] = True
                    metadata["is_disposition_or_attention"] = True
                    metadata["attention_records"] = list(active_attention.get("records") or [])
                elif req.disposition_active or req.attention_active:
                    metadata["is_disposition_or_attention"] = True
            intent = OrderIntent(
                source="web",
                environment="simulation" if req.simulation else "live",
                symbol=symbol_code,
                side=req.side,  # type: ignore[arg-type]
                price=float(req.price),
                quantity=int(req.quantity),
                order_lot=req.order_lot,  # type: ignore[arg-type]
                strategy_name=req.strategy_name,
                signal_id=req.signal_id,
                metadata=metadata,
            )
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
        from app.execution.promotion_readiness import PromotionReadinessService

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
            preview_metadata = dict(preview_intent.get("metadata") or {})
            preview_metadata.update(
                {
                    "strategy_version": str(preview["strategy_version"]),
                    "preview_id": str(req.preview_id),
                    "manual_confirmed": bool(req.manual_confirmed),
                    "promotion_gate_accepted": bool(req.promotion_gate_accepted),
                    "position_quantity": int(preview.get("position_before") or 0),
                }
            )
            intent = OrderIntent(
                source="web",
                environment=requested_environment,
                symbol=str(preview["symbol"]),
                side=str(preview["side"]),  # type: ignore[arg-type]
                price=float(preview["price"]),
                quantity=int(preview["quantity"]),
                order_lot=str(
                    preview_intent.get("order_lot")
                    or (preview.get("preview") or {}).get("order_lot")
                    or "IntradayOdd"
                ),  # type: ignore[arg-type]
                strategy_name=str(preview["strategy_name"]),
                signal_id=str(preview["signal_id"]),
                metadata=preview_metadata,
            )
            if requested_environment == "live":
                readiness = PromotionReadinessService().evaluate(
                    repository=repo,
                    advisor_name=_advisor_name_from_strategy(str(preview["strategy_name"])),
                    strategy_name=str(preview["strategy_name"]),
                    strategy_version=str(preview["strategy_version"]),
                    target_phase="small_live",
                )
                if not bool(readiness.accepted):
                    result = TradingExecutionService(repository=repo).reject_intent(
                        intent,
                        "promotion_readiness_blocked",
                        checks=[
                            {
                                "name": "promotion_readiness",
                                "passed": False,
                                "readiness": readiness.readiness,
                                "target_phase": readiness.target_phase,
                                "blocking_reasons": readiness.blocking_reasons,
                            }
                        ],
                    )
                    session.commit()
                    return JSONResponse(
                        {
                            "status": "ok",
                            "data": result.to_dict(),
                            "readiness": readiness.to_dict(),
                        }
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


@app.post("/api/tw-live/promotion-readiness")
def tw_live_promotion_readiness(req: PromotionReadinessRequest) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.execution.promotion_readiness import PromotionReadinessService

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            result = PromotionReadinessService(
                min_observation_days=int(req.min_observation_days),
                promotion_gate_limit=int(req.promotion_gate_limit),
            ).evaluate(
                repository=repo,
                advisor_name=req.advisor_name,
                strategy_name=req.strategy_name,
                strategy_version=req.strategy_version,
                target_phase=req.target_phase,
            )
        return JSONResponse({"status": "ok", "data": result.to_dict()})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-promotion-readiness", _safe_error_text(exc))
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


@app.get("/api/tw-live/eod-summary")
def tw_live_eod_summary(limit: int = 100) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.reports.eod_reconciliation import EndOfDayReconciliationSummaryService

        init_db()
        safe_limit = max(10, min(500, int(limit)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = EndOfDayReconciliationSummaryService(limit=safe_limit).build(repo)
        return JSONResponse({"status": "ok", "data": redact_sensitive(data)})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("tw-live-eod-summary", _safe_error_text(exc))
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


@app.get("/api/data/overview")
def data_overview() -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_data_health_overview()
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("data-overview", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/universe/preview")
def universe_preview(
    universe: str = "curated_watchlist",
    symbols: str = "",
    trade_date: str = "",
    freshness_days: int = 3,
) -> JSONResponse:
    try:
        from app.data.sync_service import SyncService
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.services.universe import preview_tw_universe

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            sync_service = SyncService(repo=repo)
            data = preview_tw_universe(
                repo,
                sync_service,
                str(universe or "curated_watchlist"),
                symbols=_parse_symbols_csv(symbols),
                trade_date=str(trade_date or "") or None,
                freshness_days=max(1, int(freshness_days or 3)),
            )
        return JSONResponse({"status": "ok", "data": redact_sensitive(data)})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("universe-preview", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/ops/readiness")
def ops_readiness(
    target_phase: str = "paper",
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 1,
    eod_limit: int = 100,
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.reports.ops_readiness import OpsReadinessService

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = OpsReadinessService(
                max_age_days=max(0, int(max_age_days)),
                warn_age_days=max(0, int(warn_age_days)),
                min_rows=max(1, int(min_rows)),
                min_entry_ready=max(0, int(min_entry_ready)),
                eod_limit=max(10, min(500, int(eod_limit))),
            ).build(repo, target_phase=str(target_phase or "paper"))
        data = _merge_intraday_quote_service_readiness(data)
        return JSONResponse({"status": "ok", "data": redact_sensitive(data)})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("ops-readiness", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/phase1-acceptance")
def radar_phase1_acceptance(
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 0,
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.reports.decision_radar_phase1_acceptance import DecisionRadarPhase1AcceptanceService

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = DecisionRadarPhase1AcceptanceService(
                max_age_days=max(0, int(max_age_days)),
                warn_age_days=max(0, int(warn_age_days)),
                min_rows=max(1, int(min_rows)),
                min_entry_ready=max(0, int(min_entry_ready)),
            ).build(repo)
        return JSONResponse({"status": "ok", "data": redact_sensitive(data)})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-phase1-acceptance", _safe_error_text(exc))
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


@app.get("/api/radar/quality-summary")
def radar_quality_summary(count: int = 50) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        limit = max(1, min(50, int(count or 50)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_quality_summary(count=limit)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-quality-summary", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/threshold-sweep")
def radar_threshold_sweep(count: int = 50, base_profile: str | None = None) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.reports.daily_radar_threshold_sweep import build_daily_radar_threshold_sweep

        init_db()
        limit = max(1, min(50, int(count or 50)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_latest_daily_radar(count=limit)
        sweep = build_daily_radar_threshold_sweep(data, base_profile=base_profile)
        return JSONResponse({"status": "ok", "data": sweep})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-threshold-sweep", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/threshold-backtest")
def radar_threshold_backtest(
    lookback_runs: int = 8,
    horizons: str = "5,10,20",
    count: int = 50,
    base_profile: str | None = None,
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        horizon_values = []
        for raw in str(horizons or "5,10,20").replace(";", ",").split(","):
            text = raw.strip()
            if not text:
                continue
            try:
                horizon_values.append(max(1, min(60, int(text))))
            except ValueError:
                continue
        if not horizon_values:
            horizon_values = [5, 10, 20]
        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_threshold_backtest(
                lookback_runs=max(2, min(24, int(lookback_runs or 8))),
                lookahead_bars_list=tuple(horizon_values),
                count=max(1, min(100, int(count or 50))),
                base_profile=base_profile,
            )
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-threshold-backtest", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/radar/threshold-optimization-runs")
def radar_threshold_optimization_runs(req: RadarThresholdOptimizationRunRequest) -> JSONResponse:
    try:
        from app.data.sync_service import SyncService
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.reports.daily_radar_threshold_optimization import run_daily_radar_threshold_optimization_batch

        horizon_values: list[int] = []
        for raw in str(req.horizons or "5,10,20").replace(";", ",").split(","):
            text = raw.strip()
            if not text:
                continue
            try:
                horizon_values.append(max(1, min(60, int(text))))
            except ValueError:
                continue
        if not horizon_values:
            horizon_values = [5, 10, 20]
        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            sync_service = SyncService(repo=repo) if bool(req.execute) else None
            data = run_daily_radar_threshold_optimization_batch(
                repo,
                sync_service=sync_service,
                execute=bool(req.execute),
                max_runs=int(req.max_runs),
                lookback_runs=int(req.lookback_runs),
                lookahead_bars_list=tuple(horizon_values),
                count=int(req.count),
                base_profile=req.base_profile,
                trade_date_mode=str(req.trade_date_mode),
                grid_universes=tuple(_parse_csv_items(req.grid_universes)[:8]),
                grid_profiles=tuple(_parse_csv_items(req.grid_profiles)[:3]),
                grid_offsets=tuple(_parse_csv_floats(req.grid_offsets, limit=7)),
                candidate_source=str(req.candidate_source),
            )
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-threshold-optimization-runs", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/threshold-adoptions")
def radar_threshold_adoptions(limit: int = 8, horizons: str = "5,10,20") -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        horizon_values: list[int] = []
        for raw in str(horizons or "5,10,20").replace(";", ",").split(","):
            text = raw.strip()
            if not text:
                continue
            try:
                horizon_values.append(max(1, min(60, int(text))))
            except ValueError:
                continue
        if not horizon_values:
            horizon_values = [5, 10, 20]
        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_threshold_adoption_summary(
                limit=max(1, min(24, int(limit or 8))),
                lookahead_bars_list=tuple(horizon_values),
            )
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-threshold-adoptions", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/decision-gate")
def radar_decision_gate(
    freshness_days: int = 3,
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 1,
    min_universe_coverage_pct: float = 50.0,
    min_core_coverage_pct: float = 70.0,
    min_quality_score: int = 60,
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.reports.daily_radar_decision_gate import DailyRadarDecisionGateService

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = DailyRadarDecisionGateService(
                freshness_days=max(1, int(freshness_days)),
                max_age_days=max(0, int(max_age_days)),
                warn_age_days=max(0, int(warn_age_days)),
                min_rows=max(1, int(min_rows)),
                min_entry_ready=max(0, int(min_entry_ready)),
                min_universe_coverage_pct=max(0.0, float(min_universe_coverage_pct)),
                min_core_coverage_pct=max(0.0, float(min_core_coverage_pct)),
                min_quality_score=max(0, int(min_quality_score)),
            ).evaluate(repo)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-decision-gate", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/trading-feedback")
def radar_trading_feedback(count: int = 50, limit: int = 100) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        item_limit = max(1, min(50, int(count or 50)))
        record_limit = max(10, min(300, int(limit or 100)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_trading_feedback_summary(count=item_limit, limit=record_limit)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-trading-feedback", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/deployment-health")
def radar_deployment_health(
    max_age_days: int = 3,
    warn_age_days: int = 1,
    min_rows: int = 5,
    min_entry_ready: int = 1,
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.reports.daily_radar_health import DailyRadarDeploymentHealthService

        init_db()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = DailyRadarDeploymentHealthService(
                max_age_days=max(0, int(max_age_days)),
                warn_age_days=max(0, int(warn_age_days)),
                min_rows=max(1, int(min_rows)),
                min_entry_ready=max(0, int(min_entry_ready)),
            ).evaluate(repo)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-deployment-health", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/trigger-monitor")
def radar_trigger_monitor(
    count: int = 50,
    limit: int = 10,
    near_entry_gap: float = 2.0,
    near_price_gap_pct: float = 0.02,
    price_source: str = "radar_snapshot",
    max_price_age_seconds: int = 180,
    streaming_seconds: float = 3.0,
    shioaji_live: bool = False,
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db
        from app.reports.daily_radar_trigger_monitor import DailyRadarTriggerMonitorService
        from app.scheduler.jobs import build_daily_radar_trigger_price_provider

        init_db()
        row_count = max(1, min(100, int(count or 50)))
        item_limit = max(1, min(20, int(limit or 10)))
        entry_gap = max(0.0, min(20.0, float(near_entry_gap or 2.0)))
        price_gap = max(0.0, min(0.25, float(near_price_gap_pct or 0.02)))
        source = str(price_source or "radar_snapshot").strip().lower()
        if source not in {"radar_snapshot", "shioaji_snapshot", "shioaji_streaming"}:
            source = "radar_snapshot"
        max_age = max(1, min(3600, int(max_price_age_seconds or 180)))
        collect_seconds = max(0.0, min(10.0, float(streaming_seconds or 0.0)))
        price_provider = build_daily_radar_trigger_price_provider(
            price_source=source,
            shioaji_simulation=not bool(shioaji_live),
            streaming_seconds=collect_seconds,
        )
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = DailyRadarTriggerMonitorService(
                count=row_count,
                limit=item_limit,
                near_entry_gap=entry_gap,
                near_price_gap_pct=price_gap,
                max_price_age_seconds=max_age,
                price_provider=price_provider,
            ).evaluate(repo)
        data["request"] = {
            "price_source": source,
            "streaming_seconds": collect_seconds,
            "max_price_age_seconds": max_age,
            "shioaji_live": bool(shioaji_live),
        }
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-trigger-monitor", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/outcomes")
def latest_radar_outcomes(lookahead_bars: int = 5, count: int = 20) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        lookahead = max(1, min(60, int(lookahead_bars or 5)))
        limit = max(1, min(100, int(count or 20)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_latest_radar_outcomes(lookahead_bars=lookahead, count=limit)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-radar-outcomes", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/changes")
def latest_radar_changes(limit: int = 8) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        item_limit = max(1, min(20, int(limit or 8)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_change_summary(limit=item_limit)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-radar-changes", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/stability")
def latest_radar_stability(lookback_runs: int = 5, limit: int = 8) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        run_count = max(2, min(20, int(lookback_runs or 5)))
        item_limit = max(1, min(20, int(limit or 8)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_stability_summary(lookback_runs=run_count, limit=item_limit)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-radar-stability", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/research-priority")
def latest_radar_research_priority(lookback_runs: int = 5, limit: int = 8) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        run_count = max(2, min(20, int(lookback_runs or 5)))
        item_limit = max(1, min(20, int(limit or 8)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_research_priority_summary(lookback_runs=run_count, limit=item_limit)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-radar-research-priority", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/allocation-plan")
def latest_radar_allocation_plan(
    portfolio_equity: float = 1_000_000.0,
    max_exposure_pct: float = 0.35,
    max_single_pct: float = 0.08,
    max_group_pct: float = 0.18,
    lookback_runs: int = 5,
    limit: int = 6,
    calibration_profile: str = "standard",
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        equity = max(10_000.0, min(100_000_000.0, float(portfolio_equity or 1_000_000.0)))
        exposure_pct = max(0.01, min(1.0, float(max_exposure_pct or 0.35)))
        single_pct = max(0.005, min(exposure_pct, float(max_single_pct or 0.08)))
        group_pct = max(single_pct, min(exposure_pct, float(max_group_pct or 0.18)))
        run_count = max(2, min(20, int(lookback_runs or 5)))
        item_limit = max(1, min(12, int(limit or 6)))
        profile = str(calibration_profile or "standard").strip().lower()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_allocation_plan(
                portfolio_equity=equity,
                max_exposure_pct=exposure_pct,
                max_single_pct=single_pct,
                max_group_pct=group_pct,
                lookback_runs=run_count,
                limit=item_limit,
                calibration_profile=profile,
            )
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-radar-allocation-plan", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/allocation-outcomes")
def latest_radar_allocation_outcomes(
    portfolio_equity: float = 1_000_000.0,
    max_exposure_pct: float = 0.35,
    max_single_pct: float = 0.08,
    max_group_pct: float = 0.18,
    lookahead_bars: int = 5,
    max_runs: int = 8,
    limit: int = 6,
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        equity = max(10_000.0, min(100_000_000.0, float(portfolio_equity or 1_000_000.0)))
        exposure_pct = max(0.01, min(1.0, float(max_exposure_pct or 0.35)))
        single_pct = max(0.005, min(exposure_pct, float(max_single_pct or 0.08)))
        group_pct = max(single_pct, min(exposure_pct, float(max_group_pct or 0.18)))
        lookahead = max(1, min(60, int(lookahead_bars or 5)))
        run_limit = max(1, min(24, int(max_runs or 8)))
        item_limit = max(1, min(12, int(limit or 6)))
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_allocation_outcome_summary(
                portfolio_equity=equity,
                max_exposure_pct=exposure_pct,
                max_single_pct=single_pct,
                max_group_pct=group_pct,
                lookahead_bars=lookahead,
                max_runs=run_limit,
                limit=item_limit,
            )
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-radar-allocation-outcomes", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/allocation-calibration")
def latest_radar_allocation_calibration(
    lookahead_bars: int = 5,
    max_snapshots: int = 300,
    min_evaluated: int = 4,
    calibration_profile: str = "standard",
) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        lookahead = max(1, min(60, int(lookahead_bars or 5)))
        snapshot_limit = max(20, min(2000, int(max_snapshots or 300)))
        min_samples = max(2, min(50, int(min_evaluated or 4)))
        profile = str(calibration_profile or "standard").strip().lower()
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_daily_radar_allocation_calibration(
                lookahead_bars=lookahead,
                max_snapshots=snapshot_limit,
                min_evaluated=min_samples,
                calibration_profile=profile,
            )
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("latest-radar-allocation-calibration", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/signals/catalog")
def signal_catalog(category: str | None = None) -> JSONResponse:
    try:
        from app.signals.registry import DEFAULT_SIGNAL_REGISTRY, list_registered_signals

        category_key = str(category or "").strip() or None
        data = {
            "categories": DEFAULT_SIGNAL_REGISTRY.categories(),
            "signals": list_registered_signals(category=category_key),
        }
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("signals-catalog", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/radar/symbol/{symbol}")
def radar_symbol_detail(symbol: str) -> JSONResponse:
    try:
        from app.db.repository import TradingRepository
        from app.db.session import get_session_factory, init_db

        init_db()
        code = _normalize_tw_symbol(symbol)
        session_factory = get_session_factory()
        with session_factory() as session:
            repo = TradingRepository(session)
            data = repo.get_symbol_decision_detail(code)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-symbol-detail", _safe_error_text(exc))
        return _safe_error_response()


def _brief_notify_channels(raw: str) -> tuple[str, ...]:
    value = str(raw or "none").strip().lower()
    if value in {"none", ""}:
        return tuple()
    if value == "both":
        return ("telegram", "line")
    if value not in {"telegram", "line"}:
        raise ValueError("notify must be one of: none, telegram, line, both")
    return (value,)


def _compact_notification_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _notification_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_compact_notification_text(item) for item in value if _compact_notification_text(item)]
    text = _compact_notification_text(value)
    if not text:
        return []
    if "|" in text:
        return [_compact_notification_text(item) for item in text.split("|") if _compact_notification_text(item)]
    return [text]


def _notification_number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return ""


def _notification_trigger_short_text(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    direct = _compact_notification_text(item.get("trigger_plan_short_text") or item.get("trigger_plan_metrics_text"))
    if direct:
        return direct
    plan = item.get("trigger_plan") if isinstance(item.get("trigger_plan"), dict) else {}
    if plan:
        parts = []
        target = _notification_number(plan.get("target_entry_score"), 0)
        breakout = _notification_number(plan.get("breakout_price"), 2)
        pullback = _notification_number(plan.get("pullback_price"), 2)
        if target:
            parts.append(f"Entry>={target}")
        if breakout:
            parts.append(f"突破{breakout}")
        if pullback:
            parts.append(f"回測{pullback}")
        if parts:
            return " / ".join(parts)
    text = _compact_notification_text(item.get("trigger_plan_text"))
    if "；" in text:
        text = text.split("；", 1)[0]
    return text


def _send_plain_notification_message(
    message: str,
    *,
    notify: str,
    send: bool,
    channels: tuple[str, ...],
    channel_status: dict[str, Any],
) -> dict[str, Any]:
    notification: dict[str, Any] = {
        "notify": str(notify or "none"),
        "triggered": bool(send),
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "channels": [],
        "skipped_reason": "preview_only",
    }
    if not send:
        return notification
    if not channels:
        notification["skipped_reason"] = "notify_disabled"
        return notification

    from app.scheduler.jobs import build_notifiers

    notifiers = build_notifiers(channels=channels)
    notification["channels"] = [type(item).__name__ for item in notifiers]
    notification["channel_status"] = {key: channel_status.get(key, {}) for key in channels}
    if not notifiers:
        notification["skipped_reason"] = "no_configured_notifiers"
        return notification

    notification["message_preview"] = message.splitlines()[0] if message else ""
    notification.pop("skipped_reason", None)
    for notifier in notifiers:
        notification["attempted"] += 1
        try:
            notifier.send(message)
            notification["sent"] += 1
        except Exception as exc:  # noqa: BLE001
            notification["failed"] += 1
            notification.setdefault("errors", []).append(str(exc))
    return notification


def _notification_channel_status() -> dict[str, Any]:
    from app.config import load_config

    cfg = load_config()
    telegram_missing = []
    if not cfg.telegram_bot_token:
        telegram_missing.append("TELEGRAM_BOT_TOKEN")
    if not cfg.telegram_chat_id:
        telegram_missing.append("TELEGRAM_CHAT_ID")
    line_missing = []
    if not cfg.line_notify_token:
        line_missing.append("LINE_NOTIFY_TOKEN")
    return {
        "telegram": {
            "configured": len(telegram_missing) == 0,
            "missing": telegram_missing,
            "label": "Telegram",
        },
        "line": {
            "configured": len(line_missing) == 0,
            "missing": line_missing,
            "label": "LINE Notify",
        },
    }


def _latest_radar_export_payload(count: int = 50) -> dict[str, Any]:
    from app.db.repository import TradingRepository
    from app.db.session import get_session_factory, init_db
    from app.reports.daily_radar_export import export_daily_radar_files

    init_db()
    limit = max(1, min(50, int(count or 50)))
    session_factory = get_session_factory()
    allocation_plan: dict[str, Any] = {}
    allocation_outcomes: dict[str, Any] = {}
    allocation_calibration: dict[str, Any] = {}
    trading_feedback: dict[str, Any] = {}
    data_health_overview: dict[str, Any] = {}

    def optional_repo_summary(repo: Any, method_name: str, log_key: str, **kwargs: Any) -> dict[str, Any]:
        method = getattr(repo, method_name, None)
        if not callable(method):
            return {}
        try:
            result = method(**kwargs)
            return result if isinstance(result, dict) else {}
        except Exception as exc:  # noqa: BLE001
            _append_log_line(log_key, _safe_error_text(exc))
            return {"status": "error", "reason": "summary_unavailable"}

    with session_factory() as session:
        repo = TradingRepository(session)
        data = repo.get_latest_daily_radar(count=limit)
        if data:
            allocation_plan = optional_repo_summary(
                repo,
                "get_daily_radar_allocation_plan",
                "radar-summary-export-allocation-plan",
                calibration_profile="standard",
            )
            allocation_outcomes = optional_repo_summary(
                repo,
                "get_daily_radar_allocation_outcome_summary",
                "radar-summary-export-allocation-outcomes",
            )
            allocation_calibration = optional_repo_summary(
                repo,
                "get_daily_radar_allocation_calibration",
                "radar-summary-export-allocation-calibration",
                calibration_profile="standard",
            )
            trading_feedback = optional_repo_summary(
                repo,
                "get_daily_radar_trading_feedback_summary",
                "radar-summary-export-trading-feedback",
                count=limit,
                limit=100,
            )
            data_health_overview = optional_repo_summary(
                repo,
                "get_data_health_overview",
                "radar-summary-export-data-health",
                freshness_days=3,
            )
    if not data:
        raise LookupError("no_daily_radar")
    report_date = data[0].get("run_date") or datetime.now().date().isoformat()
    run_id = data[0].get("run_id") or "latest"
    export = export_daily_radar_files(
        data,
        output_dir=REPORTS_DIR,
        report_date=report_date,
        run_id=f"latest_{run_id}",
        allocation_plan=allocation_plan,
        allocation_outcomes=allocation_outcomes,
        allocation_calibration=allocation_calibration,
        trading_feedback=trading_feedback,
        data_health_overview=data_health_overview,
    )
    export_data = export.get("data") if isinstance(export.get("data"), dict) else {}
    decision_brief = export_data.get("decision_brief") if isinstance(export_data.get("decision_brief"), dict) else {}
    return {
        "items": data,
        "allocation_plan": allocation_plan,
        "allocation_outcomes": allocation_outcomes,
        "allocation_calibration": allocation_calibration,
        "trading_feedback": trading_feedback,
        "data_health_overview": data_health_overview,
        "decision_brief": decision_brief,
        "export": export,
    }


def _daily_radar_alert_plan_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    first_item = items[0] if items and isinstance(items[0], dict) else {}
    allocation = payload.get("allocation_plan") if isinstance(payload.get("allocation_plan"), dict) else {}
    diagnostics = allocation.get("allocation_diagnostics") if isinstance(allocation.get("allocation_diagnostics"), dict) else {}
    review_queue = diagnostics.get("review_queue") if isinstance(diagnostics.get("review_queue"), dict) else {}
    raw_lanes = review_queue.get("lanes") if isinstance(review_queue.get("lanes"), dict) else {}
    decision_brief = payload.get("decision_brief") if isinstance(payload.get("decision_brief"), dict) else {}
    handoff = review_queue.get("handoff") if isinstance(review_queue.get("handoff"), dict) else {}
    if not handoff and isinstance(decision_brief.get("handoff"), dict):
        handoff = decision_brief.get("handoff") or {}

    lane_defaults = {
        "preview": {"label": "可先建立 Preview", "severity": "ok"},
        "trigger": {"label": "設觸發觀察", "severity": "watch"},
        "risk": {"label": "先處理風險", "severity": "warn"},
    }
    lanes: list[dict[str, Any]] = []
    summary = {"preview_count": 0, "trigger_count": 0, "risk_count": 0, "total_count": 0}
    for lane_key, default in lane_defaults.items():
        lane = raw_lanes.get(lane_key) if isinstance(raw_lanes.get(lane_key), dict) else {}
        lane_items = lane.get("items") if isinstance(lane.get("items"), list) else []
        alert_plan = lane.get("alert_plan") if isinstance(lane.get("alert_plan"), dict) else {}
        count = int(lane.get("count") or len(lane_items) or 0)
        checklist = _notification_text_list(alert_plan.get("checklist")) or _notification_text_list(
            alert_plan.get("checklist_text")
        )
        symbols = _notification_text_list(alert_plan.get("symbols"))
        top_items = []
        if not symbols:
            symbols = [
                _compact_notification_text(item.get("symbol"))
                for item in lane_items
                if isinstance(item, dict) and _compact_notification_text(item.get("symbol"))
            ]
        for item in [row for row in lane_items if isinstance(row, dict)][:4]:
            top_items.append(
                {
                    "symbol": _compact_notification_text(item.get("symbol")),
                    "name": _compact_notification_text(item.get("name")),
                    "industry_group": _compact_notification_text(item.get("industry_group")),
                    "action_label": _compact_notification_text(item.get("action_label")),
                    "alert_rule": _compact_notification_text(item.get("alert_rule")),
                    "checklist": _notification_text_list(item.get("checklist"))
                    or _notification_text_list(item.get("checklist_text")),
                    "reason": _compact_notification_text(item.get("reason")),
                    "next_step": _compact_notification_text(item.get("next_step")),
                    "trigger_plan_short_text": _notification_trigger_short_text(item),
                    "suggested_value": float(item.get("suggested_value") or 0.0),
                    "allocated": bool(item.get("allocated")),
                }
            )
        lane_payload = {
            "lane": lane_key,
            "label": _compact_notification_text(lane.get("label")) or default["label"],
            "severity": default["severity"],
            "count": count,
            "has_items": count > 0,
            "rule": _compact_notification_text(alert_plan.get("rule") or lane.get("hint")),
            "checklist": checklist[:5],
            "symbols": symbols[:12],
            "items": top_items,
        }
        lanes.append(lane_payload)
        summary[f"{lane_key}_count"] = count
        summary["total_count"] += count

    alert_plan = {
        "status": "active" if summary["total_count"] > 0 else "empty",
        "run_id": _compact_notification_text(first_item.get("run_id")) or _compact_notification_text(decision_brief.get("run_id")),
        "trade_date": _compact_notification_text(first_item.get("run_date"))
        or _compact_notification_text(decision_brief.get("trade_date")),
        "candidate_count": len(items),
        "summary": summary,
        "handoff": {
            "primary_lane": _compact_notification_text(handoff.get("primary_lane")),
            "primary_label": _compact_notification_text(handoff.get("primary_label")),
            "primary_action": _compact_notification_text(handoff.get("primary_action")),
            "next_session_note": _compact_notification_text(handoff.get("next_session_note")),
            "top_symbols": _notification_text_list(handoff.get("top_symbols")),
        },
        "lanes": lanes,
    }
    alert_plan["message"] = _format_daily_radar_alert_plan_message(alert_plan)
    return alert_plan


def _format_daily_radar_alert_plan_message(alert_plan: dict[str, Any]) -> str:
    summary = alert_plan.get("summary") if isinstance(alert_plan.get("summary"), dict) else {}
    handoff = alert_plan.get("handoff") if isinstance(alert_plan.get("handoff"), dict) else {}
    lanes = alert_plan.get("lanes") if isinstance(alert_plan.get("lanes"), list) else []
    lines = [
        f"[每日雷達] {_compact_notification_text(alert_plan.get('trade_date')) or '今日'} 提醒檢查",
        "Run: {run_id} · 候選 {candidate_count} 檔".format(
            run_id=_compact_notification_text(alert_plan.get("run_id")) or "-",
            candidate_count=int(alert_plan.get("candidate_count") or 0),
        ),
        "行動線: Preview {preview} / Trigger {trigger} / Risk {risk}".format(
            preview=int(summary.get("preview_count") or 0),
            trigger=int(summary.get("trigger_count") or 0),
            risk=int(summary.get("risk_count") or 0),
        ),
    ]
    if _compact_notification_text(handoff.get("primary_action")):
        lines.append(f"主線: {_compact_notification_text(handoff.get('primary_action'))}")
    if _compact_notification_text(handoff.get("next_session_note")):
        lines.append(f"交接: {_compact_notification_text(handoff.get('next_session_note'))}")
    if int(summary.get("total_count") or 0) <= 0:
        lines.append("目前沒有需要提醒的配置行動線。")
        return "\n".join(lines)

    for lane in [row for row in lanes if isinstance(row, dict) and int(row.get("count") or 0) > 0]:
        symbols = _notification_text_list(lane.get("symbols"))
        symbol_text = f"：{', '.join(symbols[:6])}" if symbols else ""
        lines.append(f"- {_compact_notification_text(lane.get('label'))}: {int(lane.get('count') or 0)} 檔{symbol_text}")
        rule = _compact_notification_text(lane.get("rule"))
        if rule:
            lines.append(f"  規則: {rule}")
        checklist = _notification_text_list(lane.get("checklist"))
        if checklist:
            lines.append(f"  檢查: {' / '.join(checklist[:3])}")
        items = lane.get("items") if isinstance(lane.get("items"), list) else []
        for item in [row for row in items if isinstance(row, dict)][:2]:
            symbol = _compact_notification_text(item.get("symbol"))
            name = _compact_notification_text(item.get("name"))
            item_rule = _compact_notification_text(item.get("alert_rule") or item.get("next_step") or item.get("reason"))
            if symbol and item_rule:
                title = f"{symbol} {name}".strip()
                trigger_short = _compact_notification_text(item.get("trigger_plan_short_text"))
                suffix = f"；觸發: {trigger_short}" if trigger_short else ""
                lines.append(f"  - {title}: {item_rule}{suffix}")
    return "\n".join(lines)


@app.post("/api/radar/latest/export-summary")
def export_latest_radar_summary(count: int = 50) -> JSONResponse:
    try:
        return JSONResponse({"status": "ok", "data": _latest_radar_export_payload(count=count)})
    except LookupError:
        return JSONResponse({"status": "error", "error": "尚無每日雷達資料"}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-summary-export", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/notifications/status")
def notification_status() -> JSONResponse:
    try:
        channels = _notification_channel_status()
        configured = [key for key, value in channels.items() if bool(value.get("configured"))]
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "channels": channels,
                    "configured": configured,
                    "supported": ["telegram", "line", "both"],
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        _append_log_line("notification-status", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/radar/latest/brief-notification")
def radar_latest_brief_notification(req: RadarBriefNotificationRequest | None = None) -> JSONResponse:
    try:
        from app.scheduler.jobs import build_notifiers, format_daily_radar_brief_message, send_daily_radar_brief_notification

        request = req or RadarBriefNotificationRequest()
        try:
            channels = _brief_notify_channels(request.notify)
        except ValueError as exc:
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)
        channel_status = _notification_channel_status()
        payload = _latest_radar_export_payload(count=request.count)
        export = payload.get("export") if isinstance(payload.get("export"), dict) else {}
        urls = export.get("urls") if isinstance(export.get("urls"), dict) else {}
        decision_brief = payload.get("decision_brief") if isinstance(payload.get("decision_brief"), dict) else {}
        message = format_daily_radar_brief_message(decision_brief, export_urls=urls)
        notification: dict[str, Any] = {
            "notify": str(request.notify or "none"),
            "triggered": bool(request.send),
            "attempted": 0,
            "sent": 0,
            "failed": 0,
            "channels": [],
            "skipped_reason": "preview_only",
        }
        if request.send:
            if channels:
                notifiers = build_notifiers(channels=channels)
                notification = send_daily_radar_brief_notification(export, notifiers=notifiers)
                notification["notify"] = str(request.notify or "none")
                notification["triggered"] = True
                notification["channel_status"] = {key: channel_status.get(key, {}) for key in channels}
            else:
                notification["skipped_reason"] = "notify_disabled"
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "message": message,
                    "decision_brief": decision_brief,
                    "export": export,
                    "channel_status": channel_status,
                    "notification": notification,
                },
            }
        )
    except LookupError:
        return JSONResponse({"status": "error", "error": "尚無每日雷達資料"}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-brief-notification", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/radar/latest/alert-plan")
def radar_latest_alert_plan(req: RadarAlertPlanRequest | None = None) -> JSONResponse:
    try:
        request = req or RadarAlertPlanRequest()
        try:
            channels = _brief_notify_channels(request.notify)
        except ValueError as exc:
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)
        channel_status = _notification_channel_status()
        payload = _latest_radar_export_payload(count=request.count)
        alert_plan = _daily_radar_alert_plan_from_payload(payload)
        message = str(alert_plan.get("message") or "")
        notification = _send_plain_notification_message(
            message,
            notify=request.notify,
            send=bool(request.send),
            channels=channels,
            channel_status=channel_status,
        )
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "message": message,
                    "alert_plan": alert_plan,
                    "decision_brief": payload.get("decision_brief") if isinstance(payload.get("decision_brief"), dict) else {},
                    "channel_status": channel_status,
                    "notification": notification,
                },
            }
        )
    except LookupError:
        return JSONResponse({"status": "error", "error": "尚無每日雷達資料"}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        _append_log_line("radar-alert-plan", _safe_error_text(exc))
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


def _exclusion_payload(exclusions: dict[str, str]) -> dict:
    try:
        file_label = str(SYMBOL_EXCLUSIONS_FILE.relative_to(PROJECT_ROOT))
    except ValueError:
        file_label = str(SYMBOL_EXCLUSIONS_FILE)
    rows = [{"symbol": symbol, "reason": reason} for symbol, reason in sorted(exclusions.items())]
    return {
        "items": rows,
        "count": len(rows),
        "file": file_label,
    }


def _display_file_label(path_value: str) -> str:
    try:
        path = Path(path_value)
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path_value)


def _index_constituents_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        if row.get("file"):
            row["file"] = _display_file_label(str(row["file"]))
        rows.append(row)
    return {
        "items": rows,
        "count": len(rows),
    }


@app.get("/api/symbol-exclusions")
def symbol_exclusions() -> JSONResponse:
    try:
        from app.data.symbol_exclusions import load_symbol_exclusions

        exclusions = load_symbol_exclusions(SYMBOL_EXCLUSIONS_FILE)
        return JSONResponse({"status": "ok", "data": _exclusion_payload(exclusions)})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("symbol-exclusions", _safe_error_text(exc))
        return _safe_error_response()


@app.get("/api/index-constituents")
def index_constituents() -> JSONResponse:
    try:
        from app.data.index_constituents import list_index_constituent_caches

        items = list_index_constituent_caches()
        return JSONResponse({"status": "ok", "data": _index_constituents_payload(items)})
    except Exception as exc:  # noqa: BLE001
        _append_log_line("index-constituents", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/index-constituents")
def import_index_constituents(req: IndexConstituentsImportRequest) -> JSONResponse:
    try:
        from app.data.index_constituents import (
            list_index_constituent_caches,
            normalize_index_key,
            parse_index_constituents_text,
            sync_index_constituents_url,
            write_index_constituents_cache,
        )

        key = normalize_index_key(req.index_key)
        if req.url:
            imported = sync_index_constituents_url(req.url, index_key=key, source=req.source)
        else:
            content = str(req.content or "")
            if not content.strip():
                return JSONResponse({"status": "error", "error": "content_or_url_required"}, status_code=400)
            rows = parse_index_constituents_text(content, index_key=key, source=req.source)
            if not rows:
                return JSONResponse({"status": "error", "error": "parseable_symbols_required"}, status_code=400)
            imported = write_index_constituents_cache(rows, index_key=key, source=req.source)
        imported["file"] = _display_file_label(str(imported.get("file", "")))
        items = list_index_constituent_caches()
        _append_log_line("index-constituents", f"import {key} {imported.get('count', 0)}")
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "imported": imported,
                    **_index_constituents_payload(items),
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, ValueError):
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)
        _append_log_line("index-constituents", _safe_error_text(exc))
        return _safe_error_response()


@app.post("/api/symbol-exclusions")
def upsert_symbol_exclusion(req: SymbolExclusionRequest) -> JSONResponse:
    try:
        from app.data.symbol_exclusions import upsert_symbol_exclusion as _upsert_symbol_exclusion

        symbol = _normalize_tw_symbol(req.symbol)
        if not symbol:
            return JSONResponse({"status": "error", "error": "symbol_required"}, status_code=400)
        exclusions = _upsert_symbol_exclusion(symbol, req.reason, SYMBOL_EXCLUSIONS_FILE)
        _append_log_line("symbol-exclusions", f"upsert {symbol}")
        return JSONResponse({"status": "ok", "data": _exclusion_payload(exclusions)})
    except ValueError as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        _append_log_line("symbol-exclusions", _safe_error_text(exc))
        return _safe_error_response()


@app.delete("/api/symbol-exclusions/{symbol}")
def delete_symbol_exclusion(symbol: str) -> JSONResponse:
    try:
        from app.data.symbol_exclusions import remove_symbol_exclusion as _remove_symbol_exclusion

        normalized = _normalize_tw_symbol(symbol)
        if not normalized:
            return JSONResponse({"status": "error", "error": "symbol_required"}, status_code=400)
        exclusions = _remove_symbol_exclusion(normalized, SYMBOL_EXCLUSIONS_FILE)
        _append_log_line("symbol-exclusions", f"delete {normalized}")
        return JSONResponse({"status": "ok", "data": _exclusion_payload(exclusions)})
    except ValueError as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        _append_log_line("symbol-exclusions", _safe_error_text(exc))
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


@app.post("/api/run/market-data-repair")
def run_market_data_repair(req: MarketDataRepairRequest) -> JSONResponse:
    return _start_action(
        "market-data-repair",
        _run_market_data_repair,
        req,
        "市場資料覆蓋修復正在執行中",
        "市場資料覆蓋修復開始執行",
    )


@app.post("/api/run/mid100-sync")
def run_mid100_sync(req: Mid100SyncRequest) -> JSONResponse:
    return _start_action(
        "mid100-sync",
        _run_mid100_sync,
        req,
        "中型 100 同步正在執行中",
        "中型 100 同步開始執行",
    )


@app.post("/api/run/0050-sync")
def run_0050_sync(req: Tw0050SyncRequest) -> JSONResponse:
    return _start_action(
        "0050-sync",
        _run_0050_sync,
        req,
        "台灣 50 同步正在執行中",
        "台灣 50 同步開始執行",
    )


@app.post("/api/run/industry-index-sync")
def run_industry_index_sync(req: IndustryIndexSyncRequest) -> JSONResponse:
    return _start_action(
        "industry-index-sync",
        _run_industry_index_sync,
        req,
        "外部指數同步正在執行中",
        "外部指數同步開始執行",
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
