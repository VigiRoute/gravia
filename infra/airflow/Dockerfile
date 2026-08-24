# Image Airflow custom : apache/airflow:3.3.0 en Python 3.12 (cf. pyproject.toml,
# requires-python = ">=3.12,<3.13" — la variante par défaut de ce tag tourne en Python 3.13,
# incompatible) + le sous-ensemble de dépendances GRAVIA dont les DAGs ont réellement besoin.
#
# Seul un sous-ensemble de pyproject.toml est installé, pas le paquet gravia lui-même : le code
# applicatif reste monté en volume depuis ../src (cf. docker-compose.yml,
# PYTHONPATH=/opt/airflow/src) pour que les DAGs voient les modifications locales sans
# reconstruire l'image. Le sous-ensemble se limite à ce que gravia.bronze/silver/gold importent
# réellement (polars, pyarrow, sqlalchemy, psycopg2-binary, boto3, python-dotenv,
# python-dateutil) — le reste de pyproject.toml (fastapi, mlflow, great-expectations,
# lightgbm...) sert l'entraînement/serving/monitoring, pas l'ETL, et entre en conflit avec les
# propres dépendances internes d'Airflow si on l'installe dans son environnement (constaté :
# installer fastapi==0.140.0 du projet écrase le fastapi<0.137 dont l'API server d'Airflow 3 a
# besoin). Les versions restent figées : extraites de pyproject.toml (via `tomllib`, stdlib),
# pas dupliquées dans un requirements.txt maintenu à part.
FROM apache/airflow:3.3.0-python3.12

COPY pyproject.toml /tmp/pyproject.toml

RUN python -c "\
import tomllib; \
etl_packages = {'polars', 'pyarrow', 'sqlalchemy', 'psycopg2-binary', 'python-dotenv', 'python-dateutil', 'boto3'}; \
deps = tomllib.load(open('/tmp/pyproject.toml', 'rb'))['project']['dependencies']; \
selected = [d for d in deps if d.split('==')[0].strip().lower() in etl_packages]; \
open('/tmp/requirements.txt', 'w').write('\n'.join(selected))" \
    && pip install --no-cache-dir --user -r /tmp/requirements.txt
