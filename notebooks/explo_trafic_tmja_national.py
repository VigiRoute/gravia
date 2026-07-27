"""Exploration : le TMJA (Trafic Moyen Journalier Annuel) est-il une source exploitable pour
entraîner GRAVIA sur l'historique BAAC — là où le DATEX II national s'est révélé temps réel
uniquement (cf. notebooks/explo_trafic_datex_national.py) ?

Source : data.gouv.fr, dataset "Trafic moyen journalier annuel sur le réseau routier
national" (Ministère / CEREMA). Contrairement au DATEX II, c'est un **vrai archivage
annuel** : un fichier CSV par année de 2007 à 2024, un enregistrement par section de route
avec le trafic moyen journalier de cette année-là. Granularité : section de route (PR début
-> PR fin), pas le point de l'accident — donc bien plus grossier que du temps réel, mais
alignable sur les millésimes BAAC.

Ce script teste la jointure concrète BAAC <-> TMJA sur le millésime 2023 :
  - BAAC `lieux-2023.csv` donne (catr, voie, pr) par accident (via lieux.Num_Acc)
  - TMJA `TMJA_2023.csv` donne (route, prD, prF, depPrD) par section
  - On reconstruit un code de route BAAC (catr 1=autoroute/2=nationale -> préfixe A/N +
    numéro de voie) et on vérifie s'il matche une section TMJA du même département dont le
    PR de l'accident tombe dans l'intervalle [prD, prF].

Objectif : mesurer la VRAIE couverture (pas supposer qu'elle est bonne), avant de décider si
le TMJA vaut la peine d'être intégré comme feature d'entraînement. Pas de features ni de
modèle à ce stade.

RÉSULTATS (BAAC 2023, 70 861 lignes lieux) :
  - Historique RÉEL confirmé : un fichier TMJA par année de 2007 à 2024 (contrairement au
    DATEX II qui n'a que du temps réel).
  - Seuls 5,8 % des accidents sont éligibles (catr autoroute/nationale + voie/PR propres :
    le champ `voie` BAAC est un texte libre très bruité — "AUTOROUTE A 63",
    "Echangeur 16.1 (Rd Pt autoroute A1)" — dont l'extraction du numéro de route doit être
    volontairement conservatrice).
  - Parmi ces éligibles, seuls 27,6 % matchent effectivement une section TMJA.
  - COUVERTURE FINALE : ~1,6 % de l'ensemble des accidents 2023. Cause structurelle, pas un
    bug de parsing : le TMJA ne couvre que le réseau **non concédé** (donc exclut l'essentiel
    des autoroutes à péage, où se concentre une bonne part des accidents catr=1).
  → Le TMJA est réellement historique, mais trop peu couvrant pour être une feature
    universelle. Utilisable au mieux comme feature optionnelle/sparse (avec gestion explicite
    du manquant) sur le sous-ensemble concerné, pas comme enrichissement systématique.
"""

import sys
import urllib.request
from pathlib import Path

import polars as pl

sys.stdout.reconfigure(encoding="utf-8")

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "trafic_tmja"

TMJA_2023_URL = "https://www.data.gouv.fr/api/1/datasets/r/b9528e37-5b81-403b-850f-e142e01285e2"
BAAC_LIEUX_2023_URL = (
    "https://static.data.gouv.fr/resources/bases-de-donnees-annuelles-des-accidents-"
    "corporels-de-la-circulation-routiere-annees-de-2005-a-2023/20241023-153219/"
    "lieux-2023.csv"
)
BAAC_CARACT_2023_URL = (
    "https://static.data.gouv.fr/resources/bases-de-donnees-annuelles-des-accidents-"
    "corporels-de-la-circulation-routiere-annees-de-2005-a-2023/20241028-103125/"
    "caract-2023.csv"
)

# BAAC catr (catégorie de route) -> préfixe de code route TMJA. Seules l'autoroute et la
# nationale sont couvertes par le réseau routier national non concédé du TMJA.
CATR_TO_PREFIX = {"1": "A", "2": "N"}


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as response:
            dest.write_bytes(response.read())
    return dest


def load_tmja(csv_path: Path) -> pl.DataFrame:
    df = pl.read_csv(csv_path, separator=";", infer_schema_length=0)
    return df.select(
        "route",
        pl.col("prD").cast(pl.Int64, strict=False),
        pl.col("prF").cast(pl.Int64, strict=False),
        # BAAC zéro-pad les départements ("01"), TMJA non ("1") : aligner sur 2 chiffres.
        pl.col("depPrD").str.zfill(2).alias("dep"),
        pl.col("TMJA").str.replace(",", ".").cast(pl.Float64, strict=False),
        pl.col("ratio_PL").str.replace(",", ".").cast(pl.Float64, strict=False),
    ).filter(pl.col("prD").is_not_null() & pl.col("prF").is_not_null())


def load_baac_lieux(csv_path: Path) -> pl.DataFrame:
    df = pl.read_csv(csv_path, separator=";", infer_schema_length=0)
    # `voie` est un champ libre bruité ("AUTOROUTE A 63", "A46N   2.930 A 7.065",
    # "Echangeur 16.1 (Rd Pt autoroute A1)", "CHARLES DE GAULLE (BD)"...). On n'extrait un
    # numéro de route que si le champ ENTIER correspond au motif "numéro seul" ou
    # "A/N + numéro (+ 1-2 lettres de suffixe)" — tout le reste (texte libre, plage de PR
    # incrustée) est ambigu et volontairement écarté plutôt que mal interprété.
    voie_num = pl.col("voie").str.extract(r"^\s*[AN]?\s*(\d{1,4})[A-Za-z]{0,2}\s*$", 1)
    return df.select(
        pl.col("Num_Acc").alias("num_acc"),
        pl.col("catr"),
        voie_num.alias("voie_num"),
        pl.col("pr").cast(pl.Int64, strict=False),
    )


def load_baac_dep(csv_path: Path) -> pl.DataFrame:
    df = pl.read_csv(csv_path, separator=";", infer_schema_length=0)
    return df.select(pl.col("Num_Acc").alias("num_acc"), pl.col("dep"))


def main() -> None:
    tmja_path = fetch(TMJA_2023_URL, RAW_DIR / "tmja_2023.csv")
    lieux_path = fetch(BAAC_LIEUX_2023_URL, RAW_DIR / "baac_lieux_2023.csv")
    caract_path = fetch(BAAC_CARACT_2023_URL, RAW_DIR / "baac_caract_2023.csv")

    tmja = load_tmja(tmja_path)
    lieux = load_baac_lieux(lieux_path)
    dep = load_baac_dep(caract_path)

    accidents = lieux.join(dep, on="num_acc", how="left")
    n_total = accidents.height

    print(f"Accidents BAAC 2023 (lignes lieux)      : {n_total}")
    print()
    print("Répartition par catégorie de route (catr) :")
    print(accidents.group_by("catr").len().sort("len", descending=True))
    print()

    # Ne peuvent matcher le TMJA que les accidents sur autoroute (1) ou route nationale (2),
    # avec un numéro de voie et un PR renseignés (pr != -1).
    candidats = accidents.filter(
        pl.col("catr").is_in(list(CATR_TO_PREFIX))
        & pl.col("voie_num").is_not_null()
        & pl.col("pr").is_not_null()
        & (pl.col("pr") >= 0)
    ).with_columns(
        (pl.col("catr").replace(CATR_TO_PREFIX) + pl.col("voie_num").str.zfill(4)).alias(
            "route"
        )
    )

    n_candidats = candidats.height
    print(
        f"Accidents éligibles (autoroute/nationale, voie+PR renseignés) : "
        f"{n_candidats} ({n_candidats / n_total:.1%} du total)"
    )

    matched = candidats.join(tmja, on=["route", "dep"], how="inner").filter(
        (pl.col("pr") >= pl.col("prD")) & (pl.col("pr") <= pl.col("prF"))
    )
    # un accident peut matcher plusieurs sections TMJA se chevauchant : dédoublonner
    n_matched = matched.select("num_acc").n_unique()

    print(
        f"Accidents effectivement matchés à une section TMJA          : "
        f"{n_matched} ({n_matched / n_total:.1%} du total, "
        f"{n_matched / n_candidats:.1%} des éligibles)"
    )
    print()
    print("Aperçu des matches (TMJA, ratio poids lourds) :")
    print(
        matched.select("num_acc", "route", "dep", "pr", "TMJA", "ratio_PL")
        .unique(subset=["num_acc"])
        .head(10)
    )


if __name__ == "__main__":
    main()
