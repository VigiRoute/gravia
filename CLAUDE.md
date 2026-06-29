# CLAUDE.md — GRAVIA

## Contexte du projet

GRAVIA est un système MLOps d'**aide à la décision pour la priorisation des secours routiers** : à la remontée d'un signalement d'accident, le système prédit la **gravité probable** afin d'aider les opérateurs à prioriser et dimensionner les moyens. Projet développé pour l'organisation fictive **VigiRoute**, dans le cadre du titre RNCP **Architecte en Intelligence Artificielle**.

- **Dataset source :** [BAAC](https://www.data.gouv.fr/fr/datasets/bases-de-donnees-annuelles-des-accidents-corporels-de-la-circulation-routiere-annees-de-2005-a-2024/) — Bases de données annuelles des accidents corporels de la circulation (2005→2024)
- **Tâche IA :** Classification **binaire** tabulaire — `grave` / `non grave`
- **Cible :** `grave` = au moins une victime hospitalisée ou tuée (agrégée au niveau accident)
- **Architecture :** Medallion Bronze / Silver / Gold + MLOps complet

> ⚠️ **Pas de GPU dans ce projet.** La tâche est une classification tabulaire (LightGBM) — l'entraînement tourne en CPU. Ne pas générer de code CUDA/ROCm.

---

## ⚠️ Deux environnements — règle fondamentale

Toujours préciser l'environnement (dev/prod) avant de générer du code ou de la config.

### 🧪 Dev — local, gratuit

| Composant | Technologie dev |
|---|---|
| Stockage objet (Bronze/Silver) | **MinIO** (S3-compatible) |
| Base analytique (Gold) | **PostgreSQL** (Docker) |
| Traitement | **Polars / DuckDB** (en mémoire) |
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

- **Polars/DuckDB, pas Spark** : le volume BAAC tient en mémoire (< 10 Go) → Spark serait de la sur-ingénierie. Spark reste la voie de montée en charge documentée.
- **Hybride lac + relationnel** : Parquet (Bronze/Silver) + PostgreSQL schéma en étoile (Gold).
- **LocalStack pour Terraform** : IaC réelle et gratuite sans AWS payant.
- **Kubernetes en cible, pas en dev** : couvre la compétence cluster (C2.6) sans alourdir le dev.

---

## Structure du dépôt

```
gravia/
├── infra/
│   ├── docker-compose.yml   # stack dev
│   └── terraform/           # IaC (LocalStack → AWS)
├── pipelines/
│   └── airflow/dags/        # DAGs d'orchestration
├── data/
│   ├── expectations/        # Great Expectations
│   └── models/              # DDL Gold (schéma étoile)
├── ml/
│   ├── features/            # feature engineering (anti-leakage)
│   ├── training/            # entraînement LightGBM
│   ├── serving/             # FastAPI /v1/predict-severity
│   └── monitoring/          # Evidently (dérive)
├── src/gravia/              # code source du package
├── tests/                   # unit + integration
├── notebooks/               # exploration
├── docs/                    # CDC, architecture, gouvernance, AIPD, ADR
└── .github/workflows/       # CI/CD
```

---

## Conventions de code

### Python
- Version : **Python 3.10+**
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
| Latence API p95 | < 500 ms | Optimisation serving |
| Disponibilité API | ≥ 99,5 % / mois | Incident |
| PSI (dérive) | < 0,2 | Déclencher réentraînement |
| Couverture de tests | ≥ 80 % | Bloquer la PR |

> Les seuils de performance sont **provisoires**, à recalibrer après baseline.

---

## Commandes utiles

```bash
make dev              # Démarrer la stack dev (Docker Compose)
make test             # Tous les tests
make lint             # Linting (ruff)
make format           # Formatage (black)
make validate-data    # Great Expectations
make tf-localstack    # Terraform apply contre LocalStack
```

---

## Variables d'environnement

Ne jamais committer de secrets. En dev, utiliser `.env` (ignoré par `.gitignore`). Voir `.env.example`.

---

## Documentation de référence

- [Cahier des charges](docs/CDC_GRAVIA.md)
- [Architecture de données](docs/Architecture_GRAVIA.md)
- [Plan de gouvernance](docs/Gouvernance_GRAVIA.md)
- [AIPD](docs/AIPD_GRAVIA.md)
- [Référentiel RNCP](docs/referentiel.md)
- [Dataset BAAC](https://www.data.gouv.fr/fr/datasets/bases-de-donnees-annuelles-des-accidents-corporels-de-la-circulation-routiere-annees-de-2005-a-2024/)
