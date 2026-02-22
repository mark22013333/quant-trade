from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ai_assistant_dashboard import generate_ai_dashboard

REPORT_PATH = Path("reports/shioaji_ai_dashboard.html")

app = FastAPI(title="Shioaji AI Assistant")


@app.get("/")
def index() -> HTMLResponse:
    if not REPORT_PATH.exists():
        generate_ai_dashboard()
    return HTMLResponse(REPORT_PATH.read_text(encoding="utf-8"))


app.mount("/reports", StaticFiles(directory="reports"), name="reports")
