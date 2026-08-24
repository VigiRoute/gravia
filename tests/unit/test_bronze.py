"""Tests unitaires de la couche Bronze (src/gravia/bronze.py)."""

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from gravia.bronze import (
    ACCIDENT_ID,
    MissingAccidentIdError,
    SourceFileNotFoundError,
    add_provenance,
    ingest,
    ingest_table,
    normalize_accident_id,
    read_raw_csv,
    resolve_source_file,
    upload,
)
from gravia.config import Settings, StoragePaths


def test_resolve_source_file_finds_correct_spelling(tmp_path: Path) -> None:
    (tmp_path / "caracteristiques-2019.csv").write_text("Num_Acc\n1\n", encoding="utf-8")
    found = resolve_source_file("caracteristiques", 2019, tmp_path)
    assert found.name == "caracteristiques-2019.csv"


def test_resolve_source_file_finds_producer_typo(tmp_path: Path) -> None:
    (tmp_path / "carcteristiques-2021.csv").write_text("Num_Acc\n1\n", encoding="utf-8")
    found = resolve_source_file("caracteristiques", 2021, tmp_path)
    assert found.name == "carcteristiques-2021.csv"


def test_resolve_source_file_finds_shortened_2023_name(tmp_path: Path) -> None:
    (tmp_path / "caract-2023.csv").write_text("Num_Acc\n1\n", encoding="utf-8")
    found = resolve_source_file("caracteristiques", 2023, tmp_path)
    assert found.name == "caract-2023.csv"


def test_resolve_source_file_raises_when_absent(tmp_path: Path) -> None:
    with pytest.raises(SourceFileNotFoundError):
        resolve_source_file("lieux", 2021, tmp_path)


def test_normalize_accident_id_leaves_num_acc_untouched(tmp_path: Path) -> None:
    df = pl.DataFrame({"Num_Acc": ["1"], "jour": ["05"]})
    result = normalize_accident_id(df, tmp_path / "caracteristiques-2019.csv")
    assert result.columns == ["Num_Acc", "jour"]


def test_normalize_accident_id_renames_2022_variant(tmp_path: Path) -> None:
    df = pl.DataFrame({"Accident_Id": ["1"], "jour": ["05"]})
    result = normalize_accident_id(df, tmp_path / "carcteristiques-2022.csv")
    assert ACCIDENT_ID in result.columns
    assert "Accident_Id" not in result.columns


def test_normalize_accident_id_raises_without_known_alias(tmp_path: Path) -> None:
    df = pl.DataFrame({"jour": ["05"]})
    with pytest.raises(MissingAccidentIdError):
        normalize_accident_id(df, tmp_path / "caracteristiques-2019.csv")


def test_read_raw_csv_preserves_leading_zero_and_sentinel(tmp_path: Path) -> None:
    """Fidélité à la source : ni le zéro de tête ni la sentinelle ' -1' ne doivent être perdus."""
    path = tmp_path / "usagers-2023.csv"
    path.write_text("Num_Acc;jour;grav\n1;05; -1\n", encoding="utf-8")

    df = read_raw_csv(path)

    assert df.schema["jour"] == pl.Utf8
    assert df["jour"][0] == "05"
    assert df["grav"][0] == " -1"


def test_add_provenance_columns() -> None:
    df = pl.DataFrame({"Num_Acc": ["1"]})
    ingested_at = datetime(2026, 1, 1, tzinfo=UTC)

    result = add_provenance(df, 2023, Path("caracteristiques-2023.csv"), ingested_at)

    assert result["_millesime"][0] == 2023
    assert result["_source_file"][0] == "caracteristiques-2023.csv"
    assert result["_ingested_at"][0] == ingested_at


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        paths=StoragePaths(
            raw=tmp_path / "raw", bronze=tmp_path / "bronze", silver=tmp_path / "silver"
        )
    )


def test_ingest_table_writes_parquet_with_provenance(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.baac.mkdir(parents=True, exist_ok=True)
    (settings.paths.baac / "caracteristiques-2023.csv").write_text(
        "Num_Acc;jour\n1;05\n2;12\n", encoding="utf-8"
    )

    destination = ingest_table("caracteristiques", 2023, settings, datetime(2026, 1, 1, tzinfo=UTC))

    result = pl.read_parquet(destination)
    assert result.height == 2
    assert result["_millesime"][0] == 2023
    assert result["jour"][0] == "05"  # zéro de tête préservé (fidélité à la source)


def test_ingest_runs_local_pipeline_for_all_four_tables(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    baac_dir = settings.paths.baac
    baac_dir.mkdir(parents=True, exist_ok=True)
    (baac_dir / "caracteristiques-2023.csv").write_text("Num_Acc;jour\n1;05\n", encoding="utf-8")
    (baac_dir / "lieux-2023.csv").write_text("Num_Acc;catr\n1;3\n", encoding="utf-8")
    (baac_dir / "vehicules-2023.csv").write_text("Num_Acc;catv\n1;7\n", encoding="utf-8")
    (baac_dir / "usagers-2023.csv").write_text("Num_Acc;grav\n1;1\n", encoding="utf-8")

    produced = ingest((2023,), settings, do_upload=False)

    assert len(produced) == 4
    assert all(path.exists() for path in produced.values())


def test_upload_calls_s3_client_for_each_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    local_file = tmp_path / "part-0.parquet"
    pl.DataFrame({"a": [1]}).write_parquet(local_file)
    calls: list[tuple[str, str, str]] = []

    class FakeS3Client:
        def upload_file(self, filename: str, bucket: str, key: str) -> None:
            calls.append((filename, bucket, key))

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: FakeS3Client())

    key = "bronze/baac/caracteristiques/millesime=2023/part-0.parquet"
    upload({key: local_file}, settings)

    assert calls == [(str(local_file), settings.object_store.bucket, key)]
