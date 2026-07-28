"""Configuration centralisée de GRAVIA, lue depuis l'environnement.

Toutes les valeurs proviennent de variables d'environnement (fichier `.env` en dev, AWS
Secrets Manager en production) et ont un défaut aligné sur `.env.example` et sur la stack
`infra/docker-compose.yml`. Aucun secret n'est écrit en dur : les défauts présents ici sont
des identifiants de développement local, sans valeur hors de la machine du développeur.

Le module est volontairement sans effet de bord à l'import (hors chargement du `.env`) :
il expose des objets de configuration immuables que les autres modules reçoivent en
paramètre, ce qui les rend testables sans variables d'environnement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Racine du dépôt : src/gravia/config.py -> src/gravia -> src -> racine
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Charge le .env de la racine s'il existe ; les variables déjà définies dans
# l'environnement (cas des conteneurs) ne sont pas écrasées.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env(name: str, default: str) -> str:
    """Lit une variable d'environnement en repliant sur un défaut.

    Args:
        name: Nom de la variable d'environnement.
        default: Valeur utilisée si la variable est absente ou vide.

    Returns:
        La valeur de la variable, ou le défaut.
    """
    value = os.getenv(name)
    return value if value else default


@dataclass(frozen=True)
class StoragePaths:
    """Emplacements de données sur le disque local.

    Le lac (Bronze/Silver) vit sur MinIO ; ces chemins servent de zone de préparation
    locale avant téléversement, et de source pour les fichiers BAAC bruts téléchargés.

    Attributes:
        raw: Fichiers sources téléchargés (BAAC, référentiels), non versionnés.
        bronze: Parquet Bronze produit localement avant téléversement.
        silver: Parquet Silver produit localement avant téléversement.
    """

    raw: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "raw")
    bronze: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "bronze")
    silver: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "silver")

    @property
    def baac(self) -> Path:
        """Répertoire des fichiers CSV BAAC bruts."""
        return self.raw / "baac"


@dataclass(frozen=True)
class ObjectStoreConfig:
    """Accès au stockage objet S3-compatible (MinIO en dev, S3 en production).

    Attributes:
        endpoint: URL du service. Vide en production pour viser AWS S3 directement.
        access_key: Clé d'accès.
        secret_key: Clé secrète.
        bucket: Bucket applicatif accueillant les couches Bronze et Silver.
        region: Région AWS (sans effet sur MinIO, requise par boto3).
    """

    endpoint: str = field(default_factory=lambda: _env("MINIO_ENDPOINT", "http://localhost:9000"))
    access_key: str = field(default_factory=lambda: _env("MINIO_ACCESS_KEY", "minioadmin"))
    secret_key: str = field(default_factory=lambda: _env("MINIO_SECRET_KEY", "minioadmin"))
    bucket: str = field(default_factory=lambda: _env("MINIO_BUCKET", "gravia"))
    region: str = field(default_factory=lambda: _env("AWS_REGION", "eu-west-1"))


@dataclass(frozen=True)
class DatabaseConfig:
    """Connexion à la base analytique accueillant la couche Gold.

    Attributes:
        url: URL SQLAlchemy vers PostgreSQL.
    """

    url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql://gravia:gravia@localhost:5432/gravia"
        )
    )


@dataclass(frozen=True)
class Settings:
    """Configuration complète de l'application.

    Attributes:
        paths: Emplacements de données locaux.
        object_store: Accès au stockage objet.
        database: Accès à la base analytique.
        mlflow_tracking_uri: URI du serveur de tracking MLflow.
    """

    paths: StoragePaths = field(default_factory=StoragePaths)
    object_store: ObjectStoreConfig = field(default_factory=ObjectStoreConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    mlflow_tracking_uri: str = field(
        default_factory=lambda: _env("MLFLOW_TRACKING_URI", "http://localhost:5000")
    )


def get_settings() -> Settings:
    """Construit la configuration à partir de l'environnement courant.

    Appelée à chaque fois plutôt que mise en cache dans une variable de module : les tests
    peuvent ainsi modifier l'environnement et obtenir une configuration cohérente.

    Returns:
        La configuration complète.
    """
    return Settings()
