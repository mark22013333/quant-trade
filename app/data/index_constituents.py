from __future__ import annotations

import csv
import ipaddress
import re
import zipfile
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX_CONSTITUENTS_DIR = PROJECT_ROOT / "data" / "cache" / "index_constituents"
SYMBOL_RE = re.compile(r"(?<!\d)(\d{4,6})(?:\.(?:TW|TWO))?(?!\d)", re.IGNORECASE)
CODE_HEADERS = {"symbol", "code", "ticker", "stock_id", "stock_code", "證券代號", "股票代號", "代號", "成分股代號"}
NAME_HEADERS = {"name", "stock_name", "security_name", "證券名稱", "股票名稱", "名稱", "成分股名稱"}
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def normalize_index_key(value: str) -> str:
    text = str(value or "").strip()
    text = text.replace("\\", "_").replace("/", "_").replace(":", "_")
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "", text)
    return text[:80] or "custom"


def normalize_tw_symbol(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    match = SYMBOL_RE.search(text)
    return match.group(1) if match else ""


def _sniff_dialect(text: str) -> csv.Dialect:
    sample = "\n".join(line for line in text.splitlines()[:20] if line.strip())
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        return csv.excel


def _header_index(headers: list[str], candidates: set[str]) -> int | None:
    normalized = [str(item or "").strip().lower() for item in headers]
    for idx, item in enumerate(normalized):
        if item in candidates:
            return idx
    for idx, item in enumerate(normalized):
        if any(candidate in item for candidate in candidates):
            return idx
    return None


def parse_index_constituents_text(text: str, *, index_key: str = "custom", source: str = "manual") -> list[dict[str, Any]]:
    raw_text = str(text or "")
    if not raw_text.strip():
        return []
    dialect = _sniff_dialect(raw_text)
    rows = list(csv.reader(raw_text.splitlines(), dialect=dialect))
    cleaned = [row for row in rows if any(str(cell or "").strip() for cell in row)]
    if not cleaned:
        return []

    first_row = [str(cell or "").strip() for cell in cleaned[0]]
    has_header = any(str(cell).strip().lower() in CODE_HEADERS | NAME_HEADERS for cell in first_row)
    headers = first_row if has_header else []
    data_rows = cleaned[1:] if has_header else cleaned
    code_idx = _header_index(headers, CODE_HEADERS) if headers else None
    name_idx = _header_index(headers, NAME_HEADERS) if headers else None

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in data_rows:
        cells = [str(cell or "").strip() for cell in row]
        symbol = normalize_tw_symbol(cells[code_idx]) if code_idx is not None and len(cells) > code_idx else ""
        if not symbol:
            for cell in cells:
                symbol = normalize_tw_symbol(cell)
                if symbol:
                    break
        if not symbol or symbol in seen:
            continue
        name = cells[name_idx] if name_idx is not None and len(cells) > name_idx else ""
        if not name:
            symbol_cell_index = next((idx for idx, cell in enumerate(cells) if normalize_tw_symbol(cell) == symbol), None)
            if symbol_cell_index is not None and len(cells) > symbol_cell_index + 1:
                candidate = cells[symbol_cell_index + 1]
                if not normalize_tw_symbol(candidate):
                    name = candidate
        output.append(
            {
                "index_key": normalize_index_key(index_key),
                "symbol": symbol,
                "name": name,
                "source": str(source or "manual"),
            }
        )
        seen.add(symbol)
    return output


def parse_index_constituents_bytes(
    content: bytes,
    *,
    filename: str = "",
    index_key: str = "custom",
    source: str = "manual",
) -> list[dict[str, Any]]:
    raw = bytes(content or b"")
    if not raw:
        return []
    if _looks_like_xlsx(raw, filename):
        text = _xlsx_to_delimited_text(raw)
    else:
        text = _decode_text_bytes(raw)
    return parse_index_constituents_text(text, index_key=index_key, source=source)


def validate_public_https_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https":
        raise ValueError("official_url_https_required")
    if not parsed.hostname:
        raise ValueError("official_url_host_required")
    if parsed.username or parsed.password:
        raise ValueError("official_url_credentials_not_allowed")

    host = parsed.hostname.strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        raise ValueError("official_url_public_host_required")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return value
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise ValueError("official_url_public_host_required")
    return value


def _decode_text_bytes(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _looks_like_xlsx(content: bytes, filename: str = "") -> bool:
    name = str(filename or "").lower()
    if name.endswith(".xlsx"):
        return True
    if not content.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            return "xl/workbook.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _xlsx_to_delimited_text(content: bytes) -> str:
    output = StringIO()
    writer = csv.writer(output)
    with zipfile.ZipFile(BytesIO(content)) as archive:
        shared_strings = _read_shared_strings(archive)
        worksheet_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        )
        for name in worksheet_names:
            for row in _read_xlsx_sheet(archive, name, shared_strings):
                if any(str(cell or "").strip() for cell in row):
                    writer.writerow([str(cell or "") for cell in row])
    return output.getvalue()


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root:
        texts = [node.text or "" for node in item.iter() if _local_name(node.tag) == "t"]
        values.append("".join(texts))
    return values


def _read_xlsx_sheet(archive: zipfile.ZipFile, sheet_name: str, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(sheet_name))
    rows: list[list[str]] = []
    for row_node in root.iter():
        if _local_name(row_node.tag) != "row":
            continue
        row: list[str] = []
        for cell_node in row_node:
            if _local_name(cell_node.tag) != "c":
                continue
            ref = str(cell_node.attrib.get("r") or "")
            col_idx = _xlsx_column_index(ref)
            while len(row) <= col_idx:
                row.append("")
            row[col_idx] = _xlsx_cell_value(cell_node, shared_strings)
        rows.append(row)
    return rows


def _xlsx_cell_value(cell_node: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell_node.attrib.get("t")
    if cell_type == "inlineStr":
        texts = [node.text or "" for node in cell_node.iter() if _local_name(node.tag) == "t"]
        return "".join(texts).strip()
    value_node = next((node for node in cell_node if _local_name(node.tag) == "v"), None)
    if value_node is None or value_node.text is None:
        return ""
    value = str(value_node.text).strip()
    if cell_type == "s":
        try:
            return shared_strings[int(value)].strip()
        except (ValueError, IndexError):
            return ""
    return value


def _xlsx_column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in str(cell_ref or "") if ch.isalpha()).upper()
    if not letters:
        return 0
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return max(0, index - 1)


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _cache_path(index_key: str, *, cache_dir: Path | None = None) -> Path:
    root = cache_dir or DEFAULT_INDEX_CONSTITUENTS_DIR
    return root / f"{normalize_index_key(index_key)}.csv"


def write_index_constituents_cache(
    rows: list[dict[str, Any]],
    *,
    index_key: str,
    cache_dir: Path | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    key = normalize_index_key(index_key)
    path = _cache_path(key, cache_dir=cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = normalize_tw_symbol(row.get("symbol"))
        if symbol:
            unique[symbol] = {**row, "symbol": symbol}
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["index_key", "symbol", "name", "source", "synced_at"])
        synced_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        for symbol in sorted(unique):
            row = unique[symbol]
            writer.writerow([key, symbol, row.get("name", ""), row.get("source") or source, synced_at])
    return {
        "index_key": key,
        "source": source,
        "symbols": sorted(unique),
        "count": len(unique),
        "file": str(path),
    }


def import_index_constituents_file(
    input_path: str | Path,
    *,
    index_key: str,
    cache_dir: Path | None = None,
    source: str = "manual_file",
) -> dict[str, Any]:
    path = Path(input_path).expanduser()
    rows = parse_index_constituents_bytes(path.read_bytes(), filename=path.name, index_key=index_key, source=source)
    if not rows:
        raise RuntimeError(f"index constituents file has no parseable Taiwan stock symbols: {path}")
    return write_index_constituents_cache(rows, index_key=index_key, cache_dir=cache_dir, source=source)


def sync_index_constituents_url(
    url: str,
    *,
    index_key: str,
    cache_dir: Path | None = None,
    source: str = "official_url",
    timeout_sec: int = 15,
) -> dict[str, Any]:
    safe_url = validate_public_https_url(url)
    response = requests.get(safe_url, timeout=int(timeout_sec))
    response.raise_for_status()
    filename = Path(urlparse(response.url or safe_url).path).name
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip()
    rows = parse_index_constituents_bytes(
        response.content,
        filename=filename,
        index_key=index_key,
        source=source,
    )
    if not rows:
        raise RuntimeError(f"index constituents URL has no parseable Taiwan stock symbols: {safe_url}")
    result = write_index_constituents_cache(rows, index_key=index_key, cache_dir=cache_dir, source=source)
    if not content_type and filename.lower().endswith(".xlsx"):
        content_type = XLSX_CONTENT_TYPE
    result.update(
        {
            "url": safe_url,
            "download_url": response.url,
            "content_type": content_type,
        }
    )
    return result


def load_index_constituent_symbols(index_key: str, *, cache_dir: Path | None = None) -> list[str]:
    path = _cache_path(index_key, cache_dir=cache_dir)
    if not path.exists():
        return []
    rows = parse_index_constituents_text(path.read_text(encoding="utf-8-sig"), index_key=index_key, source="cache")
    return sorted({row["symbol"] for row in rows if row.get("symbol")})


def list_index_constituent_caches(*, cache_dir: Path | None = None) -> list[dict[str, Any]]:
    root = cache_dir or DEFAULT_INDEX_CONSTITUENTS_DIR
    if not root.exists():
        return []
    output: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.csv")):
        key = path.stem
        symbols = load_index_constituent_symbols(key, cache_dir=root)
        output.append(
            {
                "index_key": key,
                "count": len(symbols),
                "file": str(path),
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return output
