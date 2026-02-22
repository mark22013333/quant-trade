"""Generate Shioaji AI assistant dashboard (static HTML)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from tools.shioaji_check import run_check


TEMPLATE_PATH = Path("web/ai_assistant_dashboard.html")
CSS_PATH = Path("web/ai_assistant.css")
DOCS_DIR = Path("docs/shioaji")


def _file_status(path: Path) -> dict:
    if path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        size_kb = path.stat().st_size / 1024
        return {
            "class": "ok",
            "text": "已同步",
            "meta": f"{mtime.strftime('%Y-%m-%d %H:%M')} · {size_kb:.0f} KB",
        }
    return {
        "class": "missing",
        "text": "未同步",
        "meta": "請先執行同步工具",
    }


def _check_status() -> dict:
    result = run_check(do_login=False)
    if result.status in {"missing_env", "missing_shioaji"}:
        return {
            "class": "warn",
            "text": "待設定",
            "meta": result.message,
        }
    if result.status == "env_ok":
        return {
            "class": "ok",
            "text": "環境就緒",
            "meta": "已載入 .env（未進行登入檢查）",
        }
    return {
        "class": "ok",
        "text": "檢查完成",
        "meta": result.message,
    }


def generate_ai_dashboard(output_dir: str | Path = "reports") -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "shioaji_ai_dashboard.html"

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")

    llms_status = _file_status(DOCS_DIR / "llms.txt")
    llms_full_status = _file_status(DOCS_DIR / "llms-full.txt")
    check_status = _check_status()

    replacements = {
        "{{INLINE_CSS}}": css,
        "{{UPDATED_AT}}": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "{{LLMS_STATUS_CLASS}}": llms_status["class"],
        "{{LLMS_STATUS_TEXT}}": llms_status["text"],
        "{{LLMS_STATUS_META}}": llms_status["meta"],
        "{{LLMS_FULL_STATUS_CLASS}}": llms_full_status["class"],
        "{{LLMS_FULL_STATUS_TEXT}}": llms_full_status["text"],
        "{{LLMS_FULL_STATUS_META}}": llms_full_status["meta"],
        "{{CHECK_STATUS_CLASS}}": check_status["class"],
        "{{CHECK_STATUS_TEXT}}": check_status["text"],
        "{{CHECK_STATUS_META}}": check_status["meta"],
    }

    for key, value in replacements.items():
        template = template.replace(key, value)

    out_path.write_text(template, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    path = generate_ai_dashboard()
    print(f"AI dashboard generated: {path}")
