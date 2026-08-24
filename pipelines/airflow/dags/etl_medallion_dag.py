"""DAG d'orchestration du pipeline de données BAAC : Bronze -> Silver -> Gold.

Un groupe de tâches par millésime (`bronze` -> `silver` -> `gold`, dans cet ordre — Gold a
besoin de Silver, qui a besoin de Bronze), les millésimes étant indépendants entre eux et donc
parallélisables. Déclenchement manuel (`schedule=None`) : les millésimes BAAC sont publiés
annuellement, pas de cadence à automatiser pour l'instant.

Les imports de `gravia.bronze/silver/gold` sont volontairement à l'intérieur de chaque tâche, pas
en tête de fichier : ce sont eux qui tirent les dépendances lourdes (polars, pyarrow, sqlalchemy),
et les garder hors du corps du module limite ce que le *dag processor* réanalyse à chaque cycle.

Le code applicatif est résolu depuis le montage `/opt/airflow/src` (cf. `PYTHONPATH` dans
`infra/docker-compose.yml`), pas depuis une copie figée dans l'image `infra/airflow/Dockerfile` :
les modifications locales sont visibles sans reconstruire l'image, seules ses dépendances le sont.
"""

from __future__ import annotations

from datetime import datetime

from airflow.sdk import dag, task, task_group

from gravia.bronze import DEFAULT_YEARS


@dag(
    dag_id="etl_medallion_baac",
    description="Pipeline BAAC Bronze -> Silver -> Gold",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["etl", "bronze", "silver", "gold"],
)
def etl_medallion_baac() -> None:
    @task_group(group_id="millesime")
    def process_millesime(year: int) -> None:
        @task(task_id="bronze")
        def run_bronze(year: int) -> None:
            from gravia.bronze import ingest
            from gravia.config import get_settings

            ingest((year,), get_settings())

        @task(task_id="silver")
        def run_silver(year: int) -> None:
            from gravia.config import get_settings
            from gravia.silver import clean

            clean((year,), get_settings())

        @task(task_id="gold")
        def run_gold(year: int) -> None:
            from gravia.config import get_settings
            from gravia.gold import load

            load((year,), get_settings())

        run_bronze(year) >> run_silver(year) >> run_gold(year)

    for year in DEFAULT_YEARS:
        process_millesime.override(group_id=f"millesime_{year}")(year)


etl_medallion_baac()
