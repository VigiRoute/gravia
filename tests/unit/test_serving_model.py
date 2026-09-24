"""Tests unitaires de ml/serving/model.py (logique pure, sans MLflow)."""

from datetime import datetime

import numpy as np

from ml.serving.model import _positive_class_shap_values, build_feature_frame
from ml.serving.schemas import PredictSeverityRequest
from ml.training.benchmark import feature_columns


def _sample_request(**overrides: object) -> PredictSeverityRequest:
    defaults = dict(
        moment=datetime(2026, 8, 27, 14, 30),
        departement="75",
        agglomeration=True,
        intersection=1,
        categorie_route=4,
        regime_circulation=2,
        nb_voies=2,
        voie_reservee=0,
        profil_route=1,
        trace_plan=1,
        vitesse_max=50,
        infrastructure=0,
        situation=1,
        luminosite=1,
        meteo=1,
        etat_surface=1,
        type_collision=3,
        nb_vehicules=2,
        flag_2roues_motorise=True,
        flag_poids_lourd=False,
        flag_velo_edp=False,
        flag_pieton=False,
    )
    defaults.update(overrides)
    return PredictSeverityRequest(**defaults)


def test_build_feature_frame_has_expected_columns_and_dtypes() -> None:
    categorical, other = feature_columns("enriched")
    request = _sample_request()

    frame = build_feature_frame(request, tuple(categorical), tuple(other))

    assert list(frame.columns) == categorical + other
    assert frame.height if hasattr(frame, "height") else len(frame) == 1
    for column in categorical:
        assert str(frame[column].dtype) == "category"


def test_build_feature_frame_derives_temporal_fields_from_moment() -> None:
    categorical, other = feature_columns("enriched")
    request = _sample_request(moment=datetime(2026, 8, 27, 9, 15))  # jeudi 27 août 2026

    frame = build_feature_frame(request, tuple(categorical), tuple(other))

    assert frame["heure"].iloc[0] == 9.0
    assert frame["mois"].iloc[0] == 8.0


def test_build_feature_frame_decodes_collision_code_to_gold_label() -> None:
    categorical, other = feature_columns("enriched")
    request = _sample_request(type_collision=7)  # 7 = sans collision (dictionnaire ONISR)

    frame = build_feature_frame(request, tuple(categorical), tuple(other))

    assert frame["type_collision"].iloc[0] == "Sans collision"


def test_build_feature_frame_falls_back_to_unknown_for_unmapped_collision_code() -> None:
    categorical, other = feature_columns("enriched")
    request = _sample_request(type_collision=99)  # code hors nomenclature ONISR

    frame = build_feature_frame(request, tuple(categorical), tuple(other))

    assert frame["type_collision"].iloc[0] == "Non renseigné"


def test_positive_class_shap_values_handles_plain_array() -> None:
    class FakeExplainer:
        def shap_values(self, frame):
            return np.array([[0.1, -0.2, 0.3]])

    result = _positive_class_shap_values(FakeExplainer(), frame=None)

    assert list(result) == [0.1, -0.2, 0.3]


def test_positive_class_shap_values_handles_per_class_list() -> None:
    class FakeExplainer:
        def shap_values(self, frame):
            return [np.array([[-0.1, 0.2, -0.3]]), np.array([[0.1, -0.2, 0.3]])]

    result = _positive_class_shap_values(FakeExplainer(), frame=None)

    assert list(result) == [0.1, -0.2, 0.3]
