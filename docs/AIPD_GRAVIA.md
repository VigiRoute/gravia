# Analyse d'Impact relative à la Protection des Données (AIPD) — GRAVIA

> **Traitement** : Prédiction de la gravité d'un accident pour l'aide à la priorisation des secours
> **Responsable de traitement (fictif)** : VigiRoute
> **Version** : 0.1
> **Date** : 2026-06-29
> **Méthodologie** : CNIL (description · nécessité/proportionnalité · risques · mesures)
> **Documents liés** : [Plan de gouvernance](Gouvernance_GRAVIA.md) · [Cahier des charges](CDC_GRAVIA.md)

## 1. Pourquoi une AIPD est-elle obligatoire ?

Une AIPD est requise (art. 35 RGPD) car le traitement réunit **plusieurs critères** de la liste CNIL :

- **Données sensibles** : la gravité est une **donnée de santé** (art. 9).
- **Évaluation / scoring** : prédiction d'un état de santé probable.
- **Grande échelle** : traitement potentiel de l'ensemble des signalements d'accidents.
- **Personnes vulnérables** : victimes d'accident.

> Le BAAC est de l'open data déjà publié ; **c'est le système en production** — qui traite en temps réel des données de **victimes réelles identifiables** — qui fonde l'obligation d'AIPD. L'apprentissage sur BAAC en constitue la base historique.

---

## 2. Volet 1 — Description du traitement

### 2.1 Finalité
Estimer la probabilité qu'un accident signalé soit **grave** (au moins une victime hospitalisée ou tuée), afin d'**aider** les opérateurs de secours à prioriser et dimensionner les moyens. **Aide à la décision** — la décision finale reste humaine.

### 2.2 Données traitées
| Type | Données | Statut |
|---|---|---|
| Personnelles | Âge, sexe, géolocalisation, motif de trajet | Pseudonymisées en Silver |
| **Sensibles** | Gravité (donnée de santé) — **cible** | Accès strict |
| Contextuelles | Date/heure, météo, **trafic temps réel**, route, type de collision, véhicules, **bulletins d'incidents (texte)** | Non personnelles |

**Minimisation** : les identifiants directs ne sont pas utilisés ; les variables connues seulement après enquête (équipement, blessures détaillées) sont **exclues** (anti-leakage).

### 2.3 Destinataires
Opérateurs de secours habilités, équipe data/IA (données pseudonymisées), DPO/RSSI (audit). Aucun transfert à des tiers commerciaux.

### 2.4 Durées de conservation
Cf. [plan de gouvernance §9](Gouvernance_GRAVIA.md). Données opérationnelles temps réel conservées le temps strictement nécessaire ; journaux de prédiction 12 mois.

### 2.5 Supports
Stockage objet (S3/MinIO) chiffré, base PostgreSQL (RDS), conteneurs (Kubernetes), pipelines orchestrés (Airflow).

### 2.6 Transferts hors UE
Aucun (hébergement UE / local).

---

## 3. Volet 2 — Nécessité et proportionnalité

| Principe | Évaluation |
|---|---|
| **Base légale** | Mission d'intérêt public (art. 6.1.e) ; santé : art. 9.2.i / 9.2.c |
| **Finalité déterminée** | Oui — aide à la priorisation des secours, finalité unique et explicite |
| **Minimisation** | Oui — exclusion des identifiants directs et des variables post-enquête |
| **Exactitude** | Contrôles qualité (Great Expectations) ; documentation des millésimes |
| **Limitation de conservation** | Durées définies et justifiées (§9 gouvernance) |
| **Information des personnes** | Mention d'information ; rôle d'aide à la décision explicité |
| **Droits des personnes** | Procédures accès/rectification/suppression/opposition (§10 gouvernance) |
| **Décision automatisée (art. 22)** | Écartée — **human-in-the-loop** obligatoire |
| **Sous-traitants** | Encadrés par contrat (art. 28) si recours cloud |

**Conclusion** : le traitement est **nécessaire et proportionné** à la finalité, sous réserve des mesures du volet 4.

---

## 4. Volet 3 — Appréciation des risques pour les droits et libertés

Évaluation des trois **événements redoutés** (échelle CNIL : négligeable / limitée / importante / maximale).

### 4.1 Accès illégitime aux données
- **Impact** : atteinte à la vie privée, révélation d'un état de santé (gravité).
- **Sources de risque** : attaquant externe, accès interne abusif.
- **Gravité** : *Importante* · **Vraisemblance** : *Limitée* (après mesures).
- **Mesures** : chiffrement, moindre privilège, journalisation, cloisonnement.

### 4.2 Modification non désirée des données
- **Impact** : prédiction erronée → mauvaise priorisation des secours.
- **Sources** : erreur de pipeline, altération malveillante.
- **Gravité** : *Importante* · **Vraisemblance** : *Limitée*.
- **Mesures** : contrôles qualité, intégrité, versioning, tests automatisés.

### 4.3 Disparition de données
- **Impact** : indisponibilité du service d'aide à la décision.
- **Sources** : panne, suppression accidentelle.
- **Gravité** : *Limitée* · **Vraisemblance** : *Limitée*.
- **Mesures** : sauvegardes testées, redondance, plan de reprise.

### 4.4 Risques spécifiques à l'IA

| Risque IA | Description | Mesure |
|---|---|---|
| **Ré-identification** | Croisement lat/long + date + commune | Pseudonymisation, **agrégation géographique** |
| **Biais / discrimination** | Scoring défavorable selon âge/sexe | **Tests d'équité** (equalized odds), atténuation, documentation |
| **Opacité** | Décision non comprise par l'opérateur | **Explicabilité SHAP** par prédiction |
| **Sur-confiance** | Opérateur suit aveuglément le modèle | Human-in-the-loop, formation, affichage de l'incertitude |
| **Dérive** | Perte de fiabilité dans le temps | Monitoring Evidently + réentraînement |

---

## 5. Volet 4 — Mesures et plan d'action

| Mesure | Statut | Responsable |
|---|---|---|
| Pseudonymisation dès la Silver | À implémenter | Architecte IA |
| Agrégation géographique anti-ré-identification | À implémenter | Architecte IA |
| Chiffrement repos + transit | À implémenter | RSSI |
| Contrôle d'accès moindre privilège | À implémenter | RSSI |
| Tests d'équité et atténuation des biais | À implémenter | Architecte IA |
| Explicabilité SHAP exposée via l'API | À implémenter | Architecte IA |
| Human-in-the-loop garanti | Conception | Architecte IA / Métier |
| Monitoring dérive (Evidently) | À implémenter | Architecte IA |
| Sauvegardes testées + plan de reprise | À implémenter | RSSI |
| Registre des prédictions (12 mois) | À implémenter | Architecte IA |

---

## 6. Avis du DPO et validation

| Élément | Statut |
|---|---|
| Risque résiduel global | **Acceptable** sous réserve de la mise en œuvre du plan d'action (§5) |
| Décision automatisée art. 22 | Écartée (human-in-the-loop) |
| Avis du DPO | Favorable avec réserves (suivi du plan d'action) |
| Réexamen de l'AIPD | À chaque évolution majeure du traitement ou du modèle |

---

## 7. Références

- [Plan de gouvernance GRAVIA](Gouvernance_GRAVIA.md) · [CDC GRAVIA](CDC_GRAVIA.md) · [Architecture GRAVIA](Architecture_GRAVIA.md)
- RGPD — art. 9, 22, 35 · Méthodologie AIPD CNIL · Liste CNIL des traitements soumis à AIPD
