# Script de démo — GRAVIA

Check-list et pas-à-pas pour montrer le projet en live (prof, soutenance, ou base pour les
captures vidéo exigées par le [CDC §15](CDC_GRAVIA.md)). Vérifié fonctionnel le 2026-09-20 :
stack dev up, 67 tests passent, Gold chargé (273 226 accidents), modèle en `staging` (v3).

## Avant le rendez-vous (5 min)

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d
docker compose -f infra/docker-compose.yml --env-file .env ps
```

Tout doit être `Up`/`healthy`. Si un service vient d'être recréé, laisser ~1 min avant de
commencer (healthchecks). URLs à avoir en onglets ouverts :

| Service | URL | Identifiants |
|---|---|---|
| API (Swagger) | http://localhost:8000/docs | — |
| MLflow | http://localhost:5000 | — |
| Airflow | http://localhost:8080 | n'importe quoi (auth désactivée en dev, voir note) |
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |

> **Note Airflow** : `SIMPLE_AUTH_MANAGER_ALL_ADMINS=true` en dev — n'importe quel
> identifiant/mot de passe sur l'écran de login donne un accès admin (pas de vrai compte à
> retenir, documenté dans `infra/docker-compose.yml`).

---

## 1. Le pipeline de données en marche (5 min)

**Dire :** "Le projet suit une architecture medallion Bronze/Silver/Gold, orchestrée par
Airflow, sur les vraies données BAAC 2019-2023."

1. Ouvrir **Airflow** → DAG `etl_medallion_baac` → vue Grid. Montrer les runs passés (colonnes
   vertes = succès), un groupe de tâches par millésime (`bronze` → `silver` → `quality` →
   `gold`).
2. Cliquer sur une tâche `quality` → logs → montrer que Great Expectations valide les données
   à chaque exécution (codes BAAC, complétude, plausibilité).
3. (Optionnel, si le temps le permet) Déclencher un nouveau run manuel pour montrer que ça
   tourne en live — ~80s pour les 5 millésimes.

**Dire :** "Le résultat atterrit dans PostgreSQL, schéma en étoile."

```bash
docker exec gravia-postgres-1 psql -U gravia -d gravia -c "SELECT count(*) FROM gold_fact_accident;"
```
→ **273 226** accidents.

---

## 2. Le modèle et son suivi (5 min)

**Dire :** "Chaque expérience d'entraînement est trackée dans MLflow, seul un modèle qui
franchit les deux seuils du CDC (recall ≥ 0,80, F1 macro ≥ 0,70) est promu."

1. Ouvrir **MLflow** → expérience `gravia-severity-classifier` → montrer les runs comparés
   (régression logistique, Random Forest, LightGBM) avec leurs métriques.
2. Onglet **Models** → `gravia-severity-classifier` → montrer l'alias `staging` sur la version
   retenue.

---

## 3. La prédiction en direct (5 min)

**Dire :** "Le modèle est servi par une API FastAPI, avec une explication SHAP à chaque
prédiction — pas de boîte noire."

Ouvrir http://localhost:8000/docs, dérouler `POST /v1/predict-severity`, cliquer *Try it out*,
coller un des deux payloads ci-dessous.

**Cas non grave** (centre-ville, jour, pas de 2-roues) :
```json
{
  "moment": "2026-09-20T14:30:00", "departement": "75", "agglomeration": true,
  "intersection": 1, "categorie_route": 4, "regime_circulation": 2, "nb_voies": 2,
  "voie_reservee": 0, "profil_route": 1, "trace_plan": 1, "vitesse_max": 50,
  "infrastructure": 0, "situation": 1, "luminosite": 1, "meteo": 1, "etat_surface": 1,
  "type_collision": 3, "nb_vehicules": 2, "flag_2roues_motorise": true,
  "flag_poids_lourd": false, "flag_velo_edp": false, "flag_pieton": false
}
```
→ `non_grave`, probabilité ~0,10.

**Cas grave** (autoroute, nuit, poids lourd) :
```json
{
  "moment": "2026-09-20T02:30:00", "departement": "40", "agglomeration": false,
  "intersection": 1, "categorie_route": 1, "regime_circulation": 3, "nb_voies": 2,
  "voie_reservee": 0, "profil_route": 1, "trace_plan": 1, "vitesse_max": 130,
  "infrastructure": 0, "situation": 1, "luminosite": 3, "meteo": 1, "etat_surface": 1,
  "type_collision": 2, "nb_vehicules": 2, "flag_2roues_motorise": false,
  "flag_poids_lourd": true, "flag_velo_edp": false, "flag_pieton": false
}
```
→ `grave`, probabilité ~0,93. Montrer `top_contributions` (SHAP) : `departement`,
`flag_poids_lourd`, `heure`, `vitesse_max` en tête — cohérent avec l'intuition métier.

Enchaîner sur **Grafana** → dashboard « GRAVIA — API de prédiction » → montrer que les deux
appels qu'on vient de faire apparaissent dans les métriques de latence/volume en quasi temps
réel (scrapées par Prometheus).

---

## 4. L'angle mort de sécurité — le morceau le plus intéressant (10 min)

**Dire :** "Le modèle atteint les seuils du CDC au niveau national, mais on a creusé plus loin
et trouvé un vrai problème de sécurité caché derrière cette moyenne."

Ouvrir `notebooks/eval_seuil_par_zone.ipynb` (déjà exécuté, pas besoin de relancer) :

1. Montrer la cellule qui calibre un **seuil unique** sur la validation 2022 → recall national
   **0,808** (conforme CDC).
2. Montrer la cellule suivante : le même seuil, appliqué au sous-ensemble **Paris** → recall
   **0,007**. 3 accidents graves détectés sur 431. **Dire :** "Le modèle rate 99 % des
   accidents graves à Paris tout en ayant l'air conforme au national — parce que Paris a un
   taux de gravité structurellement plus faible (~9 % contre ~36 %), donc les probabilités
   prédites y sont systématiquement plus basses."
3. Montrer la tentative de réparation (seuils par département) : recall Paris remonte à
   **0,777**, mais le F1 macro national **s'effondre à 0,573** (sous le seuil CDC de 0,70).
4. Enchaîner sur `eval_enrichissement_vs_seuil.ipynb` : la meilleure config trouvée (seuils +
   features enrichies 2-roues/poids lourd/piéton) — recall Paris 0,812, F1 macro 0,609.
   **Dire :** "Tension atténuée, mais pas résolue — c'est documenté comme risque ouvert à
   trancher avant mise en production, pas caché sous le tapis."

**Ce que ça démontre :** l'esprit critique, pas juste "le modèle marche" — on a cherché où il
pourrait *ne pas* marcher, on l'a trouvé, quantifié, et documenté honnêtement plutôt que de
s'arrêter au chiffre national qui semblait suffisant.

---

## 5. Rigueur et traçabilité (5 min, si le temps le permet)

- **`docs/AVANCEMENT_GRAVIA.md`** : section "État par composant" — chaque brique documente
  aussi les bugs trouvés et corrigés en marchant (pas juste "fait ✅").
- **Historique GitHub** (`gravia` + `gravia-mlops`) : PRs avec description, montre le travail
  itératif et les corrections réelles, pas un commit unique "projet fini".
- **Tests** : `pytest` (67 tests) + CI GitHub Actions (lint + tests + build) sur chaque PR.

```bash
.venv/Scripts/python -m pytest -q
```
→ `67 passed`.

---

## Filet de sécurité

- Si un service ne répond pas : `docker compose -f infra/docker-compose.yml --env-file .env restart <service>`.
- Si l'API renvoie une erreur 503 sur `/v1/predict-severity` : le modèle n'a pas fini de
  charger depuis MLflow au démarrage du conteneur `serving` — attendre ~10s et réessayer, ou
  vérifier `GET /health` (`modele_version` doit être renseigné).
- Tout peut se montrer **hors ligne** si le wifi du rendez-vous lâche : les notebooks sont déjà
  exécutés avec leurs résultats sauvegardés (`.ipynb`), pas besoin de connexion pour les rouvrir.
