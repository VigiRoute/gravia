"""Tests unitaires de ml/features/gold_features.py."""

import polars as pl

from ml.features.gold_features import (
    BASELINE_CATEGORICAL_COLUMNS,
    BASELINE_NUMERIC_COLUMNS,
    TEST_YEAR,
    TRAIN_YEARS_BEFORE,
    UNVALIDATED_NUMERIC_COLUMNS,
    VALID_YEAR,
    YEAR_COLUMN,
    prepare_features,
    split_train_valid_test,
)


def _minimal_frame() -> pl.DataFrame:
    """Une ligne par colonne attendue par `prepare_features`, valeurs minimales suffisantes."""
    columns = {c: [-1] for c in BASELINE_CATEGORICAL_COLUMNS}
    columns |= {c: [1] for c in (*BASELINE_NUMERIC_COLUMNS, *UNVALIDATED_NUMERIC_COLUMNS)}
    return pl.DataFrame(columns)


def test_prepare_features_casts_categorical_with_negative_sentinel() -> None:
    """Piège Polars réel : caster un entier directement en Categorical échoue sur `-1` (traité
    comme un code de catégorie invalide, pas comme une valeur) — cf. docstring du module."""
    df = _minimal_frame()

    result = prepare_features(df)

    for column in BASELINE_CATEGORICAL_COLUMNS:
        assert result.schema[column] == pl.Categorical
        assert result[column][0] == "-1"


def test_prepare_features_casts_numeric_to_float() -> None:
    df = _minimal_frame()

    result = prepare_features(df)

    for column in BASELINE_NUMERIC_COLUMNS:
        assert result.schema[column] == pl.Float64


def test_split_train_valid_test_partitions_by_year() -> None:
    df = pl.DataFrame({YEAR_COLUMN: [2019, 2020, 2021, 2022, 2023], "value": range(5)})

    train, valid, test = split_train_valid_test(df)

    assert train[YEAR_COLUMN].to_list() == [2019, 2020, 2021]
    assert valid[YEAR_COLUMN].to_list() == [VALID_YEAR]
    assert test[YEAR_COLUMN].to_list() == [TEST_YEAR]
    assert all(year < TRAIN_YEARS_BEFORE for year in train[YEAR_COLUMN].to_list())
