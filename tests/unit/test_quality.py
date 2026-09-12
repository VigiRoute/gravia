"""Tests unitaires de `gravia.quality` — Great Expectations calcule tout en local (aucune infra
requise), seul `validate_silver` a besoin de vrais Parquet Silver (couvert par le test
d'intégration)."""

from __future__ import annotations

import polars as pl
import pytest

from gravia.quality import SilverQualityError, build_expectations, validate_silver, validate_table


def _valid_caracteristiques_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "Num_Acc": ["1", "2"],
            "jour": [5, 12],
            "mois": [3, 11],
            "an": [2023, 2023],
            "lum": [1, -1],
            "agg": [1, 2],
            "int": [-1, 3],
            "atm": [1, -1],
            "col": [1, 2],
            "dep": ["75", "13"],
        }
    )


def test_build_expectations_adds_id_usager_only_when_present() -> None:
    without = build_expectations("usagers", ["Num_Acc", "place", "catu", "grav"])
    with_id = build_expectations("usagers", ["Num_Acc", "id_usager"])

    assert not any(e.column == "id_usager" for e in without if hasattr(e, "column"))
    assert any(getattr(e, "column", None) == "id_usager" for e in with_id)


def test_build_expectations_rejects_unknown_table() -> None:
    with pytest.raises(ValueError, match="Table Silver inconnue"):
        build_expectations("inconnue", [])


def test_validate_table_succeeds_on_conforming_data() -> None:
    result = validate_table("caracteristiques", 2023, _valid_caracteristiques_frame())

    assert result.success
    assert result.failed_expectations == ()


def test_validate_table_fails_on_out_of_range_code() -> None:
    df = _valid_caracteristiques_frame().with_columns(pl.Series("agg", [1, 99]))  # 99 hors {1, 2}

    result = validate_table("caracteristiques", 2023, df)

    assert not result.success
    assert "expect_column_values_to_be_in_set" in result.failed_expectations


def test_validate_silver_raises_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    bad_frame = _valid_caracteristiques_frame().with_columns(pl.Series("agg", [1, 99]))
    path = tmp_path / "bad.parquet"
    bad_frame.write_parquet(path)

    monkeypatch.setattr("gravia.quality.CLEANERS", {"caracteristiques": None})
    monkeypatch.setattr("gravia.quality.silver_path", lambda table, year, settings: path)

    with pytest.raises(SilverQualityError, match="1 table"):
        validate_silver((2023,), settings=None)
