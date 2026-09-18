# Notebooks d'exploration — GRAVIA

Scripts d'exploration ayant produit les résultats et les décisions cités dans le
[CDC](../docs/CDC_GRAVIA.md) (§13.6-7), l'[architecture](../docs/Architecture_GRAVIA.md) et la
[présentation](../docs/Presentation_Projet_GRAVIA_Jedha.md) (Bloc 4).

Chaque script documente ses résultats dans son docstring d'en-tête et est volontairement séparé
du code de production (`ml/`, `pipelines/`) : ce sont des explorations tracées, pas des
composants de la solution. Ce sont des scripts `.py` rejouables en ligne de commande — **pas
des notebooks Jupyter** (`.ipynb`). La plupart n'ont pas de visualisation (résultats imprimés en
console dans le docstring) ; `eda_exploration_baac.py` (§0 ci-dessous) est l'exception : une
vraie EDA visuelle, sur les données Gold déjà nettoyées. Seuls 3 scripts téléchargent leurs propres données
(`explo_trafic_datex_national.py`, `explo_trafic_tmja_national.py`,
`explo_trafic_paris_correlation_annuel.py`) ; les 6 autres — dont `eda_baseline_baac.py`, qui
porte les chiffres de référence cités dans tout le projet — lisent les CSV BAAC déjà présents
dans `data/raw/baac/` (à télécharger manuellement depuis data.gouv.fr, cf. lien CDC) et échouent
sinon.

## 0. EDA exploratoire visuelle

[`eda_exploration_baac.py`](eda_exploration_baac.py) — exploration visuelle sur les features
Gold déjà nettoyées/typées (pas les CSV bruts, contrairement au reste de ce dossier), ce qui
permet de compter correctement la sentinelle BAAC `-1` plutôt que des NULL SQL. Nécessite la
stack dev démarrée et Gold déjà chargé (`python -m gravia.gold`).

| Graphique | Ce qu'il montre |
|---|---|
| ![Taux de gravité par millésime](img/eda_target_balance_by_year.png) | Stabilité du taux de gravité 2019-2023 (~35-36 % chaque année) |
| ![Non-renseigné par feature](img/eda_missingness.png) | Taux de code `-1` par feature — `regime_circulation` culmine à 5,9 % |
| ![Taux de gravité par département](img/eda_gravity_by_departement.png) | Paris (75) nettement sous la moyenne nationale — première intuition visuelle de l'angle mort développé en §3 |
| ![Taux de gravité par feature](img/eda_gravity_by_feature.png) | Écarts réels de gravité selon la luminosité, le type de collision, la catégorie de route |

## 1. Baseline de référence

| Script | Question | Résultat |
|---|---|---|
| [`eda_baseline_baac.py`](eda_baseline_baac.py) | Que vaut un modèle sur le seul BAAC, sans enrichissement ? | **recall 0,808 / F1 macro 0,708** (holdout 2023, seuil calibré sur validation 2022) — les deux seuils CDC sont atteints sans aucune source externe |

## 2. Enrichissement trafic : exploré puis écarté

| Script | Question | Résultat |
|---|---|---|
| [`explo_trafic_datex_national.py`](explo_trafic_datex_national.py) | Le flux DATEX II national est-il exploitable ? | Mesures propres, mais **temps réel uniquement** (pas d'archive) et table de sites décalée → jointure spatiale, 61 % des sites géolocalisables |
| [`explo_trafic_tmja_national.py`](explo_trafic_tmja_national.py) | Le TMJA fournit-il l'historique manquant ? | Historique réel (2007→2024) mais **~1,6 % de couverture BAAC** (réseau non concédé seulement) |
| [`explo_trafic_paris_correlation.py`](explo_trafic_paris_correlation.py) | Les capteurs Paris se joignent-ils au BAAC ? | **96 %** de rattachement temporel sur une semaine test — **mais valeur réellement exploitable (débit/taux d'occupation non nuls) : ~46-53 % seulement**, corrigé dans `eval_trafic_gain_paris.py` (le rattachement temporel n'est pas la couverture utile) |
| [`explo_trafic_paris_correlation_annuel.py`](explo_trafic_paris_correlation_annuel.py) | Le signal tient-il sur une année complète ? | Corrélation **statistiquement significative** (occupation plus faible chez les accidents graves, Welch p≈0,03) |
| [`eval_trafic_gain_paris.py`](eval_trafic_gain_paris.py) | Ce signal améliore-t-il un modèle dédié Paris ? | **Non** : recall +0,007, F1 macro −0,013 |
| [`eval_trafic_gain_national.py`](eval_trafic_gain_national.py) | Et en feature sparse sur le modèle national (config prod) ? | **Non** : recall +0,001, F1 −0,000 |

→ **Décision : trafic écarté comme feature du modèle** (signal réel mais redondant avec les
variables temporelles déjà présentes). Le flux temps réel reste ingéré pour sa valeur
opérationnelle propre (routage des secours), cf. Architecture §2.1.

## 3. Angle mort de sécurité et calibration des seuils

| Script | Question | Résultat |
|---|---|---|
| [`eval_seuil_par_zone.py`](eval_seuil_par_zone.py) | Un seuil unique est-il sûr partout ? | **Non** : recall **0,007** sur Paris malgré 0,808 au national. Seuils par département → recall Paris 0,777 mais **F1 macro national 0,573** (sous le seuil CDC) |
| [`eval_enrichissement_vs_seuil.py`](eval_enrichissement_vs_seuil.py) | Enrichir les features corrige-t-il la tension ? | Meilleure configuration : enrichissement (2-roues / poids lourd / piéton) **+** seuils par département → recall Paris **0,812**, F1 macro **0,609**. Tension **atténuée, pas résolue** |

→ **Point ouvert documenté** (CDC §13.7, §14) : recall ≥ 0,80 par sous-groupe et F1 macro ≥ 0,70
global ne sont pas simultanément atteignables par simple calibration de seuil. À arbitrer avant
mise en production.

## Prérequis

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
```

Les scripts trafic Paris nécessitent d'extraire au préalable les archives annuelles dans
`data/raw/trafic_paris/<année>_full/` — elles sont compressées en **Deflate64**, que le module
`zipfile` de Python ne sait pas lire : utiliser `unzip` (Info-ZIP).
