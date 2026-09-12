"""Test d'intégration de `gravia.quality` contre les vrais Parquet Silver produits localement.

Contrairement aux autres tests d'intégration du dépôt, aucun service externe n'est requis (Great
Expectations valide entièrement en mémoire) : la seule dépendance est que la couche Silver ait
déjà été produite localement (`python -m gravia.silver`) — ignoré (`pytest.skip`) sinon, plutôt
que d'échouer sur un état de dépôt normal (Silver n'est jamais committé, cf. `.gitignore`).
"""

from __future__ import annotations

import pytest

from gravia.config import get_settings
from gravia.quality import validate_silver
from gravia.silver import silver_path

TEST_YEAR = 2023


def test_validate_silver_passes_on_real_data() -> None:
    settings = get_settings()
    if not silver_path("caracteristiques", TEST_YEAR, settings).exists():
        pytest.skip(
            f"Silver {TEST_YEAR} non produit localement (lancer `python -m gravia.silver` d'abord)"
        )

    results = validate_silver((TEST_YEAR,), settings)

    assert len(results) == 4
    assert all(r.success for r in results)
