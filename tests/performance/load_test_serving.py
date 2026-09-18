"""Test de charge de `POST /v1/predict-severity` contre le conteneur `serving` réel.

Pas un test pytest (nom volontairement hors du motif `test_*.py`/`*_test.py` — pytest ne le
collecte donc pas dans `tests/unit`/`tests/integration`) : c'est un script à lancer à la main,
contre la stack dev démarrée, pas une assertion à faire tourner en CI à chaque commit — un test
de charge est lent et son résultat dépend de la machine hôte.

Une latence mesurée en séquentiel (une requête à la fois, ce que fait un simple test manuel ou
`TestClient`) ne dit rien de la capacité sous charge concurrente. Constaté en testant : avec un
seul worker uvicorn (image d'origine), le débit plafonnait à ~65 req/s quelle que soit la
concurrence et p95 dépassait largement les 300 ms visés (CDC ENF-1) dès 25 requêtes simultanées
— alors qu'un test séquentiel affichait p95=16 ms, une évaluation trompeuse de la vraie capacité.
Corrigé en passant à 4 workers uvicorn (`infra/serving/Dockerfile`) : p95 revient sous 300 ms
jusqu'à ~25-50 requêtes simultanées (cf. docs/AVANCEMENT_GRAVIA.md pour les chiffres détaillés).

Usage :
    python -m tests.performance.load_test_serving
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

URL = "http://localhost:8000/v1/predict-severity"

#: Cible CDC (ENF-1, cf. CLAUDE.md, seuils et métriques).
TARGET_P95_MS = 300

PAYLOAD = {
    "moment": "2026-08-27T14:30:00",
    "departement": "75",
    "agglomeration": True,
    "intersection": 1,
    "categorie_route": 4,
    "regime_circulation": 2,
    "nb_voies": 2,
    "voie_reservee": 0,
    "profil_route": 1,
    "trace_plan": 1,
    "vitesse_max": 50,
    "infrastructure": 0,
    "situation": 1,
    "luminosite": 1,
    "meteo": 1,
    "etat_surface": 1,
    "type_collision": 3,
    "nb_vehicules": 2,
    "flag_2roues_motorise": True,
    "flag_poids_lourd": False,
    "flag_velo_edp": False,
    "flag_pieton": False,
}


def _one_request() -> tuple[float, int]:
    """Envoie une requête réelle sur le réseau (stdlib, pas de dépendance HTTP ajoutée pour un
    simple script de diagnostic) et mesure sa durée.

    Returns:
        `(durée en ms, code HTTP)`.
    """
    body = json.dumps(PAYLOAD).encode("utf-8")
    request = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    duration_ms = (time.perf_counter() - start) * 1000
    return duration_ms, status


def run_load(concurrency: int, total_requests: int) -> dict[str, float]:
    """Envoie `total_requests` requêtes avec `concurrency` en vol simultanément.

    Args:
        concurrency: Nombre de requêtes envoyées en parallèle (taille du pool de threads).
        total_requests: Nombre total de requêtes à envoyer.

    Returns:
        Débit et percentiles de latence mesurés.
    """
    durations: list[float] = []
    errors = 0

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(_one_request) for _ in range(total_requests)]
        for future in as_completed(futures):
            duration_ms, status = future.result()
            if status == 200:
                durations.append(duration_ms)
            else:
                errors += 1
    wall_elapsed = time.perf_counter() - wall_start

    durations.sort()
    n = len(durations)
    return {
        "concurrency": concurrency,
        "requests": total_requests,
        "errors": errors,
        "throughput_rps": total_requests / wall_elapsed,
        "p50_ms": durations[int(n * 0.50)],
        "p95_ms": durations[min(int(n * 0.95), n - 1)],
        "p99_ms": durations[min(int(n * 0.99), n - 1)],
        "max_ms": durations[-1],
        "mean_ms": statistics.mean(durations),
    }


def main() -> None:
    """Lance le test de charge à plusieurs niveaux de concurrence et affiche les résultats."""
    print(
        f"Test de charge réel (HTTP, requêtes concurrentes) — cible CDC p95 < {TARGET_P95_MS} ms\n"
    )
    for concurrency in (1, 5, 10, 25, 50):
        result = run_load(concurrency=concurrency, total_requests=max(100, concurrency * 4))
        gate = "OK" if result["p95_ms"] < TARGET_P95_MS else "AU-DESSUS DU SEUIL CDC"
        print(
            f"concurrency={result['concurrency']:3.0f}  requests={result['requests']:4.0f}  "
            f"errors={result['errors']:.0f}  throughput={result['throughput_rps']:6.1f} req/s  "
            f"p50={result['p50_ms']:6.1f}ms  p95={result['p95_ms']:6.1f}ms  "
            f"p99={result['p99_ms']:6.1f}ms  max={result['max_ms']:6.1f}ms  [{gate}]"
        )


if __name__ == "__main__":
    main()
