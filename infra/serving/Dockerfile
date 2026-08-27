# Image de serving : python:3.12-slim (cf. pyproject.toml, requires-python = ">=3.12,<3.13") +
# toutes les dépendances du projet + le code applicatif (`src/gravia`, `ml/`) copié dans l'image —
# contrairement à infra/airflow/Dockerfile, pas de montage en volume : ce service vise la cible
# prod (Architecture_GRAVIA.md §6.2, « Serving | FastAPI sur ECS/EKS »), une image autonome et
# déployable, pas un confort de développement.
#
# Presque toutes les dépendances de pyproject.toml sont installées (pas un allow-list filtré
# comme pour Airflow — cf. infra/airflow/Dockerfile, où le filtrage évitait un conflit de
# versions avec les propres dépendances d'Airflow ; ce n'est pas le cas ici). Seules
# great-expectations et evidently sont exclues (deny-list, pas allow-list : seuls ces deux
# paquets, pas utilisés par le serving, posent un problème concret) — evidently tire
# statsmodels, qui a besoin d'une chaîne de compilation (gfortran/BLAS) absente de l'image slim
# (constaté en testant : échec de build de statsmodels). Installer cette chaîne de compilation
# pour deux paquets inutiles au serving n'aurait fait qu'alourdir l'image et le temps de build.
FROM python:3.12-slim

# libgomp1 (runtime OpenMP) : le binaire précompilé de LightGBM en a besoin au chargement, absent
# de l'image slim (constaté en testant : OSError libgomp.so.1 au démarrage d'uvicorn).
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml

RUN python -c "\
import tomllib; \
excluded = {'great-expectations', 'evidently'}; \
deps = tomllib.load(open('/app/pyproject.toml', 'rb'))['project']['dependencies']; \
selected = [d for d in deps if d.split('==')[0].strip().lower() not in excluded]; \
open('/app/requirements.txt', 'w').write('\n'.join(selected))" \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY ml /app/ml

ENV PYTHONPATH=/app/src:/app

EXPOSE 8000

CMD ["uvicorn", "ml.serving.api:app", "--host", "0.0.0.0", "--port", "8000"]
