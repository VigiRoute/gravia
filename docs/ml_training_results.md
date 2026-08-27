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

## Résultats

| Modèle | Recall grave | F1 macro | Seuil calibré | Seuils CDC (recall ≥ 0,80 / F1 ≥ 0,70) |
|---|---|---|---|---|
| Régression logistique | 0,804 | 0,695 | 0,44 | F1 macro sous le seuil |
| Random Forest | 0,801 | 0,690 | 0,38 | F1 macro sous le seuil |
| **LightGBM** | **0,807** | **0,707** | 0,44 | **Atteints** |

→ **LightGBM retenu**, seul modèle des trois à franchir les deux seuils CDC. Enregistré dans le
registry MLflow (`gravia-severity-classifier`, alias `staging` — les stages Staging/Production
sont dépréciés depuis MLflow 2.9, remplacés par les alias). Les deux autres restent trackés dans
MLflow (comparaison reproductible) mais ne sont pas enregistrés : promotion bloquée sous les
seuils CDC (cf. CLAUDE.md, seuils et métriques).

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

- Seule la configuration `"baseline"` a été benchmarkée ici. `"enriched"` (+ flags véhicule/usager
  de `notebooks/eval_enrichissement_vs_seuil.py`) est disponible dans `ml/training/benchmark.py`
  (paramètre `feature_set`) mais n'a pas encore été comparée sur ces 3 modèles.
- Seuil de décision **national unique**, pas de calibration par sous-groupe (département) : l'angle
  mort de sécurité documenté en CDC §13.7/§14 (recall quasi nul sur Paris avec un seuil national,
  cf. `notebooks/eval_seuil_par_zone.py`) reste un point ouvert, pas traité par ce benchmark.
- Le seuil calibré (0,44 pour ce run) n'est pas encore persisté nulle part au-delà du run MLflow —
  à récupérer par `ml/serving` depuis les métriques du run associé au modèle `@staging`.

## Relancer le benchmark

```bash
python -m ml.training.benchmark
```

Nécessite la stack dev démarrée (`docker compose -f infra/docker-compose.yml --env-file .env up
-d`) : PostgreSQL (Gold), MLflow (tracking), MinIO (artifacts des modèles).
