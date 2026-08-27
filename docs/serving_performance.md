# Performance du serving — GRAVIA

`docs/ml_training_results.md` compare les modèles sur recall/F1 (la justesse). Ce document
répond à une question différente, posée après coup : **a-t-on vérifié la performance pure**, pas
seulement la précision ? Réponse courte : pas au début — un premier test était trompeur — mais
oui maintenant, sur deux angles distincts.

## 1. Capacité de l'API sous charge concurrente

### Le problème : un test séquentiel donne une fausse impression

Un premier test (une requête à la fois, via `TestClient`, en process) affichait **p95 = 16 ms** —
largement sous la cible CDC (ENF-1 : p95 < 300 ms). Rassurant, mais trompeur : un test séquentiel
ne dit rien de ce qui se passe quand plusieurs opérateurs signalent un accident en même temps.

### Le vrai test : requêtes HTTP concurrentes

[`tests/performance/load_test_serving.py`](../tests/performance/load_test_serving.py) envoie de
vraies requêtes HTTP en parallèle (pas un aller-retour en process) contre le conteneur `serving`
réel, à plusieurs paliers de concurrence.

**Avant correctif** (1 seul worker uvicorn — configuration d'origine) :

| Concurrence | Débit | p95 |
|---:|---:|---:|
| 1 | 57 req/s | 24 ms |
| 10 | 65 req/s | 188 ms |
| 25 | 65 req/s | **424 ms — sous le seuil CDC** |
| 50 | 65 req/s | 803 ms |

Le débit plafonne à ~65 req/s **quelle que soit la concurrence** : signature classique d'un
serveur qui sérialise tout (un seul worker uvicorn, requêtes traitées une par une derrière le
GIL Python).

### Le correctif : 4 workers uvicorn

[`infra/serving/Dockerfile`](../infra/serving/Dockerfile) : `--workers 4` (processus séparés,
plus de sérialisation par un seul GIL partagé).

**Après correctif** :

| Concurrence | Débit | p95 |
|---:|---:|---:|
| 1 | 62 req/s | 23 ms |
| 10 | 188 req/s | 101 ms |
| 25 | 217 req/s | 171 ms |
| 50 | 227 req/s | **301 ms — à la limite** |

Débit ×3-4, et p95 repasse sous 300 ms jusqu'à ~25-40 requêtes simultanées. À 50 requêtes
simultanées c'est à la limite (301 ms, max observé 416 ms) — capacité réelle du conteneur dev
actuel, pas dimensionné pour une charge de production (la cible reste EKS + autoscaling, cf.
[Architecture_GRAVIA.md §6.2](Architecture_GRAVIA.md)).

## 2. Latence de prédiction pure, par modèle

Question distincte : le benchmark de `ml/training` compare recall/F1, pas la vitesse — un modèle
moins bon aurait pu rester pertinent s'il était nettement plus rapide.
[`tests/performance/compare_model_latency.py`](../tests/performance/compare_model_latency.py)
mesure le coût de calcul du modèle seul (sans HTTP, une prédiction à la fois, comme le fait
l'API à chaque requête) pour les 3 candidats du benchmark (config `"enriched"`) :

| Modèle | predict p50 | predict p95 | Recall / F1 macro |
|---|---:|---:|---|
| Régression logistique | ~3 ms | ~3,5 ms | 0,807 / 0,708 |
| Random Forest | **~30 ms** | **~34-44 ms** | 0,811 / 0,710 |
| **LightGBM (déployé)** | ~4 ms | ~5-6 ms | 0,807 / **0,727** |

**LightGBM n'est pas seulement le meilleur modèle du benchmark : c'est aussi l'un des plus
rapides**, quasiment à égalité avec la régression logistique et **~8× plus rapide que Random
Forest** (300 arbres parcourus en entier à chaque prédiction). Aucun compromis précision/vitesse
à arbitrer : les deux critères pointaient déjà vers le même choix.

SHAP (l'explication de chaque prédiction, cf. `ml/serving`) n'est mesuré que pour LightGBM :
régression logistique et Random Forest sont enveloppés dans un `sklearn.Pipeline`
(`OneHotEncoder` + classifieur) pour l'encodage catégoriel, et `shap.TreeExplainer` ne s'applique
ni à un `Pipeline` tel quel ni à un modèle linéaire — cohérent avec le fait que seul LightGBM est
réellement déployé.

## Reproduire ces vérifications

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d serving
python -m tests.performance.load_test_serving       # capacité sous charge
python -m tests.performance.compare_model_latency    # latence pure par modèle
```

Ni l'un ni l'autre n'est un test pytest (noms hors du motif `test_*.py`) : ce sont des scripts à
lancer à la main contre la stack dev démarrée, trop lents et dépendants de la machine hôte pour
tourner en CI à chaque commit.
