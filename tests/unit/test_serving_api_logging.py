"""Tests unitaires de `ml/serving/api.py::JsonFormatter` (logique pure, pas d'import de l'app
FastAPI ni de MLflow — cf. `tests/integration/test_serving_api.py` pour la vérification bout en
bout, journal réellement émis compris)."""

from __future__ import annotations

import json
import logging

from ml.serving.api import JsonFormatter


def _make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="gravia.serving",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="prediction",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_format_produces_valid_json_with_standard_fields() -> None:
    record = _make_record()

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "gravia.serving"
    assert payload["message"] == "prediction"
    assert "timestamp" in payload


def test_format_includes_extra_fields_passed_to_the_logger() -> None:
    record = _make_record(event="prediction", departement="75", probabilite=0.42)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "prediction"
    assert payload["departement"] == "75"
    assert payload["probabilite"] == 0.42


def test_format_excludes_standard_log_record_internals() -> None:
    """`extra` ne doit contenir que ce qui a été explicitement ajouté au record, pas les
    attributs internes de logging (pathname, lineno, args, ...) — sinon le JSON serait aussi
    verbeux et difficile à requêter que le texte libre qu'il remplace."""
    record = _make_record(departement="75")

    payload = json.loads(JsonFormatter().format(record))

    assert "pathname" not in payload
    assert "lineno" not in payload
    assert "args" not in payload
    assert "msg" not in payload
