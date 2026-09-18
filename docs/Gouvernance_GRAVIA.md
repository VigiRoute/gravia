# Plan de gouvernance des données — GRAVIA

> **Projet** : GRAVIA — Aide à la décision pour la priorisation des secours routiers
> **Organisation (fictive)** : VigiRoute — opérateur d'intérêt public
> **Version** : 0.2 (révisé après implémentation — cadrage initial du 2026-06-29, périmètre réel confirmé au 2026-09-13, cf. [AVANCEMENT_GRAVIA.md](AVANCEMENT_GRAVIA.md))
> **Date de dernière révision** : 2026-09-13
> **Bloc RNCP** : Bloc 1 — Piloter la gouvernance des données
> **Documents liés** : [Cahier des charges](CDC_GRAVIA.md) · [Architecture](Architecture_GRAVIA.md) · [AIPD](AIPD_GRAVIA.md)

## Table des matières

1. Objet et portée
2. Contexte
3. Périmètre et classification des données
4. Cadre réglementaire et normatif
5. Gouvernance organisationnelle (parties prenantes & RACI)
6. Politique de qualité des données
7. Politique de sécurité et de confidentialité
8. Disponibilité et continuité
9. Cycle de vie et conservation des données
10. Droits des personnes concernées
11. Gestion des violations de données
12. Audits et contrôles
13. Formation, sensibilisation et accessibilité
14. Gestion des risques et plans de contingence
15. Amélioration continue
16. Références

---

## 1. Objet et portée

Ce document définit la politique de gouvernance des données de VigiRoute pour le projet GRAVIA. Il fixe les règles garantissant la **qualité**, la **disponibilité**, la **sécurité** et la **confidentialité** des données, ainsi que la **conformité** aux réglementations en vigueur, sur l'ensemble du cycle de vie des données (ingestion → entraînement → production → archivage).

Il s'adresse à toutes les parties prenantes du projet et constitue le cadre de référence opposable en cas d'audit.

## 2. Contexte

GRAVIA est un système d'aide à la décision qui prédit la **gravité probable d'un accident** au moment de son signalement, afin d'aider les opérateurs de secours à prioriser les moyens. Le système s'appuie :

- en **apprentissage**, sur la base ouverte **BAAC** (accidents corporels 2005→2024, data.gouv.fr) ;
- en **production**, sur des **signalements temps réel** concernant des **victimes réelles identifiables** (donnée de santé : gravité).

Cette dualité (open data historique vs données opérationnelles sensibles) structure l'ensemble de la politique.

## 3. Périmètre et classification des données

| Catégorie | Exemples | Niveau de sensibilité | Traitement |
|---|---|---|---|
| Données non personnelles | Conditions météo (déjà portées par le BAAC), trafic DATEX exploré en batch (testé puis écarté comme feature de modèle), type de route, type de collision | Public | Libre |
| **Données personnelles** | Âge, géolocalisation précise | Restreint | Pseudonymisation |
| **Données personnelles, conservées telles quelles** | Sexe (nécessaire à l'audit d'équité EC-6), motif de trajet | Restreint | Non transformées — cf. AIPD §2.2 |
| **Données sensibles (art. 9 RGPD)** | **Gravité = donnée de santé** | Confidentiel | Accès strict, AIPD |
| Données opérationnelles (prod) | Signalement temps réel d'une victime identifiable | Confidentiel | Chiffrement, accès strict, traçabilité |

**Donnée à risque de ré-identification** : le croisement `latitude/longitude` + `date` + `commune` peut réidentifier une victime → mesure d'agrégation géographique en couche Silver.

## 4. Cadre réglementaire et normatif

| Référentiel | Application dans GRAVIA |
|---|---|
| **RGPD** (UE 2016/679) | Base légale, minimisation, droits des personnes, sécurité, AIPD (art. 35) |
| **Loi Informatique et Libertés** | Droits d'accès, rectification, suppression, opposition |
| **ISO/IEC 27001** | Système de management de la sécurité de l'information (SMSI) |
| **Recommandations ANSSI** | Mesures de sécurité techniques (chiffrement, cloisonnement) |
| **RGAA** | Accessibilité des interfaces et documents |
| **Lignes directrices IA (CNIL / AI Act)** | IA éthique, explicabilité, human-in-the-loop, tests de biais |
| **HDS (Hébergement de Données de Santé)** | Non requis sur le périmètre actuel (données ouvertes/pseudonymisées) ; un hébergeur certifié HDS (art. L1111-8 CSP) serait requis en production traitant les données réelles des victimes |

**Base légale du traitement** : mission d'intérêt public (art. 6.1.e RGPD) ; pour les données de santé, intérêt public dans le domaine de la santé / sauvegarde des intérêts vitaux (art. 9.2.i / 9.2.c).

## 5. Gouvernance organisationnelle

### 5.1 Parties prenantes et rôles

| Partie prenante | Responsabilités |
|---|---|
| **Direction VigiRoute** | Sponsor, arbitrages, validation de la politique, allocation des moyens |
| **Délégué à la protection des données (DPO)** | Garant RGPD, pilotage de l'AIPD, point de contact CNIL, avis sur les traitements |
| **Responsable sécurité (RSSI)** | SMSI ISO 27001, mesures techniques, gestion des incidents de sécurité |
| **Architecte IA / Data** | Mise en œuvre technique de la politique (pseudonymisation, accès, qualité) |
| **Data stewards** | Qualité et documentation des données au quotidien |
| **Opérateurs de secours** | Utilisateurs finaux ; décision humaine finale (human-in-the-loop) |
| **Fournisseur de données (ONISR)** | Source BAAC, documentation des millésimes |
| **CNIL** | Autorité de contrôle externe |

### 5.2 Matrice RACI (extraits clés)

| Activité | Direction | DPO | RSSI | Architecte IA | Data stewards |
|---|---|---|---|---|---|
| Validation de la politique | **A** | C | C | R | I |
| Réalisation de l'AIPD | I | **A/R** | C | C | I |
| Pseudonymisation des données | I | C | C | **R** | C |
| Contrôle qualité des données | I | I | I | C | **R** |
| Gestion d'une violation | A | **R** | R | C | I |
| Audits réguliers | A | **R** | R | C | C |

*(R = Réalise, A = Approuve, C = Consulté, I = Informé)*

## 6. Politique de qualité des données

| Dimension | Règle | Contrôle |
|---|---|---|
| Exactitude | Valeurs conformes aux nomenclatures BAAC | Great Expectations (`gravité ∈ {-1,1..4}` — `-1` = non renseigné, codé explicitement par le BAAC, cf. CLAUDE.md) |
| Complétude | Champs clés non nuls (identifiant, date, lieu) | Tests de complétude par millésime |
| Cohérence | Plausibilité (âge ≤ 110 ans, borné en Silver ; `an`, `vma`/`nbv` bornés en Great Expectations ; `dep` non nul mais sans borne de valeur) | Règles de validation — les coordonnées ne sont plus contrôlées ici : `lat`/`long` sont supprimées avant ce contrôle (pseudonymisation dès la Silver, cf. AIPD §5) |
| Unicité | Pas de doublons d'accidents | Déduplication en Silver |
| Traçabilité | Lineage des transformations Bronze→Silver→Gold | Journalisation + métadonnées |

Tout lot non conforme **bloque la promotion vers Gold** (échec de la tâche `quality` du DAG Airflow, cf. `src/gravia/quality.py::SilverQualityError`) — le Parquet Silver déjà écrit n'est ni déplacé ni supprimé (pas de quarantaine physique) et aucune notification automatique n'est envoyée au data steward à ce jour ; ces deux points restent des mesures cibles.

## 7. Politique de sécurité et de confidentialité

| Mesure | Mise en œuvre | Statut |
|---|---|---|
| Chiffrement au repos | S3/RDS chiffrés | À implémenter — cible prod, pas de cloud réel déployé à ce jour |
| Chiffrement en transit | TLS systématique | À implémenter — stack dev en HTTP local |
| Pseudonymisation | Dès la couche Silver | **Fait** — vérifié sur les 5 millésimes réels (cf. AIPD §5) |
| Contrôle d'accès | Moindre privilège (IAM, rôles PostgreSQL), authentification forte (portée précisée ci-dessous) | À implémenter — cible prod |
| Gestion des secrets | `.env` (dev) / Secrets Manager (prod) ; jamais dans Git | **Fait** en dev (`.env` gitignoré) |
| Cloisonnement | Séparation des environnements dev / prod | **Fait** — Docker Compose (dev) / Terraform-LocalStack (cible prod), jamais mélangés |
| Journalisation | Logs d'accès et de prédictions horodatés | **Partiel** — prédictions journalisées en logs structurés ; pas encore de registre interrogeable 12 mois (cf. AIPD §5) |

> **Portée de « authentification forte »** : cette politique vise la **surface exposée** — l'API de prédiction et les accès aux données réelles en production. Elle ne s'applique pas à l'outillage interne de la stack dev (Airflow, MLflow, Grafana), qui tourne sur le réseau Docker local, n'est jamais exposé publiquement et simplifie volontairement son authentification pour la vélocité (ex. Airflow dev désactive son auth par défaut — `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS`, cf. `AVANCEMENT_GRAVIA.md`). Écart assumé et documenté, cohérent avec la séparation dev/prod déjà posée (CLAUDE.md) — pas une dérogation silencieuse à la politique de sécurité de production.

## 8. Disponibilité et continuité

- Objectif de disponibilité de l'API : ≥ 99,5 %/mois.
- **Fait** : retries des tâches Airflow (`default_args={"retries": 2}`, `pipelines/airflow/dags/etl_medallion_dag.py`), redémarrage automatique des conteneurs de la stack dev (`restart: unless-stopped`, `infra/docker-compose.yml`) et des pods K8s (vérifié sur cluster `kind` réel, cf. `AVANCEMENT_GRAVIA.md`). Trouvé sans configuration en auditant cette affirmation (`default_task_retries=0` constaté sur le scheduler réel) — corrigé.
- **À implémenter** : redondance du stockage (S3/RDS) et sauvegardes testées — politique cible pour la production, non déployée à ce jour (cf. AIPD §5, plan d'action).

## 9. Cycle de vie et conservation des données

| Donnée | Durée de conservation | Sort final |
|---|---|---|
| BAAC (open data) | Durée du projet | Archivage |
| Données opérationnelles temps réel | Conservées le temps de l'intervention, puis **anonymisées sous 30 jours** | Anonymisation |
| Journaux de prédiction | **12 mois** (audit / traçabilité) | Suppression |
| Modèles entraînés | Versionnés (MLflow), conservés tant qu'actifs ou de référence | Archivage |

*(Durées définies avec le DPO ; révisées à chaque évolution du traitement.)*

### Registre des traitements

Le traitement **« scoring de gravité d'accident »** est inscrit au **registre des traitements** (art. 30 RGPD), précisant : la **finalité** (aide à la priorisation des secours), la **base légale** (art. 6.1.e et 9.2.i), les **catégories de données** (personnelles + santé), les **destinataires** (opérateurs habilités), les **durées de conservation** (ci-dessus) et les **mesures de sécurité** (§7). Le registre est tenu à jour par le **DPO** et révisé à chaque évolution du traitement.

## 10. Droits des personnes concernées

Procédures pour répondre aux demandes d'**accès**, **rectification**, **suppression**, **opposition** et **limitation** :
- point de contact DPO publié ;
- délai de réponse d'un mois (RGPD) ;
- registre des demandes ;
- vérification d'identité du demandeur.

> Le système GRAVIA repose sur une **décision humaine finale** (art. 22 RGPD) : pas de décision entièrement automatisée à effet juridique sur la personne.

## 11. Gestion des violations de données

Procédure en cas de violation :
1. **Détection** (monitoring, alerte) et qualification de la gravité.
2. **Confinement** et mesures correctives immédiates (RSSI).
3. **Notification CNIL sous 72 h** si risque pour les droits et libertés (DPO).
4. **Information des personnes concernées** si risque élevé.
5. **Journalisation** dans le registre des violations et retour d'expérience.

## 12. Audits et contrôles

- Audit interne **annuel** des pratiques de gestion des données (DPO + RSSI).
- Revue de conformité à chaque évolution majeure du traitement.
- Contrôles automatisés continus (qualité des données, dérive, sécurité).
- Plan d'action correctif suivi jusqu'à clôture.

## 13. Formation, sensibilisation et accessibilité

- **Sensibilisation** de tous les collaborateurs aux principes RGPD et sécurité (à l'arrivée puis annuellement).
- **Formation** ciblée des équipes techniques (pseudonymisation, gestion des secrets, anti-leakage).
- **Accessibilité (RGAA)** : supports de formation et documentation fournis en formats accessibles (contrastes, structure de titres, alternatives textuelles), conditions adaptées aux personnes en situation de handicap.
- **Conduite du changement** : implication des opérateurs de secours en amont, communication sur le rôle d'aide (et non de remplacement) du système.

## 14. Gestion des risques et plans de contingence

| Risque | Gravité | Stratégie de gestion | Plan de contingence |
|---|---|---|---|
| Ré-identification d'une victime | Élevée | Pseudonymisation, agrégation géo | Notification, retrait des données concernées |
| Violation de données (fuite) | Élevée | Chiffrement, accès restreint | Procédure §11, notification CNIL |
| Biais discriminatoire du modèle | Élevée | Tests d'équité, atténuation, human-in-the-loop | Suspension du scoring, réentraînement |
| Dérive des données | Moyenne | Monitoring Evidently (`ml/monitoring/drift.py`, lancé à la main) | Réentraînement manuel/calendaire à ce jour (`workflow_dispatch` + trimestriel) — le déclenchement automatique par dérive détectée est visé (CDC EF-6), pas encore câblé |
| Indisponibilité de l'API | Moyenne | Redondance, supervision | Bascule mode dégradé (décision humaine seule) |
| Erreur humaine de manipulation | Moyenne | Formation, moindre privilège | Restauration depuis sauvegarde |

## 15. Amélioration continue

- Révision **annuelle** de la politique et après toute évolution réglementaire (RGPD, AI Act).
- Veille réglementaire portée par le DPO.
- Indicateurs de gouvernance suivis (taux de conformité qualité, délais de réponse aux demandes, incidents).

## 16. Références

- [Cahier des charges GRAVIA](CDC_GRAVIA.md) · [Architecture GRAVIA](Architecture_GRAVIA.md) · [AIPD GRAVIA](AIPD_GRAVIA.md)
- RGPD — Règlement (UE) 2016/679 (art. 6, 9, 22, 33, 34, 35)
- Loi n° 78-17 Informatique et Libertés
- ISO/IEC 27001 · Recommandations ANSSI · RGAA
- Méthodologie AIPD — CNIL

---

## Annexe — Correspondance avec le référentiel (Bloc 1)

| Compétence | Couverture dans ce document |
|---|---|
| C1.1 — Concevoir une politique de Data Gouvernance avec les parties prenantes | Ce document dans son ensemble ; parties prenantes impliquées §5 |
| C1.2 — Collaborer avec les parties prenantes pour intégrer la politique | §5 (rôles, matrice RACI) |
| C1.3 — Former et sensibiliser les collaborateurs, y compris en situation de handicap | §13 |
| C1.4 — Réaliser des audits réguliers de conformité | §12 |
| C1.5 — Évaluer les risques (qualité, sécurité) | §14 ; risques spécifiques aux données de santé et à l'IA détaillés dans l'[AIPD](AIPD_GRAVIA.md) §4 |
