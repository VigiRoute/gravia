# PROJET DE CERTIFICATION — ARCHITECTE EN IA (MASTÈRE 2)
## Document de présentation de projet

**Nom et prénom :** Jack Lecomte
**Titre du projet :** GRAVIA — Aide à la décision pour la priorisation des secours routiers
**Date :** 29 juin 2026

---

## 1. Présentation générale du projet

### 1.1 Secteur et organisation

GRAVIA s'inscrit dans le secteur de la **sécurité routière et des secours d'urgence**, un domaine **réglementé** car il manipule des **données de santé** (la gravité des blessures) relatives à des personnes vulnérables (victimes d'accident).

L'organisation porteuse, **VigiRoute**, est un **opérateur d'intérêt public** (issue d'un partenariat entre l'Observatoire national interministériel de la sécurité routière — **ONISR** — et les services de secours). De taille moyenne (quelques centaines d'agents), elle dispose d'une direction des systèmes d'information, d'un délégué à la protection des données (DPO) et d'un responsable de la sécurité (RSSI). Sa mission : améliorer l'efficacité de la réponse aux accidents corporels de la circulation.

En France, la route fait **environ 3 400 morts et 235 000 blessés par an, dont près de 16 000 blessés graves** (ONISR, bilan définitif 2024), soit environ **50 000 accidents corporels** enregistrés chaque année. Quelques minutes gagnées sur l'engagement des moyens adaptés peuvent changer le pronostic vital : l'enjeu se mesure en **vies humaines** et en **délai d'intervention**, ce qui justifie l'investissement dans une aide à la décision prédictive.

Ce contexte — secteur réglementé, données de santé — place la **conformité, la gouvernance et l'éthique au cœur du projet**.

### 1.2 Problématique métier

Lorsqu'un accident est signalé, l'opérateur de régulation des secours doit décider **rapidement**, à partir d'informations **incomplètes**, quels moyens engager (véhicule simple, équipe médicalisée, hélicoptère). Une mauvaise estimation de la gravité retarde la prise en charge des cas les plus critiques.

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

VigiRoute exploite un système d'information auquel GRAVIA s'intègre comme brique d'aide à la décision :

- un **centre de régulation** recevant les appels d'urgence (15 / 18 / 112) et les signalements d'accidents ;
- un **bus de messages** (type Kafka) absorbant le **flux de trafic temps réel** (capteurs) et les signalements ;
- un **data lake** sur stockage objet (type S3) pour les données brutes et l'historique ;
- un **entrepôt analytique PostgreSQL** alimentant l'entraînement et les analyses ;
- des **connecteurs** vers des sources externes temps réel (trafic capteur, météo).

Les données mobilisées couvrent les **trois dimensions des 3V**, de manière non triviale :

| Dimension | Réalité du projet | Ordre de grandeur |
|---|---|---|
| **Volume** | Historique **BAAC** 2005→2024 + données enrichies (météo, géo, trafic) | Plusieurs millions de lignes `usagers` (~50–60 000 accidents/an sur ~20 ans) |
| **Vélocité** | **Flux trafic capteur temps réel** (firehose) + signalements à scorer, en plus du batch annuel | Trafic : **milliers de mesures/min** (débit/occupation recalculés toutes les 1–6 min sur 3 000+ points) ; signalements : ~150 accidents corporels/jour |
| **Variété** | Structuré + semi-structuré | BAAC (CSV tabulaire), trafic (**XML DATEX II**, testé puis écarté comme feature de modèle), signalements (JSON) |

Le système combine ainsi une **ingestion batch** (chargement des millésimes, réentraînement périodique) et une **ingestion temps réel** (signalements à scorer à la volée), ce qui impose une architecture capable d'absorber les deux régimes.

> **Origine du flux temps réel.** Le **flux haute fréquence** du système est la **donnée de trafic capteur** (état de circulation temps réel, DATEX II — milliers de mesures/min), bien réelle : c'est elle qui justifie le bus de messages. Les **signalements** à scorer sont des événements peu fréquents pour lesquels il n'existe pas de source ouverte (le BAAC est batch, la régulation des secours n'est pas en open data) : ils sont donc **simulés par rejeu du BAAC**. L'architecture temps réel reste réelle et fonctionnelle — seule la *source des signalements* est simulée, et serait remplacée en production par le feed réel de l'opérateur.

> *Les chiffres d'accidentalité (accidents corporels, tués, blessés) proviennent de l'ONISR (voir Sources). Les débits de signalements temps réel sont une **estimation du scénario** VigiRoute, dérivée de la volumétrie annuelle d'accidents.*

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
| **HDS (Hébergement de Données de Santé)** | Non applicable au périmètre actuel (données ouvertes et pseudonymisées). En **production** traitant des données réelles de victimes, l'hébergement imposerait un **hébergeur certifié HDS** (art. L1111-8 du Code de la santé publique) — anticipé comme exigence de passage à l'échelle. |

---

## 4. Bloc 1 — Gouvernance des données

GRAVIA s'appuie sur un **plan de gouvernance complet** et une **AIPD** (méthodologie CNIL).

**Politiques et conformité.** Le plan définit les politiques de **qualité**, **sécurité**, **confidentialité**, **disponibilité** et **conservation** des données, en intégrant explicitement RGPD, Loi Informatique et Libertés, ISO 27001, ANSSI et RGAA. La base légale et les droits des personnes y sont documentés.

**Parties prenantes et responsabilités.** Les rôles sont formalisés (Direction, DPO, RSSI, Architecte IA, data stewards, opérateurs) et déclinés dans une **matrice RACI** précisant qui réalise, approuve, est consulté ou informé pour chaque activité sensible (AIPD, pseudonymisation, gestion des violations, audits).

**Gestion des risques.** Les risques sont identifiés et associés à des stratégies et **plans de contingence** : ré-identification d'une victime, violation de données, biais discriminatoire, dérive du modèle, indisponibilité du service, erreur humaine.

**Audits et mise à jour.** Audit interne **annuel** (DPO + RSSI), contrôles automatisés continus (qualité, dérive, sécurité), revue à chaque évolution majeure, et **révision périodique** de la politique pour suivre les évolutions réglementaires (veille portée par le DPO).

**AIPD.** Le risque résiduel est jugé **acceptable** sous réserve de la mise en œuvre du plan d'action (pseudonymisation, agrégation géographique anti-ré-identification, chiffrement, tests d'équité, explicabilité, human-in-the-loop).

> **Justification du choix.** La **pseudonymisation dès la couche Silver** (plutôt qu'un simple contrôle d'accès) a été retenue pour limiter l'impact d'une éventuelle violation : les jeux d'entraînement ne contiennent aucune donnée directement identifiante. L'**agrégation géographique** a été préférée à la conservation des coordonnées exactes pour neutraliser le risque de ré-identification, sans perte significative de pouvoir prédictif — le contexte routier (type de route, agglomération) suffit au modèle.

*Livrables : [plan de gouvernance](Gouvernance_GRAVIA.md), [AIPD](AIPD_GRAVIA.md).*

---

## 5. Bloc 2 — Architecture de données pour l'IA

**Modélisation.** L'architecture repose sur un **modèle Medallion Bronze / Silver / Gold**. La couche Gold est modélisée en **schéma en étoile** (table de faits `fact_accident` au grain de l'accident, dimensions `date`, `lieu`, `conditions`, `collision`), construit **à partir des 4 tables BAAC enrichies de sources externes (météo)**. Le trafic (DATEX II) et les bulletins d'incidents ont été explorés puis écartés du schéma final : le trafic n'apporte pas de gain prédictif mesurable une fois le modèle doté des variables temporelles, et aucune source réelle de bulletins n'a été identifiée. Ce choix est justifié par le besoin de requêtage analytique et de variables prêtes pour l'entraînement.

**Choix techniques justifiés.**

- **Polars / DuckDB** plutôt que Spark : le volume tient en mémoire (< 10 Go) → Spark serait de la **sur-ingénierie**. Spark est documenté comme voie de montée en charge.
- **Stockage hybride** : Parquet (Bronze/Silver) sur MinIO/S3 + **PostgreSQL** (Gold relationnel).
- **Infrastructure** : Docker Compose en dev ; cible cloud AWS (S3, RDS, **EKS/Kubernetes** pour le calcul et le scaling) déployée via **Terraform**. Faute d'accès au cloud payant, le `terraform apply` est exécuté contre **LocalStack** (émulation AWS gratuite) pour fournir l'IaC et la démonstration « infra en production ».

**3V.** Volume (millions de lignes), vélocité (batch + temps réel), variété (structuré + semi-structuré + flux) sont pris en compte explicitement.

**Sécurité et surveillance.** Chiffrement au repos et en transit, pseudonymisation dès la Silver, gestion des accès au moindre privilège ; surveillance de l'infrastructure via **Prometheus + Grafana** (latence, erreurs, disponibilité) avec alertes.

**Documentation accessible.** Architecture documentée avec diagrammes (flux, ER, étoile) accompagnés de descriptions textuelles, en formats ouverts.

> **Justification du choix.** **Polars/DuckDB** ont été préférés à Spark : le volume tient en mémoire (< 10 Go), donc un moteur distribué serait sous-utilisé et difficile à justifier (sur-ingénierie). Spark est documenté comme **voie de montée en charge** si la volumétrie augmente. De même, **LocalStack** permet un déploiement Terraform réel et gratuit, sans dépendre d'un cloud payant, tout en conservant une **architecture cible AWS** documentée. Le bus **Kafka** est, lui, justifié par un **flux trafic temps réel à haute fréquence** (milliers de mesures/min), pas par les seuls signalements. Seul **Kubernetes** dépasse la charge actuelle : il est retenu pour le **scaling et la haute disponibilité** du service en production. Le traitement est dimensionné au plus juste, l'infrastructure vise la production, ce n'est pas une contradiction.

*Livrable : [document d'architecture](Architecture_GRAVIA.md).*

---

## 6. Bloc 3 — Pipelines de données pour l'IA

**Conception batch et temps réel.** Le pipeline fonctionne sur deux rythmes. En batch, il charge les millésimes BAAC et réentraîne le modèle. En temps réel, il reçoit en continu les données de trafic des capteurs, c'est-à-dire l'état de circulation au format DATEX II, soit plusieurs milliers de mesures chaque minute : c'est ce flux nourri qui justifie l'usage d'un bus de messages comme Redpanda. Les signalements d'accidents à évaluer arrivent sur ce même bus ; comme il n'existe pas de source ouverte pour les obtenir en direct, on les rejoue à partir du BAAC (voir section 2).

**ETL/ELT entre sources hétérogènes.** Le flux **Bronze → Silver → Gold** intègre des sources hétérogènes (BAAC, météo, géolocalisation) : nettoyage, typage, **pseudonymisation**, jointures d'enrichissement, encodage, puis construction du schéma en étoile et du label `is_grave`. Le trafic et les bulletins d'incidents ont été évalués comme sources d'enrichissement potentielles : le trafic (DATEX II national + capteurs Paris) a montré un signal statistique réel mais un gain prédictif nul une fois testé en modèle multivarié (features temporelles déjà suffisantes) — écarté ; les bulletins n'ont pas de source réelle identifiée — retirés du périmètre.

**Automatisation complète.** Orchestration par **Airflow** : collecte, traitement, mise à jour, **alertes** et **reprise sur erreur** (retries, redémarrage) sans intervention manuelle. Chaque tâche est **idempotente et relançable** (rejouable sans effet de bord).

**Contrôle qualité.** Validation par **Great Expectations** à chaque exécution (codes BAAC valides, complétude des clés, plausibilité des valeurs) ; les lots non conformes sont mis en quarantaine et signalés.

**Monitoring et conformité.** Suivi des métriques du pipeline et alertes proactives (Prometheus/Grafana). Conformité RGPD assurée par la **minimisation** (exclusion des identifiants directs et des variables post-enquête — règle **anti-leakage**), la **traçabilité** (lineage des transformations) et le respect des durées de conservation.

---

## 7. Bloc 4 — Déploiement de la solution IA

**Algorithme et démarche de benchmark.** Nous comparons plusieurs modèles sur les mêmes métriques : une régression logistique comme référence, une forêt aléatoire, puis des modèles de gradient boosting (LightGBM, XGBoost). Toutes les expériences sont suivies dans MLflow. Le modèle final sera choisi à partir des résultats, en donnant la priorité au recall de la classe « grave » (au moins 0,80), puis au F1 macro et au PR-AUC, et toujours sous condition d'explicabilité (SHAP). En triage de secours, manquer un cas grave (faux négatif) coûte bien plus cher qu'une fausse alerte (faux positif) : c'est ce qui justifie de prioriser le recall. Le gradient boosting part favori sur ces données tabulaires déséquilibrées, mais rien ne sera tranché avant d'avoir vu les chiffres.

**Intégration.** Le modèle est exposé par une API FastAPI (`/v1/predict-severity`) qui renvoie un score, une classe et une explication SHAP. Elle gère les erreurs, sécurise les accès et s'intègre à l'infrastructure existante.

**CI/CD.** Un pipeline GitHub Actions automatise toute la chaîne, des tests au déploiement en passant par le lint et la construction de l'image, avec un versionnage du code, des données et des modèles dans MLflow.

**Réentraînement et dérive.** Des scripts réentraînent le modèle automatiquement, à chaque nouveau millésime ou dès qu'une dérive est détectée, le tout de façon reproductible grâce aux graines fixées et au versionnage. La dérive des données est suivie avec Evidently, qui déclenche un réentraînement lorsque le PSI dépasse 0,2.

**Monitoring en production.** Nous suivons les performances et la latence, avec des alertes proactives et une vérification continue du respect des spécifications. Les métriques sont collectées par Prometheus et visualisées dans Grafana, avec des alertes sur seuils.

**Conformité et éthique.** Le projet respecte le RGPD, la loi Informatique et Libertés et l'ISO 27001. Sur le plan de l'IA éthique, chaque prédiction est explicable grâce à SHAP, des tests de non-discrimination mesurent l'équité par groupe d'âge et de sexe (*equalized odds*) et des mécanismes d'atténuation corrigent les biais éventuels (repondération, contrôle des variables sensibles). La vie privée est protégée et un opérateur humain conserve la décision finale (human-in-the-loop). Enfin, les interfaces et les documents respectent les règles d'accessibilité (RGAA).

> **Justification du choix.** Le choix du modèle est **différé au résultat d'un benchmark** plutôt que tranché a priori : c'est plus rigoureux et reproductible (comparaison tracée dans MLflow). Un **modèle profond est néanmoins écarté d'emblée** : sur des données tabulaires de ce volume il n'apporterait pas de gain et nuirait à l'explicabilité, exigence réglementaire ici. La famille **gradient boosting** est privilégiée a priori pour sa performance sur tabulaire déséquilibré et sa compatibilité naturelle avec SHAP, mais la décision finale dépendra des métriques mesurées.

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

---

## Sources

- ONISR — *Bilan définitif 2024 de la sécurité routière* : https://www.onisr.securite-routiere.gouv.fr/en/road-safety-performance/annual-road-safety-reports/2024-road-safety-annual-report (≈ 3 432 tués France entière, ≈ 235 000 blessés dont ≈ 16 000 graves).
- ONISR — *Bilan 2023 de la sécurité routière* : https://www.onisr.securite-routiere.gouv.fr/en/road-safety-performance/annual-road-safety-reports/2023-road-safety-annual-report
- Données BAAC (accidents corporels 2005→2024), data.gouv.fr : https://www.data.gouv.fr/fr/datasets/bases-de-donnees-annuelles-des-accidents-corporels-de-la-circulation-routiere-annees-de-2005-a-2024/
- État de circulation en temps réel (réseau routier national, DATEX II), transport.data.gouv.fr : https://transport.data.gouv.fr/datasets/etat-de-circulation-en-temps-reel-sur-le-reseau-national-routier-non-concede
- Comptages routiers permanents (capteurs), opendata.paris.fr : https://opendata.paris.fr/explore/dataset/comptages-routiers-permanents/

> Le **nombre d'accidents corporels** (~50 000/an) est un ordre de grandeur issu des bases BAAC ; les **débits de signalements temps réel** sont une estimation propre au scénario fictif VigiRoute, non issue d'une statistique officielle.
