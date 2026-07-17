from __future__ import annotations

from io import BytesIO
import zipfile

from app.data.curated_watchlist import load_curated_watchlist_symbols
from app.data.index_constituents import (
    import_index_constituents_file,
    list_index_constituent_caches,
    load_index_constituent_symbols,
    parse_index_constituents_bytes,
    parse_index_constituents_text,
    sync_index_constituents_url,
    validate_public_https_url,
)
from app.data.universe_0050 import load_0050_symbols, sync_yuanta_0050_symbols
from app.data.universe_mid100 import load_mid100_symbols, sync_yuanta_mid100_symbols
from app.services.universe import normalize_risk_profile, preview_tw_universe, resolve_tw_universe


class _Repo:
    def __init__(
        self,
        *,
        twse_tpex: list[str] | None = None,
        latest_0050: list[str] | None = None,
        recent_liquid: list[str] | None = None,
    ):
        self.twse_tpex = twse_tpex or []
        self.latest_0050 = latest_0050 or []
        self.recent_liquid = recent_liquid or []
        self.recent_liquid_by_industry: list[dict] = []
        self.research_ready_by_industry: list[dict] = []
        self.industry_map: dict[str, str] = {}
        self.recent_bar_count = 0
        self.recent_bar_args: dict | None = None
        self.recent_liquid_args: dict | None = None
        self.recent_liquid_by_industry_args: dict | None = None
        self.research_ready_by_industry_args: dict | None = None

    def get_symbols_by_markets(self, markets):  # noqa: ANN001, ARG002
        return self.twse_tpex

    def get_latest_0050_symbols(self):
        return self.latest_0050

    def get_recent_liquid_symbols(self, **kwargs):  # noqa: ANN003
        self.recent_liquid_args = kwargs
        return self.recent_liquid

    def get_recent_liquid_symbols_by_industry(self, **kwargs):  # noqa: ANN003
        self.recent_liquid_by_industry_args = kwargs
        return self.recent_liquid_by_industry

    def get_research_ready_symbols_by_industry(self, **kwargs):  # noqa: ANN003
        self.research_ready_by_industry_args = kwargs
        return self.research_ready_by_industry

    def count_symbols_with_recent_bars(self, **kwargs):  # noqa: ANN003
        self.recent_bar_args = kwargs
        return self.recent_bar_count

    def get_instrument_industry(self, symbol: str) -> str:
        return self.industry_map.get(symbol, "")


class _SyncService:
    def __init__(self):
        self.sync_count = 0
        self.default_symbols = ["2330", "2317"]

    def sync_twse_tpex_universe(self):
        self.sync_count += 1
        return {"symbols_total": 0}

    def _resolve_symbols(self, symbols):  # noqa: ANN001
        return self.default_symbols if not symbols else symbols


def test_normalize_risk_profile_supports_three_daily_radar_modes():
    assert normalize_risk_profile("conservative") == "conservative"
    assert normalize_risk_profile("defensive") == "conservative"
    assert normalize_risk_profile("balanced") == "balanced"
    assert normalize_risk_profile("aggressive") == "aggressive"
    assert normalize_risk_profile("high-beta") == "aggressive"
    assert normalize_risk_profile("unknown") == "balanced"


def _minimal_xlsx(rows: list[list[str]]) -> bytes:
    def cell_ref(col_index: int, row_index: int) -> str:
        letters = ""
        col = col_index + 1
        while col:
            col, remainder = divmod(col - 1, 26)
            letters = chr(ord("A") + remainder) + letters
        return f"{letters}{row_index + 1}"

    sheet_rows: list[str] = []
    for row_index, row in enumerate(rows):
        cells = []
        for col_index, value in enumerate(row):
            ref = cell_ref(col_index, row_index)
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index + 1}">{"".join(cells)}</row>')

    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                "</Types>"
            ),
        )
        archive.writestr(
            "xl/workbook.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheets><sheet name=\"Sheet1\" sheetId=\"1\" r:id=\"rId1\" "
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>'
                "</workbook>"
            ),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
            ),
        )
    return output.getvalue()


def test_load_curated_watchlist_symbols_supports_local_override(tmp_path):
    watchlist = tmp_path / "curated_watchlist.txt"
    watchlist.write_text(
        "\n".join(
            [
                "# local daily candidates",
                "2330.TW",
                "2454",
                "2454.TW  # duplicate with suffix",
                "  ",
                "2317.TWO",
            ]
        ),
        encoding="utf-8",
    )

    assert load_curated_watchlist_symbols(watchlist) == ["2317", "2330", "2454"]


def test_load_mid100_symbols_supports_local_override(tmp_path):
    watchlist = tmp_path / "tw_mid100_symbols.txt"
    watchlist.write_text(
        "\n".join(
            [
                "# local mid-cap pool",
                "2301.TW",
                "2324",
                "2324.TW  # duplicate with suffix",
                "  ",
                "3105.TWO",
            ]
        ),
        encoding="utf-8",
    )

    assert load_mid100_symbols(watchlist) == ["2301", "2324", "3105"]


def test_load_0050_symbols_supports_local_override(tmp_path):
    watchlist = tmp_path / "tw0050_symbols.txt"
    watchlist.write_text(
        "\n".join(
            [
                "# local Taiwan 50 pool",
                "2330.TW",
                "2454",
                "2454.TW  # duplicate with suffix",
                "  ",
                "2881.TWO",
            ]
        ),
        encoding="utf-8",
    )

    assert load_0050_symbols(watchlist) == ["2330", "2454", "2881"]


def test_sync_yuanta_mid100_symbols_writes_pcf_cache(tmp_path):
    cache_file = tmp_path / "tw_mid100_symbols.txt"
    payload = {
        "PCF": {"trandate": "20260624", "upddate": "2026-06-24 17:06:08"},
        "InKind": {
            "FundComposition": [
                {"stkcd": "1101", "name": "台泥", "qty": 41809, "cashinlieu": "N", "minimum": "Y"},
                {"stkcd": "2301.TW", "name": "光寶科", "qty": 1234, "cashinlieu": "N", "minimum": "N"},
            ]
        },
    }

    result = sync_yuanta_mid100_symbols(symbols_file=cache_file, payload=payload)

    content = cache_file.read_text(encoding="utf-8")
    assert result["source"] == "yuanta_0051_pcf"
    assert result["trade_date"] == "20260624"
    assert result["symbols"] == ["1101", "2301"]
    assert "source=yuanta_0051_pcf" in content
    assert "trade_date=20260624" in content
    assert "1101 # 台泥 qty=41809" in content
    assert "2301 # 光寶科 qty=1234" in content
    assert load_mid100_symbols(cache_file) == ["1101", "2301"]


def test_sync_yuanta_0050_symbols_writes_pcf_cache(tmp_path):
    cache_file = tmp_path / "tw0050_symbols.txt"
    payload = {
        "PCF": {"trandate": "20260626", "upddate": "2026-06-26 17:06:08"},
        "InKind": {
            "FundComposition": [
                {"stkcd": "2330", "name": "台積電", "qty": 12345, "cashinlieu": "N", "minimum": "Y"},
                {"stkcd": "2454.TW", "name": "聯發科", "qty": 678, "cashinlieu": "N", "minimum": "N"},
            ]
        },
    }

    result = sync_yuanta_0050_symbols(symbols_file=cache_file, payload=payload)

    content = cache_file.read_text(encoding="utf-8")
    assert result["source"] == "yuanta_0050_pcf"
    assert result["trade_date"] == "20260626"
    assert result["symbols"] == ["2330", "2454"]
    assert "source=yuanta_0050_pcf" in content
    assert "trade_date=20260626" in content
    assert "2330 # 台積電 qty=12345" in content
    assert "2454 # 聯發科 qty=678" in content
    assert load_0050_symbols(cache_file) == ["2330", "2454"]


def test_import_index_constituents_supports_official_csv_like_file(tmp_path):
    source_file = tmp_path / "quality50.csv"
    source_file.write_text(
        "證券代號,證券名稱,權重\n2330,台積電,10.5\n2454.TW,聯發科,3.2\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "index_constituents"

    parsed = parse_index_constituents_text(source_file.read_text(encoding="utf-8"), index_key="tw_quality_50")
    result = import_index_constituents_file(
        source_file,
        index_key="tw_quality_50",
        cache_dir=cache_dir,
        source="official_csv",
    )

    assert [row["symbol"] for row in parsed] == ["2330", "2454"]
    assert result["index_key"] == "tw_quality_50"
    assert result["symbols"] == ["2330", "2454"]
    assert load_index_constituent_symbols("tw_quality_50", cache_dir=cache_dir) == ["2330", "2454"]
    listed = list_index_constituent_caches(cache_dir=cache_dir)
    assert listed[0]["index_key"] == "tw_quality_50"
    assert listed[0]["count"] == 2


def test_import_index_constituents_supports_official_xlsx_file(tmp_path):
    source_file = tmp_path / "tip_constituents.xlsx"
    source_file.write_bytes(
        _minimal_xlsx(
            [
                ["臺灣指數公司收盤成分股檔"],
                ["證券代號", "證券名稱", "權重"],
                ["2330", "台積電", "12.5"],
                ["2454.TW", "聯發科", "4.1"],
            ]
        )
    )
    cache_dir = tmp_path / "index_constituents"

    parsed = parse_index_constituents_bytes(source_file.read_bytes(), filename=source_file.name, index_key="tip_tw50")
    result = import_index_constituents_file(
        source_file,
        index_key="tip_tw50",
        cache_dir=cache_dir,
        source="tip_xlsx",
    )

    assert [row["symbol"] for row in parsed] == ["2330", "2454"]
    assert result["symbols"] == ["2330", "2454"]
    assert load_index_constituent_symbols("tip_tw50", cache_dir=cache_dir) == ["2330", "2454"]


def test_sync_index_constituents_url_downloads_and_writes_cache(monkeypatch, tmp_path):
    class _Response:
        url = "https://backend.taiwanindex.com.tw/files/tip_constituents.xlsx"
        headers = {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        content = _minimal_xlsx(
            [
                ["證券代號", "證券名稱"],
                ["1101", "台泥"],
                ["2303", "聯電"],
            ]
        )

        def raise_for_status(self):
            return None

    def fake_get(url, timeout):  # noqa: ANN001
        assert url == "https://backend.taiwanindex.com.tw/files/tip_constituents.xlsx"
        assert timeout == 15
        return _Response()

    monkeypatch.setattr("app.data.index_constituents.requests.get", fake_get)

    result = sync_index_constituents_url(
        "https://backend.taiwanindex.com.tw/files/tip_constituents.xlsx",
        index_key="tip_sample",
        cache_dir=tmp_path,
        source="tip_official_url",
    )

    assert result["index_key"] == "tip_sample"
    assert result["symbols"] == ["1101", "2303"]
    assert result["url"] == "https://backend.taiwanindex.com.tw/files/tip_constituents.xlsx"
    assert load_index_constituent_symbols("tip_sample", cache_dir=tmp_path) == ["1101", "2303"]


def test_validate_public_https_url_rejects_insecure_or_local_urls():
    assert validate_public_https_url("https://backend.taiwanindex.com.tw/file.xlsx") == (
        "https://backend.taiwanindex.com.tw/file.xlsx"
    )

    for url in ("http://backend.taiwanindex.com.tw/file.csv", "https://127.0.0.1/file.csv", "https://localhost/file.csv"):
        try:
            validate_public_https_url(url)
        except ValueError:
            continue
        raise AssertionError(f"URL should have been rejected: {url}")


def test_resolve_tw_universe_defaults_to_curated_watchlist(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_curated_watchlist_symbols", lambda: ["2330", "2454"])

    sync_service = _SyncService()
    symbols = resolve_tw_universe(_Repo(twse_tpex=["1111"]), sync_service, "")

    assert symbols == ["2330", "2454"]
    assert sync_service.sync_count == 0


def test_resolve_tw_universe_dynamic_liquid_uses_recent_turnover_and_exclusions(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {"9999": "event risk"})
    repo = _Repo(recent_liquid=["2301", "2324", "3105"])

    symbols = resolve_tw_universe(repo, _SyncService(), "dynamic_liquid")

    assert symbols == ["2301", "2324", "3105"]
    assert repo.recent_liquid_args == {
        "markets": ["TWSE", "TPEX"],
        "exclude_symbols": ["9999"],
        "limit": 80,
        "lookback_days": 90,
        "min_bars": 5,
    }


def test_resolve_tw_universe_dynamic_balanced_prefers_industry_rotation(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {"2330": "no touch"})
    repo = _Repo()
    repo.recent_liquid_by_industry = [
        {"symbol": "1101", "industry": "水泥工業", "avg_turnover": 100.0},
        {"symbol": "2881", "industry": "金融保險業", "avg_turnover": 90.0},
        {"symbol": "2301", "industry": "電子零組件業", "avg_turnover": 80.0},
    ]

    symbols = resolve_tw_universe(repo, _SyncService(), "dynamic_balanced")

    assert symbols == ["1101", "2881", "2301"]
    assert repo.recent_liquid_by_industry_args == {
        "markets": ["TWSE", "TPEX"],
        "exclude_symbols": ["2330"],
        "limit": 60,
        "per_industry": 3,
        "lookback_days": 90,
        "min_bars": 5,
    }
    assert repo.recent_liquid_args is None


def test_resolve_tw_universe_dynamic_balanced_falls_back_to_curated(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {})
    monkeypatch.setattr("app.services.universe.load_curated_watchlist_symbols", lambda: ["2330", "2454"])
    repo = _Repo(recent_liquid=[])

    symbols = resolve_tw_universe(repo, _SyncService(), "dynamic_balanced")

    assert symbols == ["2330", "2454"]


def test_preview_tw_universe_describes_dynamic_balanced_quality(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {"9999": "event risk"})
    repo = _Repo()
    repo.recent_bar_count = 2
    repo.recent_liquid_by_industry = [
        {"symbol": "1101", "industry": "水泥工業", "avg_turnover": 100.0},
        {"symbol": "2881", "industry": "金融保險業", "avg_turnover": 90.0},
        {"symbol": "2301", "industry": "電子零組件業", "avg_turnover": 80.0},
    ]

    preview = preview_tw_universe(
        repo,
        _SyncService(),
        "dynamic_balanced",
        trade_date="2026-07-10",
        freshness_days=3,
    )

    assert preview["status"] == "warn"
    assert preview["source"] == "dynamic_balanced"
    assert preview["symbol_count"] == 3
    assert preview["sample_symbols"] == ["1101", "2301", "2881"]
    assert preview["freshness"]["coverage_pct"] == 66.7
    assert preview["industry_counts"] == [
        {"industry": "水泥工業", "count": 1},
        {"industry": "金融保險業", "count": 1},
        {"industry": "電子零組件業", "count": 1},
    ]
    assert repo.recent_bar_args["min_date"].isoformat() == "2026-07-07"
    assert any("近期日線覆蓋偏低" in message for message in preview["messages"])


def test_preview_tw_universe_surfaces_fallback_and_no_touch_overlap(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {"2330": "no touch"})
    monkeypatch.setattr("app.services.universe.load_curated_watchlist_symbols", lambda: ["2330", "2454"])
    repo = _Repo(recent_liquid=[])
    repo.recent_bar_count = 2
    repo.industry_map = {"2330": "半導體業", "2454": "半導體業"}

    preview = preview_tw_universe(repo, _SyncService(), "dynamic_liquid", trade_date="2026-07-10")

    assert preview["status"] == "warn"
    assert preview["source"] == "fallback_curated"
    assert preview["fallback_used"] is True
    assert preview["no_touch_overlap"] == ["2330"]
    assert preview["industry_counts"] == [{"industry": "半導體業", "count": 2}]
    assert any("動態流動性池目前沒有足夠資料" in message for message in preview["messages"])


def test_preview_tw_universe_warns_when_balanced_pool_uses_liquidity_fallback(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {})
    repo = _Repo(recent_liquid=["1101", "2301", "2881"])
    repo.recent_bar_count = 3
    repo.industry_map = {"1101": "水泥工業", "2301": "電子零組件業", "2881": "金融保險業"}

    preview = preview_tw_universe(repo, _SyncService(), "dynamic_balanced", trade_date="2026-07-10")

    assert preview["status"] == "warn"
    assert preview["source"] == "dynamic_balanced_liquid_fallback"
    assert any("產業分散資料不足" in message for message in preview["messages"])


def test_preview_tw_universe_warns_when_balanced_pool_uses_symbol_proxy_groups(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {})
    repo = _Repo()
    repo.recent_bar_count = 3
    repo.recent_liquid_by_industry = [
        {"symbol": "1101", "industry": "水泥/建材", "industry_source": "symbol_proxy", "avg_turnover": 100.0},
        {"symbol": "2301", "industry": "電子/科技", "industry_source": "symbol_proxy", "avg_turnover": 90.0},
        {"symbol": "2881", "industry": "金融/保險", "industry_source": "symbol_proxy", "avg_turnover": 80.0},
    ]

    preview = preview_tw_universe(repo, _SyncService(), "dynamic_balanced", trade_date="2026-07-10")

    assert preview["status"] == "warn"
    assert preview["source"] == "dynamic_balanced"
    assert preview["industry_source_counts"] == {"symbol_proxy": 3}
    assert preview["industry_proxy_ratio"] == 1.0
    assert any("代號前綴 proxy" in message for message in preview["messages"])


def test_resolve_tw_universe_research_ready_uses_strict_quality_pool(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {"9999": "event risk"})
    repo = _Repo()
    repo.research_ready_by_industry = [
        {"symbol": "2454", "industry": "半導體業", "history_bars": 120, "chip_days": 20},
        {"symbol": "2881", "industry": "金融保險業", "history_bars": 110, "chip_days": 18},
    ]

    symbols = resolve_tw_universe(repo, _SyncService(), "research_ready")

    assert symbols == ["2454", "2881"]
    assert repo.research_ready_by_industry_args["min_history_bars"] == 60
    assert repo.research_ready_by_industry_args["min_chip_days"] == 5
    assert repo.research_ready_by_industry_args["exclude_symbols"] == ["9999"]


def test_preview_tw_universe_research_ready_surfaces_quality_settings(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_symbol_exclusions", lambda: {})
    repo = _Repo()
    repo.recent_bar_count = 3
    repo.research_ready_by_industry = [
        {
            "symbol": "2454",
            "industry": "半導體業",
            "industry_source": "official",
            "history_bars": 120,
            "chip_days": 20,
        },
        {
            "symbol": "2881",
            "industry": "金融保險業",
            "industry_source": "official",
            "history_bars": 110,
            "chip_days": 18,
        },
        {
            "symbol": "1101",
            "industry": "水泥工業",
            "industry_source": "official",
            "history_bars": 100,
            "chip_days": 16,
        },
    ]

    preview = preview_tw_universe(repo, _SyncService(), "research_ready", trade_date="2026-07-10")

    assert preview["status"] == "ok"
    assert preview["source"] == "research_ready"
    assert preview["source_label"] == "研究就緒池"
    assert preview["symbol_count"] == 3
    assert preview["settings"]["research_ready_min_history_bars"] == 60
    assert preview["settings"]["research_ready_min_chip_days"] == 5


def test_resolve_tw_universe_prefers_repo_0050_symbols():
    symbols = resolve_tw_universe(_Repo(latest_0050=["0050A", "0050B"]), _SyncService(), "tw0050")

    assert symbols == ["0050A", "0050B"]


def test_resolve_tw_universe_loads_imported_index_constituents(monkeypatch):
    monkeypatch.setattr("app.services.universe.load_index_constituent_symbols", lambda key: ["2330", "2454"] if key == "custom" else [])

    symbols = resolve_tw_universe(_Repo(), _SyncService(), "index:custom")

    assert symbols == ["2330", "2454"]


def test_resolve_tw_universe_prefers_local_mid100(monkeypatch):
    monkeypatch.setattr(
        "app.services.universe.load_mid100_symbols",
        lambda *, fallback=True: ["2301", "2324"] if fallback is False else ["9999"],
    )
    repo = _Repo(recent_liquid=["1111"])

    symbols = resolve_tw_universe(repo, _SyncService(), "tw_mid100")

    assert symbols == ["2301", "2324"]
    assert repo.recent_liquid_args is None


def test_resolve_tw_universe_derives_mid100_by_recent_liquidity(monkeypatch):
    monkeypatch.setattr(
        "app.services.universe.load_mid100_symbols",
        lambda *, fallback=True: [] if fallback is False else ["9999"],
    )
    repo = _Repo(latest_0050=["2330"], recent_liquid=["2301", "2324", "3105"])

    symbols = resolve_tw_universe(repo, _SyncService(), "mid100")

    assert symbols == ["2301", "2324", "3105"]
    assert repo.recent_liquid_args == {
        "markets": ["TWSE", "TPEX"],
        "exclude_symbols": ["2330"],
        "limit": 100,
        "lookback_days": 90,
        "min_bars": 5,
    }


def test_resolve_tw_universe_can_skip_unknown_fallback():
    symbols = resolve_tw_universe(_Repo(), _SyncService(), "mystery", fallback_to_default=False)

    assert symbols == []


def test_index_constituents_cli_parser_exposes_import_and_list_commands():
    from app.cli import (
        build_parser,
        cmd_import_index_constituents,
        cmd_list_index_constituents,
        cmd_sync_index_constituents_url,
    )

    import_args = build_parser().parse_args(
        [
            "import-index-constituents",
            "--index-key",
            "custom",
            "--file",
            "constituents.csv",
            "--source",
            "official_csv",
        ]
    )
    sync_args = build_parser().parse_args(
        [
            "sync-index-constituents-url",
            "--index-key",
            "tip_tw50",
            "--url",
            "https://backend.taiwanindex.com.tw/files/tip_constituents.xlsx",
            "--source",
            "tip_url",
        ]
    )
    list_args = build_parser().parse_args(["list-index-constituents"])

    assert import_args.func is cmd_import_index_constituents
    assert import_args.index_key == "custom"
    assert import_args.file == "constituents.csv"
    assert import_args.source == "official_csv"
    assert sync_args.func is cmd_sync_index_constituents_url
    assert sync_args.index_key == "tip_tw50"
    assert sync_args.url == "https://backend.taiwanindex.com.tw/files/tip_constituents.xlsx"
    assert sync_args.source == "tip_url"
    assert list_args.func is cmd_list_index_constituents
