#!/bin/bash
# Crée les bases annexes au premier démarrage du conteneur PostgreSQL.
#
# La base applicative (couche Gold) est créée par l'image elle-même via POSTGRES_DB.
# Ce script y ajoute les deux bases de métadonnées, isolées de la donnée métier :
#   - airflow : métadonnées d'orchestration (DAG runs, tâches, connexions)
#   - mlflow  : backend store du tracking et du registry de modèles
#
# Exécuté une seule fois, au moment de l'initialisation du volume de données.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE airflow OWNER $POSTGRES_USER;
    CREATE DATABASE mlflow  OWNER $POSTGRES_USER;
EOSQL

echo "Bases 'airflow' et 'mlflow' créées."
