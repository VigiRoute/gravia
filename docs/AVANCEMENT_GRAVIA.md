# Avancement du projet GRAVIA

> **But de ce fichier :** permettre de reprendre le projet dans un nouveau chat sans perdre le
> contexte. À mettre à jour à chaque jalon (fin de couche, décision structurante, changement de
> cap) — pas à chaque commit. Le détail du *pourquoi* de chaque décision reste dans
> [CLAUDE.md](../CLAUDE.md) et les docs référencées ; ce fichier ne fait que pointer dessus et
> dire *où on en est*.

**Dernière mise à jour :** 2026-08-27 — `ml/training/benchmark.py` implémenté : benchmark
LightGBM/régression logistique/Random Forest tracké MLflow, meilleur modèle enregistré (registry).

## En une phrase

Le cadrage, l'EDA et les décisions d'architecture sont actés ; le pipeline de données
**Bronze → Silver → Gold est complet, testé (82 % de couverture) et orchestré par Airflow**,
chargé en base réelle (273 226 accidents, 2019-2023) ; **`ml/features` et `ml/training` en
place** — un premier modèle (LightGBM) est entraîné, évalué et enregistré dans le registry
MLflow ; serving et monitoring pas encore commencés.

## État par composant

### ✅ Fait

- **Cadrage & gouvernance** — CDC, architecture de données, plan de gouvernance, AIPD et
  présentation Jedha rédigés ([docs/](.)).
- **EDA & décisions produit** (voir [notebooks/README.md](../notebooks/README.md) pour le détail) :
  - Baseline BAAC seul validée : recall 0,808 / F1 macro 0,708 (holdout 2023) — les deux seuils
    CDC sont atteints sans aucun enrichissement.
  - Enrichissement trafic (DATEX national + capteurs Paris) testé en modèle et **écarté** :
    signal statistiquement réel mais gain prédictif nul. Le flux temps réel reste ingéré pour le
    routage des secours, pas pour le modèle.
  - Bulletins d'incidents texte **retirés du périmètre** (aucune source réelle).
  - Angle mort du seuil unique documenté : recall 0,007 sur Paris vs 0,808 national. Seuils par
    département : recall Paris 0,777 mais F1 macro national tombe à 0,573 (sous seuil CDC).
    Meilleure config trouvée (seuils + features enrichies) : recall Paris 0,812 / F1 macro 0,609
    — **tension atténuée, pas résolue**. Point ouvert à trancher avant mise en production
    (CDC §13.7, §14).
- **Infra dev** — stack Docker Compose (MinIO, PostgreSQL, Airflow, MLflow, Redis, Redpanda…),
  projet nommé explicitement `gravia`, dépendances Python figées, cible Python 3.12.
- **Pipeline de données — couche Bronze** ([src/gravia/bronze.py](../src/gravia/bronze.py)) —
  ingestion brute des 4 tables BAAC (caracteristiques, lieux, vehicules, usagers) pour
  2019-2023, tout en `Utf8` sans coercion, écriture Parquet local + upload MinIO, idempotente.
  Gère déjà les pièges de schéma connus (renommage `Accident_Id` en 2022, coquille
  `carcteristiques` 2021/2022). Colonnes de provenance (`_millesime`, `_source_file`,
  `_ingested_at`) pour la traçabilité gouvernance.
- **Décision d'architecture Silver/Gold** — Silver = une table Parquet par entité BAAC
  (caractéristiques/lieux/véhicules/usagers), nettoyée/typée/dédupliquée/pseudonymisée
  **indépendamment, sans jointure inter-table**. Gold fait la jointure (`Num_Acc`, `id_vehicule`)
  et construit `is_grave` + le schéma en étoile. Doc mise à jour en conséquence
  ([Architecture_GRAVIA.md §5](Architecture_GRAVIA.md)) ; la mention d'enrichissement météo/géo en
  Silver a été retirée de ce tableau (jamais implémenté, cf. CLAUDE.md).
- **Dictionnaire officiel BAAC identifié** — [Description des bases de données annuelles
  (ONISR, 21/10/2025)](https://www.onisr.securite-routiere.gouv.fr/sites/default/files/2025-10/Description%20des%20bases%20de%20donn%C3%A9es%20annuelles.pdf),
  référencé dans CLAUDE.md, à vérifier systématiquement pour tout choix de typage Silver/Gold.
  Deux points à retenir : valeurs manquantes sur 3 formats (vide/`0`/`.`, pas seulement `-1`) ;
  l'indicateur « blessé hospitalisé » (`grav=3`, utilisé dans `is_grave`) n'est plus labellisé
  par la statistique publique depuis 2019 et non comparable avant/après 2018.
- **Pipeline de données — couche Silver** ([src/gravia/silver.py](../src/gravia/silver.py)) —
  nettoie les 4 tables Bronze indépendamment (pas de jointure), typage strict vérifié contre le
  dictionnaire ONISR, dédoublonnage `lieux` sur `Num_Acc`. Pseudonymisation : `lat`/`long`
  supprimées (localisation restant disponible via `dep`/`com`, choix documenté car un simple
  arrondi de coordonnées ne garantit pas la non-individualisation RGPD dans les zones peu
  denses) ; `an_nais` remplacé par `tranche_age` (6 tranches), calculé via `_millesime` sans
  jointure vers `caracteristiques`. Testé sur les 5 millésimes réels 2019-2023 (`--no-upload`) :
  taux de nulls faibles et cohérents, codes `catv`/`grav`/`catu` conformes au dictionnaire.
  `is_grave` **n'est pas construit ici**, reporté à Gold.
- **Pipeline de données — couche Gold** ([src/gravia/gold.py](../src/gravia/gold.py), DDL dans
  [data/models/gold_schema.sql](../data/models/gold_schema.sql)) — implémente fidèlement le
  schéma en étoile de [Architecture_GRAVIA.md §4.2](Architecture_GRAVIA.md) (`gold_fact_accident`,
  `gold_dim_date`, `gold_dim_lieu`, `gold_dim_conditions`, `gold_dim_collision`), avec 3 écarts
  assumés et documentés dans le module : `departement` en `VARCHAR` (codes Corse `2A`/`2B` non
  numériques) ; `type_collision` décodé en libellé texte ; les flags véhicule/usager reprennent
  la configuration **réellement validée** dans `notebooks/eval_enrichissement_vs_seuil.py`
  (`flag_2roues_motorise`/`flag_poids_lourd`/`flag_velo_edp`/`flag_pieton`, codes `catv` repris à
  l'identique) plutôt que le `flag_moto` jamais testé du schéma d'origine. `jour_ferie` est un
  enrichissement neuf (jours fériés France métropolitaine, `dateutil.easter`, dépendance figée
  ajoutée), non validé en amont contrairement au reste. Chargé et vérifié sur les 5 millésimes
  réels dans le PostgreSQL du Docker Compose dev : **273 226 accidents** (identique au chiffre du
  protocole baseline CLAUDE.md), taux `is_grave` **35,76 % national pondéré sur 2019-2023**
  (34,60 % à 36,09 % selon le millésime — cohérent avec le ~36 % déjà documenté ; la valeur
  36,09 % citée dans une version précédente de ce fichier était en réalité le taux 2023 seul, pas
  la moyenne pondérée des 5 ans), zéro clé étrangère orpheline, rechargement testé idempotent.
  **Bug trouvé et corrigé en testant Gold** : chaque fichier BAAC source contient une ligne
  finale entièrement vide (artefact d'export) que Silver ne filtrait pas encore — corrigé dans
  `silver.py` (`Num_Acc` non nul), Silver régénéré, Bronze inchangé (fidélité à la source).
  Documenté dans CLAUDE.md, pièges de schéma BAAC.
  **Écart structurel trouvé en préparant `ml/features`, corrigé** : `gold_dim_lieu` ne portait
  que 4 attributs (`departement`/`agglomeration`/`categorie_route`/`vitesse_max`) — 8 attributs
  utilisés par le baseline déjà validé manquaient (`circ`, `nbv`, `vosp`, `prof`, `plan`, `infra`,
  `situ` côté lieu ; `int`/intersection côté caracteristiques), rendant Gold structurellement
  incapable de le reproduire. Étendu à 12 attributs dans `gold_dim_lieu` (DDL + `gold.py` +
  [Architecture_GRAVIA.md §4.2](Architecture_GRAVIA.md)), schéma recréé et rechargé sur les 5
  millésimes réels : mêmes comptes qu'avant (273 226 accidents, 35,76 % `is_grave` — la
  correction ajoute des features, ne change pas le label).
  **Deuxième bug trouvé au passage** : `create_schema` découpait le DDL sur `;`, mais les
  commentaires SQL du fichier contiennent eux-mêmes des `;` (ex. `-- -1 = non renseigné ;
  lieux.circ`) — coupait en plein milieu d'une instruction. Corrigé pour découper sur `;` suivi
  d'une fin de ligne, seul un vrai terminateur d'instruction dans ce fichier.
- **Suite de tests** (`tests/unit/test_bronze.py`, `test_silver.py`, `test_gold.py`,
  `tests/integration/test_gold_postgres.py`) — 26 tests, **90 % de couverture** (seuil CLAUDE.md :
  80 %). Les tests unitaires n'ont besoin d'aucune infra (fichiers temporaires uniquement) ; le
  test d'intégration Gold est ignoré (`pytest.skip`) si PostgreSQL n'est pas joignable, et nettoie
  ses propres données de test (millésime factice 1900) pour ne pas polluer la base dev partagée.
  **Bug trouvé en écrivant les tests, corrigé** : `cast_columns` castait `" -1"` (sentinelle BAAC
  avec espace de tête, présente sur la quasi-totalité des colonnes codées, pas seulement
  `grav`/`catv`/`catu`) en `null` plutôt qu'en `-1` — `str.strip_chars()` manquant avant le cast.
  Silver et Gold régénérés après correction ; les totaux `is_grave` sont restés identiques (la
  distinction -1/null n'affectait pas ce calcul précis), mais la distinction « valeur explicitement
  codée non-renseigné » vs « valeur réellement absente » est désormais correcte pour tout usage
  futur (Great Expectations, ml/features). Documenté dans CLAUDE.md.
- **Orchestration Airflow** ([pipelines/airflow/dags/etl_medallion_dag.py](../pipelines/airflow/dags/etl_medallion_dag.py))
  — un groupe de tâches par millésime (`bronze` → `silver` → `gold`), TaskFlow API Airflow 3
  (`airflow.sdk`), déclenchement manuel (`schedule=None`, millésimes publiés annuellement).
  A nécessité une image Airflow custom ([infra/airflow/Dockerfile](../infra/airflow/Dockerfile)) :
  l'image officielle `apache/airflow:3.3.0` tourne en Python 3.13, incompatible avec
  `requires-python = ">=3.12,<3.13"` — bascule sur le tag `3.3.0-python3.12`. Seul un
  sous-ensemble des dépendances du projet y est installé (`polars`, `pyarrow`, `sqlalchemy`,
  `psycopg2-binary`, `boto3`, `python-dotenv`, `python-dateutil`) : installer la liste complète de
  `pyproject.toml` (testé, puis abandonné) écrase les versions internes d'Airflow lui-même
  (`fastapi<0.137` requis par son API server vs `fastapi==0.140.0` du projet). Le code applicatif
  n'est pas copié dans l'image : il reste monté en volume (`PYTHONPATH=/opt/airflow/src`) pour que
  les DAGs voient les modifications locales sans reconstruire l'image. `.dockerignore` ajouté au
  passage — le contexte de build sans lui aurait embarqué `data/` (34 Go) et `.venv/` (1,5 Go).
  **Vérifié** : `airflow dags test etl_medallion_baac` exécute les 15 tâches (5 millésimes × 3
  couches) avec succès en ~82 s ; les données rechargées restent identiques (273 226 accidents,
  35,76 % `is_grave`) — l'orchestration ne modifie pas les résultats déjà validés en CLI manuel.
- **Feature engineering ML** ([ml/features/gold_features.py](../ml/features/gold_features.py))
  — charge le schéma en étoile Gold déjà joint depuis PostgreSQL, type pour un modèle
  (catégorielles en `Categorical`, numériques en `Float64`), split temporel anti-leakage train
  2019-2021 / validation 2022 / test 2023 (codé en dur, dévier casserait la comparabilité avec
  les chiffres de référence). Trois groupes de colonnes distincts plutôt que tout mélanger :
  `BASELINE_*` (protocole d'origine, renommé aux noms Gold), `ENRICHED_FLAG_COLUMNS` (meilleure
  config déjà testée), `UNVALIDATED_*` (`weekend`/`jour_ferie`/`nb_usagers`, disponibles dans
  Gold mais jamais testés dans aucun notebook — à comparer au protocole avant adoption, pas à
  utiliser d'office). **Vérifié en reproduisant le protocole complet** (LightGBM + seuil calibré
  sur validation, cf. `notebooks/eda_baseline_baac.py`) sur la sortie de ce module : recall 0,807
  / F1 macro 0,707 contre 0,808/0,708 publiés — reproduction à 0,001 près, seuil calibré identique
  (0,44). **Piège Polars réel trouvé en testant** : caster un entier directement en `Categorical`
  traite sa valeur comme un code de catégorie interne (doit être positif) et non comme un
  libellé — `-1` (sentinelle omniprésente dans ces colonnes) fait échouer le cast. Corrigé en
  passant par `Utf8` d'abord. `ml/` ajouté à la couverture de tests suivie (`pyproject.toml`).

- **Entraînement / benchmark de modèles** ([ml/training/benchmark.py](../ml/training/benchmark.py),
  résultats détaillés dans [docs/ml_training_results.md](ml_training_results.md)) — benchmark 3
  familles de modèles comme prévu par
  [Architecture_GRAVIA.md §3](Architecture_GRAVIA.md) (régression logistique, Random Forest,
  LightGBM ; XGBoost non ajouté — le document cite « LightGBM/XGBoost » comme alternative, pas
  les deux), chacune trackée comme un run MLflow (params, métriques, modèle). Sélection du
  meilleur modèle : priorité au recall (coût asymétrique, cf. CDC), F1 macro en second critère.
  Seul un modèle franchissant les deux seuils CDC (recall ≥ 0,80, F1 macro ≥ 0,70) est enregistré
  dans le registry MLflow, à l'alias `staging` (les stages Staging/Prod sont dépréciés depuis
  MLflow 2.9, remplacés par des alias — traduction de « registry Staging/Prod » de
  l'architecture). **Vérifié en conditions réelles** (MLflow + PostgreSQL du Docker Compose dev,
  273 226 accidents) : logistic_regression recall=0,804/F1=0,695 (sous seuil), random_forest
  recall=0,801/F1=0,690 (sous seuil), **lightgbm recall=0,807/F1=0,707 (OK)** — cohérent avec la
  vérification manuelle précédente. LightGBM v1 enregistré et rechargé avec succès
  (`mlflow.lightgbm.load_model`), prédictions vérifiées correctes.
  **Deux problèmes réels trouvés en testant, corrigés :**
  - `.env` définit déjà `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY=test` pour LocalStack/Terraform
    (chargées par `gravia.config` au démarrage) — MLflow/boto3 lisent les mêmes noms de variable
    pour l'artifact store S3 (MinIO), avec des identifiants différents. Un `setdefault` ne les
    remplaçait jamais ; corrigé en les écrasant explicitement dans le process (n'affecte ni
    `.env` ni le shell appelant).
  - Le chemin de service générique de MLflow (auto-validation `pyfunc`, scoring REST JSON) perd
    le dtype `category` pandas des colonnes catégorielles au round-trip JSON, ce que LightGBM
    refuse ensuite (`categorical_feature do not match`). Le modèle n'est pas cassé — chargé
    nativement avec le dtype `category` remis explicitement, il prédit normalement (vérifié). **À
    retenir pour `ml/serving`** : charger via `mlflow.lightgbm.load_model`, pas via le scoring
    REST générique.
  30 tests unitaires + 10 tests d'intégration (dont un contre MLflow réel sur données
  synthétiques, ~12s, isolé du registry réel via des noms de test supprimés en sortie) : 82 % de
  couverture globale (seuil CLAUDE.md : 80 %).
  **Config `enriched` testée aussi** (2026-08-27, cf. [ml_training_results.md](ml_training_results.md)) :
  cette fois les **3 modèles** franchissent les seuils CDC ; LightGBM enriched (F1 macro 0,727)
  bat le LightGBM baseline (0,707), reproduit la config C de
  `notebooks/eval_enrichissement_vs_seuil.py` à 0,002 près. **Promu à l'alias `staging`**
  (`gravia-severity-classifier` v2, décision explicite de l'utilisateur) — rechargé et revérifié
  après promotion (`mlflow.lightgbm.load_model`, prédictions correctes). v1 (baseline) reste dans
  le registry comme historique, accessible via `models:/gravia-severity-classifier/1`.

### 🚧 Pas commencé

- **Serving FastAPI** `/v1/predict-severity` (`ml/serving/` — vide). À charger via
  `mlflow.lightgbm.load_model`, pas via le scoring REST générique MLflow (cf. ci-dessus).
- **Monitoring de dérive Evidently** (`ml/monitoring/` — vide).
- **Great Expectations** (`data/expectations/` à vérifier/peupler).
- **Dépôt `gravia-mlops`** (Terraform/LocalStack, manifests K8s, CD) — non entamé à ce stade du
  suivi.

## Pistes à évaluer plus tard

- **Étendre le nombre de millésimes d'entraînement.** 2024 est publié sur data.gouv.fr (mêmes
  noms de fichiers que 2023, déjà gérés par `bronze.py`), donc faisable techniquement. Mais avant
  de s'y lancer, à trancher : (1) **combien d'années** utiliser pour le train sans dégrader la
  pertinence du signal (le BAAC change de convention chaque année — cf. CLAUDE.md, pièges de
  schéma — donc « plus » n'est pas gratuit : chaque nouveau millésime ajouté doit être vérifié
  comme les précédents) ; (2) **si c'est réellement utile** — le baseline atteint déjà les deux
  seuils CDC avec 5 ans (273 226 accidents), donc établir d'abord si le facteur limitant actuel
  est la quantité de données ou autre chose (features, angle mort du seuil unique) avant d'investir
  dans l'ingestion. Décalé au train (2019-2022) ou ajouté au holdout changerait aussi le protocole
  de référence déjà cité partout (CLAUDE.md, ce fichier, `ml_training_results.md`) — à documenter
  explicitement plutôt qu'à faire glisser silencieusement.

## Prochaine étape probable

**Serving FastAPI** (`ml/serving/`) au-dessus du modèle enregistré dans le registry MLflow
(`gravia-severity-classifier@staging`, LightGBM enriched v2) : endpoint `/v1/predict-severity`
(cf. CDC), charger le modèle nativement (pas via le scoring REST générique MLflow, cf. piège
trouvé ci-dessus), appliquer le seuil calibré (0,47 — à persister quelque part plutôt qu'à
recalculer). Alternative possible : Great Expectations (`data/expectations/`), toujours vide. Une
branche par sujet (cf. CLAUDE.md, Workflow Git).

## Comment relancer le contexte dans un nouveau chat

1. Ce fichier donne le *où on en est*.
2. [CLAUDE.md](../CLAUDE.md) donne les règles, conventions et pièges à ne pas re-découvrir.
3. `git log --oneline -20` donne l'activité récente réelle (source de vérité si ce fichier a
   pris du retard).
