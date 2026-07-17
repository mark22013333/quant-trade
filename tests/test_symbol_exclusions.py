from __future__ import annotations

from app.data.symbol_exclusions import (
    load_symbol_exclusions,
    remove_symbol_exclusion,
    save_symbol_exclusions,
    upsert_symbol_exclusion,
)


def test_load_symbol_exclusions_supports_suffixes_comments_and_reasons(tmp_path):
    exclusions = tmp_path / "excluded_symbols.txt"
    exclusions.write_text(
        "\n".join(
            [
                "# local no-touch list",
                "2330.TW # valuation too stretched",
                "2317.TWO, event risk",
                "2454",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert load_symbol_exclusions(exclusions) == {
        "2317": "event risk",
        "2330": "valuation too stretched",
        "2454": "manual_exclusion",
    }


def test_save_symbol_exclusions_normalizes_and_sorts(tmp_path):
    exclusions = tmp_path / "excluded_symbols.txt"

    save_symbol_exclusions(
        {
            "2330.TW": "valuation",
            "2317.TWO": "",
            "2454": "event risk",
        },
        exclusions,
    )

    assert exclusions.read_text(encoding="utf-8").splitlines() == [
        "# quant-trade no-touch list: symbol, reason",
        "2317, manual_exclusion",
        "2330, valuation",
        "2454, event risk",
    ]
    assert load_symbol_exclusions(exclusions) == {
        "2317": "manual_exclusion",
        "2330": "valuation",
        "2454": "event risk",
    }


def test_upsert_and_remove_symbol_exclusion_roundtrip(tmp_path):
    exclusions = tmp_path / "excluded_symbols.txt"

    after_insert = upsert_symbol_exclusion("2330.TW", "valuation", exclusions)
    after_update = upsert_symbol_exclusion("2330", "earnings event", exclusions)
    after_second_insert = upsert_symbol_exclusion("2317.TWO", "", exclusions)
    after_remove = remove_symbol_exclusion("2330.TW", exclusions)

    assert after_insert == {"2330": "valuation"}
    assert after_update == {"2330": "earnings event"}
    assert after_second_insert == {"2317": "manual_exclusion", "2330": "earnings event"}
    assert after_remove == {"2317": "manual_exclusion"}
    assert load_symbol_exclusions(exclusions) == {"2317": "manual_exclusion"}
