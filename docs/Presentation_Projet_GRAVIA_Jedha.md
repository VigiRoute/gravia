# PROJET DE CERTIFICATION — ARCHITECTE EN IA (MASTÈRE 2)
## Document de présentation de projet

**Nom et prénom :** ……………………………………………………
**Titre du projet :** GRAVIA — Aide à la décision pour la priorisation des secours routiers
**Date :** 29 juin 2026

---

## 1. Présentation générale du projet

### 1.1 Secteur et organisation

GRAVIA s'inscrit dans le secteur de la **sécurité routière et des secours d'urgence**, un domaine **réglementé** car il manipule des **données de santé** (la gravité des blessures) relatives à des personnes vulnérables (victimes d'accident).

L'organisation porteuse, **VigiRoute**, est un **opérateur d'intérêt public** (structure de type partenariat entre l'Observatoire national interministériel de la sécurité routière et les services de secours). De taille moyenne (quelques centaines d'agents), elle dispose d'une direction des systèmes d'information, d'un délégué à la protection des données (DPO) et d'un responsable de la sécurité (RSSI). Sa mission : améliorer l'efficacité de la réponse aux accidents corporels de la circulation.

Le caractère réglementé du secteur (santé) et la présence d'une gouvernance structurée facilitent la couverture des compétences de conformité, de gouvernance et d'éthique attendues.

### 1.2 Problématique métier

Lorsqu'un accident est signalé, l'opérateur de régulation des secours doit décider **rapidement** quels moyens engager (véhicule simple, équipe médicalisée, hélicoptère), à partir d'informations **incomplètes**. Une mauvaise estimation de la gravité retarde la prise en charge des cas les plus critiques.

> **Problème à résoudre :** estimer, dès le signalement, la **probabilité qu'un accident soit grave** (au moins une victime hospitalisée ou tuée), afin d'aider l'opérateur à prioriser et dimensionner les moyens.

Les contraintes imposent de réels arbitrages :

- **Temps réel** : prédiction attendue en moins de 500 ms (p95) au moment de l'appel.
- **Budget** : projet à coût maîtrisé → priorité aux solutions open source ; pas d'accès au free tier d'un cloud commercial.
- **Volumétrie** : ~20 années d'historique, plusieurs millions d'enregistrements.
- **Conformité** : données personnelles et de santé → RGPD, analyse d'impact (AIPD).
- **Qualité / fiabilité** : fort déséquilibre des classes (les cas graves sont minoritaires) ; risque de **fuite de données** si l'on utilise des variables connues seulement après enquête.

### 1.3 Parties prenantes

| Partie prenante | Rôle |
|---|---|
| Direction VigiRoute | Sponsor, arbitrages, validation, ROI |
| DPO | Conformité RGPD, pilotage de l'AIPD, contact CNIL |
| RSSI | Sécurité (ISO 27001 / ANSSI), gestion des incidents |
| Architecte IA / Data | Conception et mise en œuvre technique |
| Data stewards | Qualité et documentation des données |
| Opérateurs de secours | Utilisateurs finaux ; décision humaine finale |
| ONISR (fournisseur) | Source des données (BAAC) |
| CNIL | Autorité de contrôle externe |

---

## 2. Environnement technique existant

VigiRoute exploite un système d'information classique (bases opérationnelles, centre de réception des signalements 15/18/112) sur lequel GRAVIA vient se greffer comme brique d'aide à la décision.

Les données mobilisées couvrent les **trois dimensions des 3V**, de manière non triviale :

| Dimension | Réalité du projet |
|---|---|
| **Volume** | Base **BAAC** 2005→2024 : plusieurs millions de lignes `usagers` (~50–60 000 accidents/an) |
| **Vélocité** | Historique en **batch** (millésimes annuels) **+ flux temps réel** des signalements et du trafic (Bison Futé / Waze) |
| **Variété** | **Structuré** (BAAC : 4 tables), **semi-structuré** (météo Open-Meteo, géolocalisation BAN/OSM), **flux** événementiel temps réel |

Les flux combinent donc une **ingestion batch** (chargement des millésimes, réentraînement) et une **ingestion temps réel** (signalements à scorer).

---

## 3. Contraintes réglementaires

| Réglementation | Application justifiée à GRAVIA |
|---|---|
| **RGPD** | Traitement de données personnelles (âge, sexe, géolocalisation) et **sensibles** (gravité = donnée de santé, art. 9). Base légale : mission d'intérêt public (art. 6.1.e) et intérêt public en santé (art. 9.2.i). **AIPD obligatoire** (art. 35 : données sensibles + scoring + grande échelle). **Article 22** : pas de décision entièrement automatisée → human-in-the-loop. |
| **Loi Informatique et Libertés** | Exercice des droits (accès, rectification, suppression, opposition), registre des traitements. |
| **ISO/IEC 27001** | Système de management de la sécurité de l'information (chiffrement, gestion des accès, gestion des incidents). |
| **Recommandations ANSSI** | Mesures techniques de sécurisation (cloisonnement, secrets). |
| **RGAA** | Accessibilité des interfaces et des documents (personnes en situation de handicap). |
| **IA éthique (CNIL / AI Act)** | Explicabilité des prédictions, non-discrimination, respect de la vie privée. |

---

## 4. Bloc 1 — Gouvernance des données

GRAVIA s'appuie sur un **plan de gouvernance complet** et une **AIPD** (méthodologie CNIL).

**Politiques et conformité.** Le plan définit les politiques de **qualité**, **sécurité**, **confidentialité**, **disponibilité** et **conservation** des données, en intégrant explicitement RGPD, Loi Informatique et Libertés, ISO 27001, ANSSI et RGAA. La base légale et les droits des personnes y sont documentés.

**Parties prenantes et responsabilités.** Les rôles sont formalisés (Direction, DPO, RSSI, Architecte IA, data stewards, opérateurs) et déclinés dans une **matrice RACI** précisant qui réalise, approuve, est consulté ou informé pour chaque activité sensible (AIPD, pseudonymisation, gestion des violations, audits).

**Gestion des risques.** Les risques sont identifiés et associés à des stratégies et **plans de contingence** : ré-identification d'une victime, violation de données, biais discriminatoire, dérive du modèle, indisponibilité du service, erreur humaine.

**Audits et mise à jour.** Audit interne **annuel** (DPO + RSSI), contrôles automatisés continus (qualité, dérive, sécurité), revue à chaque évolution majeure, et **révision périodique** de la politique pour suivre les évolutions réglementaires (veille portée par le DPO).

**AIPD.** Le risque résiduel est jugé **acceptable** sous réserve de la mise en œuvre du plan d'action (pseudonymisation, agrégation géographique anti-ré-identification, chiffrement, tests d'équité, explicabilité, human-in-the-loop).

*Livrables : [plan de gouvernance](Gouvernance_GRAVIA.md), [AIPD](AIPD_GRAVIA.md).*

---

## 5. Bloc 2 — Architecture de données pour l'IA

**Modélisation.** L'architecture repose sur un **modèle Medallion Bronze / Silver / Gold**. La couche Gold est modélisée en **schéma en étoile** (table de faits `fact_accident` au grain de l'accident, dimensions `date`, `lieu`, `conditions`, `collision`), à partir d'un **modèle entité-relation** reliant les 4 tables BAAC. Ce choix est justifié par le besoin de requêtage analytique et de features model-ready.

**Choix techniques justifiés.**

- **Polars / DuckDB** plutôt que Spark : le volume tient en mémoire (< 10 Go) → Spark serait de la **sur-ingénierie**. Spark est documenté comme voie de montée en charge.
- **Stockage hybride** : Parquet (Bronze/Silver) sur MinIO/S3 + **PostgreSQL** (Gold relationnel).
- **Infrastructure** : Docker Compose en dev ; cible cloud AWS (S3, RDS, **EKS/Kubernetes** pour le calcul et le scaling) déployée via **Terraform**. Faute d'accès au cloud payant, le `terraform apply` est exécuté contre **LocalStack** (émulation AWS gratuite) pour fournir l'IaC et la démonstration « infra en production ».

**3V.** Volume (millions de lignes), vélocité (batch + temps réel), variété (structuré + semi-structuré + flux) sont pris en compte explicitement.

**Sécurité et surveillance.** Chiffrement au repos et en transit, pseudonymisation dès la Silver, gestion des accès au moindre privilège ; surveillance de l'infrastructure via **Prometheus + Grafana** (latence, erreurs, disponibilité) avec alertes.

**Documentation accessible.** Architecture documentée avec diagrammes (flux, ER, étoile) accompagnés de descriptions textuelles, en formats ouverts.

*Livrable : [document d'architecture](Architecture_GRAVIA.md).*

---

## 6. Bloc 3 — Pipelines de données pour l'IA

**Conception batch + temps réel.** Le pipeline combine un traitement **batch** (chargement des millésimes BAAC, réentraînement) et une **ingestion temps réel** des signalements via **Redpanda** (compatible Kafka), adaptée aux contraintes de vélocité.

**ETL/ELT entre sources hétérogènes.** Le flux **Bronze → Silver → Gold** intègre des sources hétérogènes (BAAC, météo, géolocalisation, trafic) : nettoyage, typage, **pseudonymisation**, jointures d'enrichissement, encodage, puis construction du schéma en étoile et du label `is_grave`.

**Automatisation complète.** Orchestration par **Airflow** : collecte, traitement, mise à jour, **alertes** et **reprise sur erreur** (retries, redémarrage) sans intervention manuelle.

**Contrôle qualité.** Validation par **Great Expectations** à chaque exécution (codes BAAC valides, complétude des clés, plausibilité des valeurs) ; les lots non conformes sont mis en quarantaine et signalés.

**Monitoring et conformité.** Suivi des métriques du pipeline et alertes proactives (Prometheus/Grafana). Conformité RGPD assurée par la **minimisation** (exclusion des identifiants directs et des variables post-enquête — règle **anti-leakage**), la **traçabilité** (lineage des transformations) et le respect des durées de conservation.

---

## 7. Bloc 4 — Déploiement de la solution IA

**Algorithme adapté.** Un modèle **LightGBM** (avec une régression logistique en baseline) réalise la **classification binaire** `grave` / `non grave`. Ce choix est justifié par l'efficacité du gradient boosting sur des données **tabulaires déséquilibrées** (pondération des classes), sa rapidité et son explicabilité ; un modèle profond serait inadapté à ce type et ce volume de données. Métriques retenues : **recall de la classe `grave` ≥ 0,80** (ne pas manquer un cas grave est prioritaire), F1 macro, PR-AUC.

**Intégration.** Exposition via une **API FastAPI** (`/v1/predict-severity`) renvoyant un score, une classe et une **explication SHAP**, avec gestion des erreurs et sécurisation des accès, compatible avec l'infrastructure existante.

**CI/CD.** Pipeline **GitHub Actions** automatisé de bout en bout (tests, lint, build, déploiement) avec **gestion des versions** du code, des données et des modèles (MLflow).

**Réentraînement et dérive.** Scripts de **réentraînement automatique** (nouveau millésime ou détection de dérive), reproductibles (graines fixées, versioning). **Détection de dérive** (data drift) via **Evidently** (PSI < 0,2 comme seuil de déclenchement).

**Monitoring en production.** Suivi des performances et de la latence, alertes proactives, vérification du respect des spécifications.

**Conformité et éthique.** RGPD, Loi Informatique et Libertés, ISO 27001 ; **IA éthique** : explicabilité (SHAP), **tests de non-discrimination** (équité selon âge/sexe), respect de la vie privée, **human-in-the-loop** ; **accessibilité** des interfaces et documents (RGAA).

> **Deux dépôts distincts** sont prévus, conformément à l'attendu : un dépôt pour la **solution IA** (entraînement, modèle, API) et un dépôt pour le **pipeline CI/CD et l'infrastructure** (IaC, déploiement, orchestration).

---

## 8. Synthèse des livrables prévus

| Bloc | Livrables |
|---|---|
| **Transverse** | Cahier des charges ; ce document de présentation ; dépôts GitHub |
| **Bloc 1 — Gouvernance** | Plan de gouvernance des données ; AIPD ; matrice RACI ; présentation (slides) |
| **Bloc 2 — Architecture** | Document d'architecture ; diagrammes (flux, ER, schéma en étoile) ; code Terraform (IaC) ; vidéo de l'infra déployée (LocalStack) |
| **Bloc 3 — Pipelines** | DAGs Airflow ; pipeline ETL/ELT Bronze→Silver→Gold ; suite Great Expectations ; tableaux de bord de monitoring ; vidéo du pipeline en exécution |
| **Bloc 4 — Solution IA** | Code de la solution IA (dépôt 1) ; pipeline CI/CD + IaC (dépôt 2) ; modèle suivi dans MLflow ; API FastAPI ; rapports d'explicabilité et d'équité ; monitoring de dérive (Evidently) ; vidéo de la solution en production |

**Format de remise.** Document final attendu au format `.doc` nommé « Nom Prénom », déposé sur le Drive de l'école avant le **jeudi 2 juillet 2026**.
