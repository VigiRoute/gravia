# GRAVIA

**Aide à la décision pour la priorisation des secours routiers** — prédiction de la gravité probable d'un accident à partir des informations connues au moment de son signalement.

Projet MLOps réalisé dans le cadre du titre RNCP **Architecte en Intelligence Artificielle**.

## En bref

| | |
|---|---|
| **Tâche IA** | Classification binaire tabulaire (`grave` / `non grave`) |
| **Donnée** | [BAAC](https://www.data.gouv.fr/fr/datasets/bases-de-donnees-annuelles-des-accidents-corporels-de-la-circulation-routiere-annees-de-2005-a-2024/) — accidents corporels 2005→2024 |
| **Cible** | `grave` = au moins une victime hospitalisée ou tuée |
| **Architecture** | Medallion Bronze / Silver / Gold + MLOps |
| **Stack dev** | MinIO · PostgreSQL · Polars/DuckDB · Airflow · MLflow · FastAPI · Docker |
| **Stack prod (cible)** | AWS S3 · RDS · EKS · MSK · Terraform (via LocalStack en local) |

## Organisation du code

Projet réparti sur l'organisation **VigiRoute**, en deux dépôts (exigence de certification) :

- **[VigiRoute/gravia](https://github.com/VigiRoute/gravia)** *(ce dépôt)* — la solution IA : code, pipelines de données, docs, stack dev.
- **[VigiRoute/gravia-mlops](https://github.com/VigiRoute/gravia-mlops)** — CI/CD et déploiement : IaC Terraform, Kubernetes, workflows de déploiement.

## Documentation

- 📋 [Cahier des charges](docs/CDC_GRAVIA.md)
- 🏗️ [Architecture de données](docs/Architecture_GRAVIA.md)
- 🛡️ [Plan de gouvernance](docs/Gouvernance_GRAVIA.md)
- 🔍 [AIPD](docs/AIPD_GRAVIA.md)

## Démarrage rapide

```bash
cp .env.example .env     # configurer les variables
make dev                 # démarrer la stack dev
make test                # lancer les tests
```

## Licence

Projet académique.
