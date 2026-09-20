"""Schémas Pydantic de `POST /v1/predict-severity` (cf. CDC_GRAVIA.md, EF-5).

Les champs correspondent à la configuration `"enriched"` actuellement promue à l'alias
`staging` du registry MLflow (cf. `ml/training/benchmark.py::feature_columns`,
docs/ml_training_results.md) — pas à toutes les colonnes de `gold_fact_accident`. Les codes
numériques reprennent le dictionnaire officiel ONISR (cf. CLAUDE.md, documentation de référence) ;
`-1` signifie « non renseigné » pour la quasi-totalité d'entre eux, comme dans le BAAC source.

`heure`/`mois`/`jour_semaine` ne sont pas des champs de la requête : dérivés côté serveur depuis
`moment` (l'instant du signalement), pour ne pas demander à l'appelant de calculer lui-même un
jour de semaine ISO — l'anti-leakage (CLAUDE.md) est respecté puisque `moment` est par définition
connu au moment du signalement.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PredictSeverityRequest(BaseModel):
    """Caractéristiques d'un accident connues au moment du signalement."""

    moment: datetime = Field(..., description="Date et heure du signalement (ISO 8601).")

    # --- Localisation (rubrique lieux / caracteristiques BAAC) ---
    departement: str = Field(
        ..., description="Code INSEE du département (ex. '75', '2A' en Corse)."
    )
    agglomeration: bool = Field(..., description="Accident survenu en agglomération.")
    intersection: int = Field(
        ...,
        description="Type d'intersection : -1 non renseigné, 1 hors intersection, 2 en X, "
        "3 en T, 4 en Y, 5 à plus de 4 branches, 6 giratoire, 7 place, 8 passage à niveau, "
        "9 autre.",
    )
    categorie_route: int = Field(
        ...,
        description="1 autoroute, 2 nationale, 3 départementale, 4 voie communale, "
        "5 hors réseau public, 6 parking, 7 métropole urbaine, 9 autre.",
    )
    regime_circulation: int = Field(
        ...,
        description="-1 non renseigné, 1 sens unique, 2 bidirectionnelle, 3 chaussées "
        "séparées, 4 voies à affectation variable.",
    )
    nb_voies: int = Field(..., ge=0, description="Nombre total de voies de circulation.")
    voie_reservee: int = Field(
        ...,
        description="-1 non renseigné, 0 sans objet, 1 piste cyclable, 2 bande cyclable, "
        "3 voie réservée.",
    )
    profil_route: int = Field(
        ...,
        description="Déclivité : -1 non renseigné, 1 plat, 2 pente, 3 sommet de côte, "
        "4 bas de côte.",
    )
    trace_plan: int = Field(
        ...,
        description="-1 non renseigné, 1 rectiligne, 2 courbe à gauche, 3 courbe à droite, "
        "4 en S.",
    )
    vitesse_max: int = Field(..., ge=0, description="Vitesse maximale autorisée (km/h).")
    infrastructure: int = Field(
        ...,
        description="-1 non renseigné, 0 aucun, 1 souterrain/tunnel, 2 pont/autopont, "
        "3 bretelle d'échangeur, 4 voie ferrée, 5 carrefour aménagé, 6 zone piétonne, "
        "7 zone de péage, 8 chantier, 9 autres.",
    )
    situation: int = Field(
        ...,
        description="-1 non renseigné, 0 aucun, 1 sur chaussée, 2 bande d'arrêt d'urgence, "
        "3 accotement, 4 trottoir, 5 piste cyclable, 6 autre voie spéciale, 8 autres.",
    )

    # --- Conditions au moment de l'accident ---
    luminosite: int = Field(
        ...,
        description="1 plein jour, 2 crépuscule/aube, 3 nuit sans éclairage public, "
        "4 nuit avec éclairage public non allumé, 5 nuit avec éclairage public allumé.",
    )
    meteo: int = Field(
        ...,
        description="-1 non renseigné, 1 normale, 2 pluie légère, 3 pluie forte, "
        "4 neige/grêle, 5 brouillard/fumée, 6 vent fort/tempête, 7 temps éblouissant, "
        "8 temps couvert, 9 autre.",
    )
    etat_surface: int = Field(
        ...,
        description="-1 non renseigné, 1 normale, 2 mouillée, 3 flaques, 4 inondée, "
        "5 enneigée, 6 boue, 7 verglacée, 8 corps gras/huile, 9 autre.",
    )
    type_collision: int = Field(
        ...,
        description="-1 non renseigné, 1 deux véhicules frontale, 2 deux véhicules par "
        "l'arrière, 3 deux véhicules par le côté, 4 trois véhicules et plus en chaîne, "
        "5 trois véhicules et plus collisions multiples, 6 autre collision, 7 sans collision.",
    )

    # --- Véhicules et usagers impliqués ---
    nb_vehicules: int = Field(..., ge=0, description="Nombre de véhicules impliqués.")
    flag_2roues_motorise: bool = Field(
        ..., description="Un deux-roues motorisé (scooter, moto, quad) est impliqué."
    )
    flag_poids_lourd: bool = Field(
        ..., description="Un poids lourd, tracteur routier, bus ou car est impliqué."
    )
    flag_velo_edp: bool = Field(
        ..., description="Un vélo ou un engin de déplacement personnel est impliqué."
    )
    flag_pieton: bool = Field(..., description="Un piéton est impliqué.")


class FeatureContribution(BaseModel):
    """Contribution d'une variable à la prédiction (valeur SHAP)."""

    feature: str
    contribution: float


class PredictSeverityResponse(BaseModel):
    """Réponse de `POST /v1/predict-severity`.

    Une estimation, pas une décision (human-in-the-loop) : à l'opérateur d'arbitrer, ce endpoint
    ne déclenche aucune action.
    """

    gravite_predite: str = Field(..., description="'grave' ou 'non_grave'.")
    probabilite: float = Field(..., description="Probabilité de gravité estimée par le modèle.")
    seuil_decision: float = Field(
        ..., description="Seuil calibré sur validation utilisé pour la décision binaire."
    )
    modele_version: str = Field(
        ..., description="Version du modèle dans le registry MLflow (gravia-severity-classifier)."
    )
    top_contributions: list[FeatureContribution] = Field(
        ...,
        description="Variables les plus contributives (valeurs SHAP, |contribution| décroissante).",
    )
