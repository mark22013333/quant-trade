from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_assistant_dashboard import generate_ai_dashboard
from analysis.short_term_ranker import run_short_term_ranking
from dashboard_generator import generate_dashboard
from swing_analysis import generate_swing_report
from tools.shioaji_ai_sync import sync_ai_docs
from web.services import ShioajiGateway, ShioajiWorkflowService
from web.services.shioaji_workflow import OrderTestConfig

CONTROL_PANEL_HTML = Path("web/control_panel.html")
CONTROL_PANEL_CSS = Path("web/control_panel.css")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports"
LOG_PATH = REPORTS_DIR / "control_panel.log"
ENV_PATH = PROJECT_ROOT / ".env"


def _append_log_line(action: str, message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {action} | {message}\n")
    except Exception:
        return


app = FastAPI(title="Quant-Trade Control Panel")


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
    stock_code: str = Field(default="2890", min_length=3, max_length=12)
    stock_quantity: int = Field(default=1, ge=1, le=10)
    stock_price: float | None = Field(default=None, gt=0)
    futures_code: str = Field(default="TXFC0", min_length=2, max_length=12)
    futures_quantity: int = Field(default=1, ge=1, le=10)
    futures_price: float | None = Field(default=None, gt=0)
    interval_sec: float = Field(default=1.1, ge=1.0, le=5.0)


_status_lock = threading.RLock()
_status: Dict[str, Dict[str, object]] = {}
SHIOAJI_LOCK = threading.Lock()
SHIOAJI_LOCK_TIMEOUT_SEC = 16

SHIOAJI_GATEWAY = ShioajiGateway(ENV_PATH)
SHIOAJI_SERVICE = ShioajiWorkflowService(SHIOAJI_GATEWAY)


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
            entry["message"] = message
        if append_log:
            entry.setdefault("log", []).insert(0, append_log)
            entry["log"] = entry["log"][:80]
            _append_log_line(action, append_log)
        if result is not None:
            entry["result"] = result
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


def _run_short_term(req: ShortTermRequest) -> None:
    action = "short-term"
    try:
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
    thread = threading.Thread(target=target, args=(req,), daemon=True)
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
    return JSONResponse({"status": "ok", "data": SHIOAJI_GATEWAY.env_status()})


@app.post("/api/echo")
async def echo(request: Request) -> JSONResponse:
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


app.mount("/reports", StaticFiles(directory="reports"), name="reports")


@app.on_event("startup")
def _startup_log() -> None:
    _append_log_line("server", "control panel started")
