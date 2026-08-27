# Benchmark de modèles — GRAVIA

Résultats du benchmark prévu par [Architecture_GRAVIA.md §3](Architecture_GRAVIA.md) (« Benchmark :
régression logistique (baseline), Random Forest, LightGBM/XGBoost — modèle retenu selon les
métriques »), produit par [`ml/training/benchmark.py`](../ml/training/benchmark.py). XGBoost n'a
pas été ajouté à côté de LightGBM : le document cite les deux comme une alternative, pas comme
deux modèles à tester en plus l'un de l'autre, et LightGBM est déjà la dépendance figée du projet,
déjà validée dans les notebooks (cf. [notebooks/README.md](../notebooks/README.md)).

Protocole identique à la référence déjà publiée (cf. CLAUDE.md, référence baseline) : train
2019-2021, seuil de décision calibré sur validation 2022 (cible recall ≥ 0,80), évalué sur le
holdout 2023 — jamais vu, y compris pour le calibrage. Features : configuration `"baseline"`
(cf. `ml/features/gold_features.py`), 273 226 accidents.

## Résultats — configuration `"baseline"`

| Modèle | Recall grave | F1 macro | Seuil calibré | Seuils CDC (recall ≥ 0,80 / F1 ≥ 0,70) |
|---|---|---|---|---|
| Régression logistique | 0,804 | 0,695 | 0,44 | F1 macro sous le seuil |
| Random Forest | 0,801 | 0,690 | 0,38 | F1 macro sous le seuil |
| **LightGBM** | **0,807** | **0,707** | 0,44 | **Atteints** |

→ **LightGBM retenu**, seul modèle des trois à franchir les deux seuils CDC. Enregistré dans le
registry MLflow (`gravia-severity-classifier` v1 — les stages Staging/Production sont dépréciés
depuis MLflow 2.9, remplacés par les alias). Les deux autres restent trackés dans MLflow
(comparaison reproductible) mais ne sont pas enregistrés : promotion bloquée sous les seuils CDC
(cf. CLAUDE.md, seuils et métriques). **v1 n'est plus le modèle `@staging`** depuis la promotion
du LightGBM enriched (v2, cf. section suivante) — reste dans le registry comme historique/point de
comparaison, rechargeable explicitement via `models:/gravia-severity-classifier/1`.

## Résultats — configuration `"enriched"`

Flags véhicule/usager (`flag_2roues_motorise`/`flag_poids_lourd`/`flag_velo_edp`/`flag_pieton`),
meilleure configuration déjà repérée dans `notebooks/eval_enrichissement_vs_seuil.py` (config C :
recall 0,805 / F1 macro 0,727 avec un seuil unique). Même protocole, mêmes 3 modèles :

| Modèle | Recall grave | F1 macro | Seuil calibré | Seuils CDC |
|---|---|---|---|---|
| Régression logistique | 0,807 | 0,708 | 0,45 | Atteints |
| Random Forest | 0,811 | 0,710 | 0,39 | Atteints |
| **LightGBM** | 0,807 | **0,727** | 0,47 | **Atteints** |

→ Cette fois **les trois modèles franchissent les deux seuils CDC** (pas seulement LightGBM).
LightGBM enriched reproduit à 0,000/0,002 près la config C du notebook (recall 0,805/F1 0,727) —
même conclusion qu'à l'époque : l'enrichissement seul (sans calibration par département) apporte
un vrai gain de F1 macro (0,707 → 0,727) sans coût sur le recall. **Meilleur modèle du benchmark
toutes configurations confondues — promu à l'alias `staging`** (`gravia-severity-classifier` v2,
2026-08-27, décision explicite de l'utilisateur). Rechargé après promotion et revérifié :
`mlflow.lightgbm.load_model("models:/gravia-severity-classifier@staging")` prédit correctement.

## Latence de prédiction pure des 3 candidats

Le benchmark ci-dessus compare recall/F1, pas la vitesse — un modèle moins bon aurait pu rester
un choix pertinent s'il était nettement plus rapide. Mesuré séparément
([tests/performance/compare_model_latency.py](../tests/performance/compare_model_latency.py)),
une prédiction à la fois (ce que fait l'API à chaque requête), sur le holdout 2023 :

| Modèle | predict p50 | predict p95 | SHAP p50 | SHAP p95 |
|---|---:|---:|---:|---:|
| Régression logistique | 2,9-3,2 ms | 3,4-3,6 ms | n/a | n/a |
| Random Forest | 29,5-30,1 ms | 34-44 ms | n/a | n/a |
| **LightGBM (déployé)** | 3,9 ms | 5,1-5,7 ms | 4,4-4,6 ms | 6,3-7,3 ms |

→ **LightGBM n'est pas seulement le meilleur modèle du benchmark : c'est aussi l'un des plus
rapides**, quasiment à égalité avec la régression logistique et **~8× plus rapide que Random
Forest** (300 arbres parcourus en entier à chaque prédiction). Choisir LightGBM n'a donc sacrifié
aucune performance brute pour la justesse — les deux critères vont dans le même sens ici, pas de
compromis à arbitrer entre précision et vitesse.

SHAP non mesuré pour régression logistique/Random Forest : tous deux enveloppés dans un
`sklearn.Pipeline` (`OneHotEncoder` + classifieur) pour l'encodage catégoriel —
`shap.TreeExplainer` ne s'applique ni à un `Pipeline` tel quel (Random Forest) ni à un modèle
linéaire (régression logistique). `ml/serving` ne câble une explication SHAP que pour LightGBM
aujourd'hui, cohérent avec le seul modèle réellement déployé.

## Performance de l'API sous charge (pas seulement le modèle)

À distinguer de la latence du modèle ci-dessus : la latence de **l'API déployée** sous charge
concurrente réelle a d'abord été mesurée de façon trompeuse (test séquentiel, une requête à la
fois) avant d'être corrigée — cf. [AVANCEMENT_GRAVIA.md](AVANCEMENT_GRAVIA.md) et
[tests/performance/load_test_serving.py](../tests/performance/load_test_serving.py) pour le
détail (un seul worker uvicorn sérialisait les requêtes ; passage à 4 workers, débit ×3-4).

## Cohérence avec le baseline déjà publié

Le LightGBM retenu (recall 0,807 / F1 macro 0,707) reproduit à 0,001 près le baseline déjà validé
dans `notebooks/eda_baseline_baac.py` (recall 0,808 / F1 macro 0,708, même seuil calibré 0,44) —
confirme que Gold + `ml/features` reconstituent fidèlement ce qui avait été établi sur CSV brut.

## Deux problèmes réels trouvés en testant

1. **Collision de variables d'environnement** : `.env` définit déjà `AWS_ACCESS_KEY_ID`/
   `AWS_SECRET_ACCESS_KEY=test` pour LocalStack/Terraform, chargées par `gravia.config` au
   démarrage. MLflow/boto3 lisent les mêmes noms de variable pour l'artifact store S3 (MinIO),
   avec des identifiants différents (`minioadmin`). Un `os.environ.setdefault(...)` ne les
   remplaçait jamais — corrigé en les écrasant explicitement dans le process (n'affecte ni `.env`
   ni le shell appelant, cf. `ml/training/benchmark.py::_configure_s3_artifact_env`).
2. **Piège MLflow/LightGBM sur les catégorielles** : le chemin de service générique de MLflow
   (auto-validation `pyfunc` sur l'exemple d'entrée, scoring REST JSON) sérialise l'exemple en
   JSON puis le redéserialise, ce qui perd le dtype `category` pandas des colonnes catégorielles.
   LightGBM refuse alors de prédire (`ValueError: train and valid dataset categorical_feature do
   not match`). Le modèle lui-même n'est pas cassé : chargé nativement
   (`mlflow.lightgbm.load_model`) avec le dtype `category` explicitement remis sur les colonnes
   concernées (comme à l'entraînement), il prédit normalement — vérifié manuellement sur le modèle
   enregistré. **`ml/serving` devra charger le modèle de cette façon**, pas via le scoring REST
   générique de MLflow.

## Portée et limites

- Seuil de décision **national unique**, pas de calibration par sous-groupe (département) : l'angle
  mort de sécurité documenté en CDC §13.7/§14 (recall quasi nul sur Paris avec un seuil national,
  cf. `notebooks/eval_seuil_par_zone.py`) reste un point ouvert, pas traité par ce benchmark.
- ~~Le seuil calibré n'est pas encore persisté nulle part au-delà du run MLflow~~ — fait :
  `ml/serving` le récupère au démarrage depuis les métriques du run associé au modèle `@staging`
  (cf. `ml/serving/model.py::load_staged_model`), pas recalculé ni codé en dur.

## Relancer le benchmark

```bash
python -m ml.training.benchmark
```

Nécessite la stack dev démarrée (`docker compose -f infra/docker-compose.yml --env-file .env up
-d`) : PostgreSQL (Gold), MLflow (tracking), MinIO (artifacts des modèles).
