# CLAUDE.md — GRAVIA

## Contexte du projet

GRAVIA est un système MLOps d'**aide à la décision pour la priorisation des secours routiers** : à la remontée d'un signalement d'accident, le système prédit la **gravité probable** afin d'aider les opérateurs à prioriser et dimensionner les moyens. Projet développé pour l'organisation fictive **VigiRoute**, dans le cadre du titre RNCP **Architecte en Intelligence Artificielle**.

- **Dataset source :** [BAAC](https://www.data.gouv.fr/fr/datasets/bases-de-donnees-annuelles-des-accidents-corporels-de-la-circulation-routiere-annees-de-2005-a-2024/) — Bases de données annuelles des accidents corporels de la circulation (2005→2024). **Seule source réellement utilisée par le modèle à ce jour.**
- **Enrichissements — statuts à connaître avant d'en reproposer un :**
  - **Trafic** (DATEX II national + capteurs Paris) : exploré, testé en modèle, **écarté comme feature** (signal statistique réel mais gain prédictif nul). Le flux temps réel reste **ingéré** pour une valeur opérationnelle propre (routage des secours), pas pour le modèle.
  - **Bulletins d'incidents (texte)** : **retirés du périmètre**, aucune source réelle n'existe. Ne pas les réintroduire.
  - **Météo (Open-Meteo)** et **géo (BAN/OSM)** : déclarés dans le CDC, **jamais implémentés ni testés** à ce jour. Le BAAC contient déjà la météo (`atm`) et les caractéristiques de route (`catr`, `vma`, `nbv`).
- **Tâche IA :** Classification **binaire** tabulaire — `grave` / `non grave`
- **Cible :** `grave` = au moins une victime hospitalisée ou tuée (agrégée au niveau accident)
- **Architecture :** Medallion Bronze / Silver / Gold + MLOps complet

> ⚠️ **Pas de GPU dans ce projet.** La tâche est une classification tabulaire (benchmark de modèles — gradient boosting anticipé favori) — l'entraînement tourne en CPU. Ne pas générer de code CUDA/ROCm.

---

## ⚠️ Deux environnements — règle fondamentale

Toujours préciser l'environnement (dev/prod) avant de générer du code ou de la config.

### 🧪 Dev — local, gratuit

| Composant | Technologie dev |
|---|---|
| Stockage objet (Bronze/Silver) | **MinIO** (S3-compatible) |
| Base analytique (Gold) | **PostgreSQL** (Docker) |
| Traitement | **Polars** (en mémoire) |
| Orchestration | **Airflow** (Docker Compose) |
| Temps réel | **Redpanda** (Kafka-compatible) |
| Tracking ML | **MLflow** (Docker) |
| Qualité données | **Great Expectations** |
| Serving | **FastAPI** (uvicorn) |
| Cache | **Redis** (Docker) |
| Monitoring infra | **Prometheus + Grafana** |
| Monitoring modèle | **Evidently** |
| IaC | **Terraform** via **LocalStack** |
| CI/CD | **GitHub Actions** |

### 🚀 Prod — cible cloud (documentée + IaC)

| Composant | Technologie prod |
|---|---|
| Stockage | **AWS S3** |
| Base | **AWS RDS PostgreSQL** |
| Conteneurs / scaling | **Kubernetes (EKS)** |
| Orchestration | **Airflow sur EKS** |
| Temps réel | **Kafka managé (MSK)** |
| Serving | **FastAPI** sur ECS/EKS |
| Cache | **ElastiCache** |
| Secrets | **AWS Secrets Manager** |
| IaC | **Terraform → AWS** |

> Faute d'accès au free tier AWS, la prod est déployée **réellement via Terraform contre LocalStack** (émulation AWS gratuite) pour produire l'IaC + la vidéo « infra en production ». L'architecture cible AWS est documentée pour l'oral.

---

## Choix techniques structurants (à savoir défendre)

- **Polars, pas Spark** : le volume BAAC tient en mémoire (< 10 Go) → Spark serait de la sur-ingénierie. Spark reste la voie de montée en charge documentée. `duckdb` a été retiré des dépendances (`pyproject.toml`) : envisagé au cadrage, jamais importé nulle part dans le code — tout le traitement passe par Polars et SQL PostgreSQL.
- **Hybride lac + relationnel** : Parquet (Bronze/Silver) + PostgreSQL schéma en étoile (Gold).
- **LocalStack pour Terraform** : même code IaC que la cible AWS (bascule par endpoint/identifiants). Prouve que l'infrastructure est exécutable, **pas** une charge de production réelle.
- **Kubernetes en cible, pas en dev** : scaling et haute disponibilité en production, sans alourdir le développement.
- **Kafka justifié par le trafic, pas par le modèle** : le flux DATEX (milliers de mesures/min) a une valeur opérationnelle autonome (routage des secours). Sans lui, il ne resterait que les signalements (~150/jour), insuffisants pour justifier un bus de messages.
- **Toutes les versions sont figées** (dépendances Python en `==`, images Docker en tag précis, jamais `latest`) : la reproductibilité est une exigence du CDC. Relever une version impose de rejouer les notebooks pour vérifier que les chiffres tiennent.

---

## Organisation en deux dépôts

Le projet est réparti sur l'organisation GitHub **VigiRoute**, en deux dépôts distincts (exigence de certification) :

| Dépôt | Rôle | Contenu |
|---|---|---|
| **[VigiRoute/gravia](https://github.com/VigiRoute/gravia)** *(ce dépôt)* | Solution IA | Code de la solution, pipelines de données, docs, stack dev |
| **[VigiRoute/gravia-mlops](https://github.com/VigiRoute/gravia-mlops)** | CI/CD + déploiement | IaC (Terraform), manifests Kubernetes, workflows de déploiement |

Règle : `gravia` **construit** la solution, `gravia-mlops` la **déploie**.

## Structure du dépôt — gravia (ce dépôt)

```
gravia/
├── src/gravia/              # code source du package
├── ml/
│   ├── features/            # feature engineering (anti-leakage)
│   ├── training/            # entraînement & benchmark de modèles
│   ├── serving/             # FastAPI /v1/predict-severity
│   ├── monitoring/          # Evidently (dérive)
│   └── fairness/            # tests d'équité (EC-6 : parité âge/sexe, equalized odds)
├── pipelines/airflow/dags/  # DAGs du pipeline de données
├── data/
│   ├── expectations/        # Great Expectations
│   └── models/              # DDL Gold (schéma étoile)
├── infra/
│   └── docker-compose.yml   # stack DEV locale (MinIO, PostgreSQL, Airflow…)
├── tests/                   # unit + integration
├── notebooks/               # exploration
├── docs/                    # CDC, architecture, gouvernance, AIPD, présentation, ADR
└── .github/workflows/       # CI de la solution (tests, lint, build)
```

## Structure du dépôt — gravia-mlops

```
gravia-mlops/
├── terraform/               # IaC prod (LocalStack → AWS) : storage, network, compute, mlops
├── k8s/                     # manifests Kubernetes (EKS cible)
└── .github/workflows/       # CD : déploiement infra + solution, réentraînement planifié
```

---

## Conventions de code

### Python
- Version : **Python 3.12** (seule version testée ; dépendances figées dans `pyproject.toml`)
- Style : **PEP 8** — linting `ruff`, formatage `black`
- Type hints : **obligatoires** sur toutes les fonctions publiques
- Docstrings : format **Google style**
- Gestion des erreurs : exceptions explicites, jamais de `except Exception` silencieux

### SQL
- Nommage : `{couche}_{entité}` (ex : `gold_fact_accident`, `gold_dim_lieu`)
- Couche Gold = tables (schéma en étoile) ; index sur les clés de dimensions

### Terraform
- Un module par responsabilité (`storage`, `network`, `compute`, `mlops`)
- Variables typées et documentées ; pas de valeurs en dur
- Nommage : `gravia-{env}-{ressource}`

---

## Règles métier importantes

### Anti-leakage (CRITIQUE)
Le modèle ne doit utiliser **que les variables connues au moment du signalement** (date, lieu, météo, type de collision, véhicules impliqués). Les champs renseignés **après enquête** (nature précise des blessures, équipement de sécurité porté, manœuvre détaillée) sont **exclus** des features. Toute fuite de données rend le modèle inutilisable en production.

### Définition de la cible
- `grav` BAAC par usager : 1=indemne, 2=tué, 3=hospitalisé, 4=blessé léger.
- **Label accident** : `is_grave = 1` si au moins un usager a `grav ∈ {2, 3}` (tué ou hospitalisé), sinon `0`.

### ⚠️ Angle mort de sécurité du seuil unique (CRITIQUE)
Un seuil de décision **unique** calibré sur la distribution nationale donne un recall de **0,007 sur Paris** alors que le recall national est de 0,808 — le système raterait 99 % des accidents graves d'une zone entière tout en paraissant conforme. Cause : le taux de gravité de base varie fortement (~9 % à Paris contre ~36 % au national), donc le modèle y prédit des probabilités systématiquement plus basses.

**Conséquence de méthode : ne jamais valider ce modèle sur sa seule métrique agrégée nationale.** Toujours vérifier le recall **par sous-groupe** (département, urbain/rural).

Calibrer des seuils par zone répare le recall local mais dégrade le F1 macro global sous le seuil CDC (0,609 au mieux, en combinant seuils par département et flags véhicule). **Tension non résolue**, documentée comme risque à arbitrer avant production (CDC §13.7 et §14).

### Pièges de schéma BAAC (constatés, à gérer dans tout code d'ingestion)
- `Num_Acc` est renommé **`Accident_Id`** dans le fichier caractéristiques **2022 uniquement**.
- Les fichiers caractéristiques 2021 et 2022 sont nommés **`carcteristiques`** (sans le « a ») par le producteur lui-même.
- `jour` / `mois` sont zéro-paddés certaines années (`"05"`) et pas d'autres (`"5"`) → toujours caster en `Int64`.
- `grav`, `catv`, `catu` contiennent des valeurs `" -1"` → caster avec `strict=False`. **Attention** : la sentinelle est précédée d'une espace (`" -1"`, pas `"-1"`) sur la quasi-totalité des colonnes codées du BAAC (pas seulement ces trois-là : `lum`, `int`, `atm`, `col`, `circ`, `vosp`, `prof`, `plan`, `surf`, `infra`, `situ`, `sexe`, `trajet`, `locp`… constaté). `"strict=False"` seul ne suffit pas : `" -1".cast(Int8, strict=False)` renvoie `null`, pas `-1` — `str.strip_chars()` est nécessaire avant le cast, sans quoi une valeur explicitement codée « non renseigné » se confond silencieusement avec une valeur réellement absente.
- Les valeurs manquantes s'écrivent de **trois façons différentes** selon la variable : cellule vide, `0`, ou point `.` (pas seulement `-1`) — cf. dictionnaire ONISR, section « Documentation de référence » ci-dessous.
- L'indicateur « blessé hospitalisé » (`grav = 3`, utilisé dans `is_grave`) **n'est plus labellisé par la statistique publique depuis 2019** et n'est pas comparable avant/après 2018 (changement de process de saisie des forces de l'ordre). À garder en tête pour la fiabilité de la cible sur longue période.
- La colonne `id_usager` (rubrique usagers) est **absente des fichiers 2019 et 2020**, présente à partir de 2021 seulement (constaté sur les fichiers réels) — cohérent avec l'ajout des usagers en fuite documenté par l'ONISR à partir de cette année. Ne pas supposer sa présence sans vérifier le millésime.
- Les noms de colonnes ne respectent pas toujours la casse du dictionnaire ONISR : `an_nais` (rubrique usagers) est en minuscules dans les fichiers réels alors que le PDF l'écrit `An_nais`. Vérifier la casse réelle plutôt que de la recopier du PDF.
- Chaque fichier BAAC source contient une **ligne finale entièrement vide** (artefact d'export) : `Num_Acc` et toutes les autres colonnes valent `null` après lecture Bronze. Constatée sur 17 des 20 combinaisons table/millésime 2019-2023. Filtrée en Silver (`Num_Acc` non nul), pas en Bronze (fidélité à la source).
- `hrmn` (caractéristiques) est au format `"HH:MM"` — vérifié empiriquement sur 2019-2023, non documenté par le dictionnaire ONISR.
- Le champ `voie` (lieux) est du **texte libre très bruité** (`"AUTOROUTE A 63"`, `"Echangeur 16.1 (Rd Pt autoroute A1)"`) : toute extraction de numéro de route doit être conservatrice.
- Un accident a **plusieurs lignes** dans `lieux` → dédoublonner sur `Num_Acc`.

### Données personnelles et sensibles
- Âge, sexe, géolocalisation = données personnelles → **pseudonymisation dès la Silver**.
- Gravité = **donnée de santé** (art. 9 RGPD) → accès strict.
- Risque de ré-identification (lat/long + date + commune) → **agrégation géographique**.
- Le modèle assiste ; la décision reste humaine (**human-in-the-loop**, art. 22 RGPD).

---

## Seuils et métriques

| Métrique | Seuil | Action si dépassé |
|---|---|---|
| Recall classe `grave` | ≥ 0,80 | Revoir features / rééquilibrage |
| F1-score macro | ≥ 0,70 | Bloquer la promotion en production |
| Latence API p95 | < 300 ms | Optimisation serving |
| Disponibilité API | ≥ 99,5 % / mois | Incident |
| PSI (dérive) | < 0,2 | Déclencher réentraînement |
| Couverture de tests | ≥ 80 % | Bloquer la PR |

### Référence baseline (à battre)
Le baseline **BAAC seul, sans aucun enrichissement** atteint déjà les deux seuils au niveau national :

| | Valeur | Protocole |
|---|---|---|
| Recall classe `grave` | **0,808** | train 2019-2021 / seuil calibré sur validation 2022 / test holdout 2023 |
| F1 macro | **0,708** | idem, 273 226 accidents, seuil de décision 0,44 |

Toute nouvelle feature doit être comparée à ces chiffres **sur ce même protocole** pour juger de son apport réel. C'est ainsi que le trafic a été écarté. Reproductible via `notebooks/eda_baseline_baac.py` (voir [notebooks/README.md](notebooks/README.md) pour l'index complet des explorations).

> Les seuils sont atteints au niveau agrégé national — mais voir l'**angle mort du seuil unique** ci-dessus avant de considérer le modèle comme validé.

---

## Commandes utiles

```bash
make dev              # Démarrer la stack dev (Docker Compose)
make down             # Arrêter la stack
make ps               # État des services
make logs S=<service> # Logs d'un service
make test             # Tous les tests
make lint             # Linting (ruff)
make format           # Formatage (black)
make validate-data    # Great Expectations
```

> `make` **n'est pas installé** sur le poste de développement Windows. Équivalent direct :
> `docker compose -f infra/docker-compose.yml --env-file .env up -d`
>
> Le Terraform vit dans `gravia-mlops`, pas ici : il n'y a donc **pas** de cible `tf-localstack` dans ce Makefile.

## Workflow Git

⚠️ **Ne jamais merger une branche depuis le terminal** (pas de `git merge`, `git checkout main && git merge ...`, ni de merge via `gh pr merge`). L'utilisateur veut merger lui-même depuis l'interface GitHub (créer/merger la PR sur github.com). Créer la PR (`gh pr create`) reste possible sur demande, mais le merge proprement dit est une action manuelle de l'utilisateur sur le site.

✅ **Toute nouvelle branche créée doit être immédiatement poussée sur `origin`** (`git push -u origin <branche>` dès la création, pas seulement au moment d'ouvrir la PR), pour qu'elle soit visible sur GitHub sans étape manuelle supplémentaire.

## Environnement de développement — pièges connus

- **Console Windows en cp1252** : tout script qui affiche des caractères non-ASCII (tableaux Polars, emojis MLflow) plante avec `UnicodeEncodeError`. Ajouter `sys.stdout.reconfigure(encoding="utf-8")` en tête de script, ou lancer avec `PYTHONIOENCODING=utf-8`.
- **Fins de ligne** : `.gitattributes` force LF sur les scripts, YAML et Dockerfile. Un `.sh` en CRLF monté dans un conteneur Linux échoue avec `bad interpreter`.
- **Projet Docker Compose nommé `gravia`** (directive `name:`) : sans lui, Compose déduirait le nom du dossier (`infra`), générique au point d'entrer en collision avec les volumes d'autres projets.
- **Versions de la stack** : **Airflow 3.x** (l'API des DAGs diffère de la 2.x, `api-server` remplace `webserver`) et **Great Expectations 1.x** (réécriture complète de l'API par rapport à 0.18 — le matériel 0.18 est inapplicable).
- **Archives trafic Paris** compressées en **Deflate64** : le module `zipfile` de Python ne sait pas les lire, extraire avec `unzip` (Info-ZIP).

---

## Variables d'environnement

Ne jamais committer de secrets. En dev, utiliser `.env` (ignoré par `.gitignore`). Voir `.env.example`.

---

## Documentation de référence

- [Avancement du projet](docs/AVANCEMENT_GRAVIA.md) — **à consulter en premier** pour reprendre le contexte dans un nouveau chat (où on en est, ce qui est fait/pas fait, prochaine étape)
- [Cahier des charges](docs/CDC_GRAVIA.md)
- [Architecture de données](docs/Architecture_GRAVIA.md)
- [Plan de gouvernance](docs/Gouvernance_GRAVIA.md)
- [AIPD](docs/AIPD_GRAVIA.md)
- [Référentiel RNCP](docs/referentiel.md)
- [Dataset BAAC](https://www.data.gouv.fr/fr/datasets/bases-de-donnees-annuelles-des-accidents-corporels-de-la-circulation-routiere-annees-de-2005-a-2024/)
- [Description des bases de données BAAC (ONISR)](https://www.onisr.securite-routiere.gouv.fr/sites/default/files/2025-10/Description%20des%20bases%20de%20donn%C3%A9es%20annuelles.pdf) — dictionnaire officiel des 4 tables et de leurs variables/codes, source de vérité pour tout choix de typage en Silver/Gold (à consulter avant de deviner un code)
