# Plan de gouvernance des données — GRAVIA

> **Projet** : GRAVIA — Aide à la décision pour la priorisation des secours routiers
> **Organisation (fictive)** : VigiRoute — opérateur d'intérêt public
> **Version** : 0.1
> **Date** : 2026-06-29
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
| Données non personnelles | Conditions météo, type de route, type de collision | Public | Libre |
| **Données personnelles** | Âge, sexe, géolocalisation précise, motif de trajet | Restreint | Pseudonymisation |
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
| Exactitude | Valeurs conformes aux nomenclatures BAAC | Great Expectations (`gravité ∈ {1..4}`, codes valides) |
| Complétude | Champs clés non nuls (identifiant, date, lieu) | Tests de complétude par millésime |
| Cohérence | Plausibilité (âge, coordonnées en France) | Règles de validation |
| Unicité | Pas de doublons d'accidents | Déduplication en Silver |
| Traçabilité | Lineage des transformations Bronze→Silver→Gold | Journalisation + métadonnées |

Tout lot non conforme est **rejeté ou mis en quarantaine** et signalé au data steward.

## 7. Politique de sécurité et de confidentialité

| Mesure | Mise en œuvre |
|---|---|
| Chiffrement au repos | S3/RDS chiffrés |
| Chiffrement en transit | TLS systématique |
| Pseudonymisation | Dès la couche Silver |
| Contrôle d'accès | Moindre privilège (IAM, rôles PostgreSQL), authentification forte |
| Gestion des secrets | `.env` (dev) / Secrets Manager (prod) ; jamais dans Git |
| Cloisonnement | Séparation des environnements dev / prod |
| Journalisation | Logs d'accès et de prédictions horodatés |

## 8. Disponibilité et continuité

- Objectif de disponibilité de l'API : ≥ 99,5 %/mois.
- Redondance du stockage (S3/RDS), sauvegardes régulières et testées.
- Plan de reprise : retries d'orchestration, redémarrage automatique des conteneurs, restauration depuis sauvegarde.

## 9. Cycle de vie et conservation des données

| Donnée | Durée de conservation (proposée) | Sort final |
|---|---|---|
| BAAC (open data) | Durée du projet | Archivage |
| Données opérationnelles temps réel | Durée strictement nécessaire à la décision + délai légal | Suppression / anonymisation |
| Journaux de prédiction | 12 mois (audit) | Suppression |
| Modèles entraînés | Versionnés (MLflow) | Archivage |

*(Durées à valider avec le DPO selon le cadre légal applicable.)*

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
| Dérive des données | Moyenne | Monitoring Evidently | Réentraînement automatique |
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
