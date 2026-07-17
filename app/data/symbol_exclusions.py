from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXCLUSIONS_FILE = PROJECT_ROOT / "data" / "cache" / "excluded_symbols.txt"


def _normalize_tw_symbol(raw: str) -> str:
    code = str(raw or "").strip().upper()
    for suffix in (".TW", ".TWO"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


def _parse_exclusion_line(raw: str) -> tuple[str, str] | None:
    text = str(raw or "").strip()
    if not text or text.startswith("#"):
        return None
    comment_reason = ""
    if "#" in text:
        text, comment_reason = text.split("#", 1)
        text = text.strip()
        comment_reason = comment_reason.strip()
    if not text:
        return None
    reason = comment_reason
    if "," in text:
        symbol_part, reason_part = text.split(",", 1)
        text = symbol_part.strip()
        reason = reason_part.strip() or reason
    symbol = _normalize_tw_symbol(text)
    if not symbol:
        return None
    return symbol, reason or "manual_exclusion"


def load_symbol_exclusions(symbols_file: Path | None = None) -> dict[str, str]:
    path = symbols_file or DEFAULT_EXCLUSIONS_FILE
    if not path.exists():
        return {}
    exclusions: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_exclusion_line(raw)
        if parsed is None:
            continue
        symbol, reason = parsed
        exclusions[symbol] = reason
    return exclusions


def save_symbol_exclusions(exclusions: dict[str, str], symbols_file: Path | None = None) -> None:
    path = symbols_file or DEFAULT_EXCLUSIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# quant-trade no-touch list: symbol, reason"]
    for symbol, reason in sorted((exclusions or {}).items()):
        normalized = _normalize_tw_symbol(symbol)
        if not normalized:
            continue
        clean_reason = str(reason or "manual_exclusion").strip() or "manual_exclusion"
        lines.append(f"{normalized}, {clean_reason}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def upsert_symbol_exclusion(symbol: str, reason: str = "", symbols_file: Path | None = None) -> dict[str, str]:
    normalized = _normalize_tw_symbol(symbol)
    if not normalized:
        raise ValueError("symbol is required")
    exclusions = load_symbol_exclusions(symbols_file)
    exclusions[normalized] = str(reason or "manual_exclusion").strip() or "manual_exclusion"
    save_symbol_exclusions(exclusions, symbols_file)
    return exclusions


def remove_symbol_exclusion(symbol: str, symbols_file: Path | None = None) -> dict[str, str]:
    normalized = _normalize_tw_symbol(symbol)
    if not normalized:
        raise ValueError("symbol is required")
    exclusions = load_symbol_exclusions(symbols_file)
    exclusions.pop(normalized, None)
    save_symbol_exclusions(exclusions, symbols_file)
    return exclusions
