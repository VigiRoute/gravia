# Cahier des charges — GRAVIA

> **Projet** : GRAVIA — Aide à la décision pour la priorisation des secours routiers par prédiction de la gravité des accidents
> **Organisation (fictive)** : VigiRoute — opérateur d'intérêt public (partenariat type ONISR + services de secours)
> **Version** : 0.1 (brouillon initial)
> **Date** : 2026-06-29
> **Titre RNCP visé** : Architecte en Intelligence Artificielle
> **Blocs couverts** : ce document sert de socle aux Blocs 1 (gouvernance), 2 (architecture), 3 (pipelines) et 4 (solution d'IA).

---

## 1. Contexte et enjeux

Chaque année en France, les accidents corporels de la circulation mobilisent massivement les services de secours (SAMU, pompiers). La rapidité et le bon dimensionnement de la réponse (nombre de véhicules, médicalisation, hélicoptère) conditionnent directement le pronostic vital des victimes.

Aujourd'hui, la décision de priorisation au moment du signalement repose sur l'appréciation humaine de l'opérateur, à partir d'informations incomplètes (lieu, heure, type de choc, météo). Une aide à la décision capable d'estimer la **gravité probable** d'un accident dès son signalement permettrait :

- de **prioriser** l'envoi des moyens lourds vers les accidents les plus graves ;
- de **pré-positionner** les ressources sur les zones et créneaux à risque ;
- d'objectiver et de tracer les décisions de dispatching.

Le projet exploite la base ouverte **BAAC** (Bases de données annuelles des accidents corporels de la circulation), qui décrit ~20 ans d'accidents avec leurs circonstances et la gravité constatée des victimes.

---

## 2. Problème métier et objectifs

### 2.1 Problème métier
> À la remontée d'un signalement d'accident, **estimer la gravité probable** des victimes afin d'aider l'opérateur de secours à prioriser et dimensionner la réponse.

### 2.2 Objectifs

**Objectifs métier**
- Réduire le délai de mobilisation des moyens adaptés aux accidents graves.
- Fournir une estimation **explicable** (l'opérateur doit comprendre pourquoi un accident est jugé grave).
- Garantir une décision **équitable** (pas de discrimination selon l'âge ou le sexe des personnes impliquées).

**Objectifs IA**
- Entraîner un modèle de **classification de la gravité** d'un accident à partir de ses caractéristiques au moment du signalement.
- Exposer le modèle via une **API temps réel** (`/v1/predict-severity`).
- Industrialiser l'ensemble (pipeline de données, CI/CD, réentraînement, monitoring de dérive).

---

## 3. Définition de la cible IA

La base BAAC code la gravité par usager en 4 niveaux : *indemne*, *blessé léger*, *blessé hospitalisé*, *tué*.

**Choix retenu** : cible **binaire au niveau de l'accident** —
- `grave` = au moins une victime *hospitalisée* ou *tuée* ;
- `non grave` = sinon (indemnes / blessés légers uniquement).

**Granularité : une prédiction par accident, pas par personne.**
Le modèle produit **une seule prédiction par accident** (la situation globale), et **non** un pronostic individuel pour chaque victime. Les données par usager servent uniquement à *construire l'étiquette*, jamais à prédire personne par personne.

| Étape | Grain |
|---|---|
| Construction du label (entraînement) | par **usager** (`grav` 1–4), agrégé en « au moins un grave » |
| Entrée du modèle (features) | par **accident** (contexte : véhicules, route, météo…) |
| Prédiction (sortie) | par **accident** (`grave` / `non grave`) |

Ce choix découle du besoin métier (l'opérateur dimensionne les secours pour l'accident dans son ensemble) et de la réalité du signalement (les individus ne sont pas connus en détail à cet instant). Une prédiction au grain *usager* serait un autre problème, plus complexe et sans valeur ajoutée pour le triage — éventuelle piste d'extension, hors périmètre.

**Justification du choix binaire** : pour un usage de triage, la décision opérationnelle est binaire (« envoyer des moyens lourds ou non ») ; le regroupement binaire stabilise aussi l'apprentissage face au fort déséquilibre des classes et aux variations historiques de définition du « blessé hospitalisé ».

**Pourquoi ne pas retenir le multi-classes (4 niveaux) ?**
- **Adéquation au besoin** : la décision métier visée est binaire. Une granularité à 4 niveaux dépasserait le besoin opérationnel (sur-ingénierie au regard de l'usage de triage).
- **Déséquilibre** : la classe `tué` est très minoritaire (quelques %), ce qui dégrade fortement l'apprentissage et la fiabilité des prédictions sur cette classe — au détriment de la robustesse globale.
- **Performance défendable** : une cible binaire permet d'atteindre des métriques plus stables et plus crédibles, alors qu'un 4-classes ferait mécaniquement chuter le F1 macro.
- **Qualité des labels** : la définition du « blessé hospitalisé » a évolué dans BAAC selon les millésimes ; la regrouper avec `tué` dans une cible `grave` réduit ce bruit d'étiquetage.
- **Simplicité d'agrégation** : « au moins une victime grave » est une règle d'agrégation au niveau accident claire et non ambiguë, contrairement au choix d'une classe représentative en 4 niveaux.

**Variante (extension)** : une classification multi-classes à 4 niveaux — en exploitant le caractère **ordinal** des classes — reste envisageable comme approfondissement ultérieur, sans remettre en cause la cible binaire principale.

> ✅ **Décision validée** : cible **binaire** `grave`/`non grave`. La variante multi-classes reste une extension possible.

---

## 4. Parties prenantes

| Partie prenante | Rôle |
|---|---|
| Direction VigiRoute | Sponsor, arbitrages, ROI |
| Opérateurs de régulation des secours | Utilisateurs finaux de l'aide à la décision |
| Délégué à la protection des données (DPO) | Conformité RGPD, AIPD |
| Équipe Data / IA (architecte IA) | Conception, développement, déploiement |
| ONISR / fournisseur de données | Source BAAC, qualité des données |
| CNIL (autorité) | Référent réglementaire externe |
| Responsable sécurité (RSSI) | ISO 27001 / ANSSI, sécurité de l'infrastructure |

---

## 5. Périmètre

**Inclus**
- Ingestion et traitement des données BAAC + enrichissements (météo, géolocalisation, trafic).
- Entraînement, évaluation, explicabilité et test d'équité du modèle.
- API de prédiction temps réel.
- Pipeline automatisé, CI/CD, réentraînement, monitoring (qualité + dérive).
- Plan de gouvernance des données et AIPD.

**Exclu**
- Intégration réelle aux systèmes opérationnels des SAMU/pompiers (hors maquette).
- Décision automatique sans opérateur humain (le modèle **assiste**, ne décide pas — *human-in-the-loop*).
- Données nominatives d'identité (la base BAAC n'en contient pas ; aucune ré-identification ne sera tentée).

---

## 6. Données

### 6.1 Sources

| Source | Contenu | Nature | Fréquence |
|---|---|---|---|
| **BAAC** (data.gouv.fr) | 4 tables : `caractéristiques`, `lieux`, `véhicules`, `usagers` (~2005→2024) | Structuré | Annuelle (millésime) |
| Météo-France / Open-Meteo | Conditions météo au lieu/heure | Semi-structuré | Historique + temps réel |
| BAN + OpenStreetMap | Réseau routier, type de voie | Géospatial | Référentiel |
| **État de circulation temps réel** (RRN + métropoles, DATEX II) | Débit, vitesse, taux d'occupation (3 000+ points) | Semi-structuré (XML) | **Temps réel (1–6 min), haute fréquence** |
| **Signalements** (à scorer) | Accidents entrants à classer à la volée | Flux d'événements | **Simulé par rejeu du BAAC** (voir §6.4) |

> **Bulletins d'incidents (texte)** — retirés du périmètre. L'exploration (§13.6) n'a identifié aucune source réelle correspondante ; la mention initiale provenait d'un mauvais étiquetage d'une source de comptage trafic structurée, pas d'un flux texte. La variété "non structurée" du dataset n'est donc plus démontrée à ce stade — à re-sourcer ou à retirer du discours 3V si aucune source texte n'est identifiée par ailleurs.

### 6.2 Volumétrie
- Ordre de grandeur : plusieurs millions de lignes `usagers` sur ~20 ans (~50–60k accidents/an).

### 6.3 Données personnelles et sensibles
- **Données personnelles** : âge, sexe, géolocalisation précise, motif de trajet.
- **Données sensibles (art. 9 RGPD)** : la **gravité = donnée de santé**.
- **Risque principal** : ré-identification par croisement `lat/long` + `date` + `commune`.
- **Mesures** : pseudonymisation et agrégation géographique dès la couche Silver, minimisation dès l'ingestion, **AIPD obligatoire** (données de santé + scoring + grande échelle).

### 6.4 Origine du flux temps réel

Le BAAC est une source **batch** (publiée ~2 fois/an) : ce n'est **pas** une source temps réel. La source opérationnelle réelle des signalements d'accidents (régulation des secours 15 / 18 / 112) **n'est pas accessible en open data**.

En conséquence, le flux de **signalements** est alimenté par un **simulateur de rejeu** (*replay*) : un producteur lit les enregistrements BAAC et les réinjecte dans le bus de messages (Redpanda/Kafka) avec un horodatage, comme s'ils arrivaient en direct. En revanche, les **données de trafic temps réel** (état de circulation, DATEX II — milliers de mesures/min) et la **météo** (Open-Meteo) proviennent de **véritables flux temps réel** : le **trafic constitue le flux haute fréquence** qui justifie le bus de messages, tandis que les signalements en sont les événements (peu fréquents) à scorer.

> **Choix d'architecture assumé.** L'architecture temps réel (bus de messages, enrichissement, inférence) est **réelle et fonctionnelle** ; seule la *source* des signalements est simulée. En production, le simulateur serait remplacé par le **feed réel de l'opérateur** (Kafka managé).

---

## 7. Exigences fonctionnelles

| Réf. | Exigence |
|---|---|
| EF-1 | Le système ingère les millésimes BAAC et les enrichit (météo, géo, trafic). |
| EF-2 | Le système nettoie, pseudonymise et structure les données (Bronze → Silver → Gold). |
| EF-3 | Le modèle prédit la gravité (`grave` / `non grave`) à partir des caractéristiques d'un accident. |
| EF-3b | **Plusieurs modèles sont comparés (benchmark)** — régression logistique (baseline), Random Forest, gradient boosting (LightGBM/XGBoost) — et le **modèle final est retenu en fonction des résultats** (métriques du §11), avec suivi des expériences dans MLflow. |
| EF-4 | Le modèle renvoie un **score de confiance** et une **explication** (contributions des variables, SHAP). |
| EF-5 | Une **API REST** expose la prédiction en temps réel (`POST /v1/predict-severity`). |
| EF-6 | Le système réentraîne le modèle sur nouveau millésime ou sur détection de dérive. |
| EF-7 | Le système journalise les prédictions pour audit et traçabilité. |

---

## 8. Exigences non-fonctionnelles

| Réf. | Exigence | Cible (à affiner après baseline) |
|---|---|---|
| ENF-1 | Latence API (p95) | < 300 ms |
| ENF-2 | Disponibilité API | ≥ 99,5 % / mois |
| ENF-3 | Scalabilité | Traitement du volume historique + montée en charge des appels temps réel |
| ENF-4 | Sécurité | Chiffrement au repos et en transit, gestion des accès (moindre privilège) |
| ENF-5 | Reproductibilité | Versioning code + données + modèle (Git, MLflow), graines fixées |
| ENF-6 | Couverture de tests | ≥ 80 % |
| ENF-7 | Observabilité | Monitoring infra + qualité données + dérive modèle |

---

## 9. Conformité, RGPD et éthique

| Réf. | Exigence |
|---|---|
| EC-1 | **AIPD/DPIA** réalisée (traitement de données de santé à grande échelle avec scoring). |
| EC-2 | Base légale documentée : mission d'intérêt public (art. 6.1.e) + intérêt public en santé (art. 9.2.i). |
| EC-3 | Minimisation : seules les variables nécessaires sont conservées ; pseudonymisation dès la Silver. |
| EC-4 | Durées de conservation définies ; procédures de droit d'accès / rectification / suppression. |
| EC-5 | Référentiels : RGPD, Loi Informatique et Libertés, ISO 27001, recommandations ANSSI. |
| EC-6 | **IA éthique** : tests d'équité (parité selon âge/sexe, *equalized odds*), documentation et atténuation des biais. |
| EC-7 | **Human-in-the-loop** : le modèle assiste l'opérateur, ne prend pas de décision autonome. |
| EC-8 | Explicabilité fournie pour chaque prédiction (SHAP). |
| EC-9 | **HDS (Hébergement de Données de Santé)** : non requis sur le périmètre actuel (données ouvertes/pseudonymisées) ; un **hébergeur certifié HDS** (art. L1111-8 CSP) serait requis en production traitant des données réelles de victimes. |

---

## 10. Accessibilité

- Tous les livrables documentaires sont fournis dans des formats accessibles (structure de titres, contrastes, alternatives textuelles aux schémas).
- L'éventuelle interface de consultation respecte les bonnes pratiques d'accessibilité (RGAA).
- La documentation est vulgarisée pour être compréhensible par un public non spécialiste.

---

## 11. Métriques et critères de succès

| Métrique | Seuil cible | Justification | Action si non atteint |
|---|---|---|---|
| **Recall classe `grave`** | ≥ 0,80 | Un faux négatif (grave classé non grave) est le risque le plus coûteux | Revoir features / rééquilibrage |
| **F1-score macro** | ≥ 0,70 | Cible réaliste sur tâche déséquilibrée | Bloquer la promotion en production |
| **PR-AUC** | suivi (pas de seuil bloquant) | Adaptée au déséquilibre | — |
| **Latence API p95** | < 300 ms | Usage temps réel | Optimisation serving |
| **Équité (écart de taux par groupe)** | écart maîtrisé et documenté | IA éthique / non-discrimination | Atténuation du biais |
| **PSI (dérive)** | < 0,2 | Détection de dérive des données | Déclencher réentraînement |
| **Couverture de tests** | ≥ 80 % | Qualité logicielle | Bloquer la PR |

> Les seuils de performance sont des **cibles provisoires assumées** : le **recall `grave` (0,80)** traduit le coût élevé d'un faux négatif (cas grave manqué), le **F1 macro (0,70)** une cible réaliste sur tâche déséquilibrée. Ils seront **recalibrés à l'issue du benchmark** — la valeur finale étant justifiée par les résultats mesurés, jamais fixée arbitrairement.

---

## 12. Architecture cible (haut niveau)

Le détail relève des Blocs 2 et 3 ; principes directeurs ici :

- **Architecture Medallion** : Bronze (brut) → Silver (nettoyé, pseudonymisé, enrichi) → Gold (features model-ready, schéma en étoile).
- **Deux environnements** : dev local (gratuit) et prod cloud — le choix précis des technologies sera arbitré dans le document d'architecture.
- **Orchestration** automatisée ; **tracking** des expériences et **registry** des modèles ; **serving** API ; **monitoring** infra + données + dérive.

---

## 13. Hypothèses et décisions à valider

1. ✅ **Cible** : binaire `grave`/`non grave` — **validé**. Multi-classes 4 niveaux = extension possible.
2. ✅ **Périmètre géographique** : **France entière** — validé (volumétrie maîtrisable ; repli sur un sous-ensemble seulement si contrainte technique avérée).
3. ✅ **Seuils de performance (définition)** : cibles provisoires assumées, à recalibrer après baseline — validé. Le baseline confirme les valeurs cibles à l'échelle nationale, mais révèle une tension d'application par sous-groupe : voir item 7 ci-dessous, non close.
4. ✅ **Scénario temps réel** : flux alimenté par un **simulateur de rejeu du BAAC** (+ météo/trafic réels) — choix d'architecture **assumé et documenté** (§6.4).
5. **Stack technique** dev/prod : à arbitrer dans le document d'architecture (Bloc 2).
6. ✅ **Enrichissements — trafic testé et écarté comme feature, bulletins retirés** : le **trafic** (DATEX II national + capteurs Paris) a été exploré et testé en modèle (jointure, corrélation statistique, gain prédictif mesuré avec/sans la feature, en modèle dédié Paris puis en configuration nationale sparse). Résultat : signal statistique réel mais **gain prédictif nul** une fois intégré à un modèle multivarié qui a déjà accès à l'heure/jour/mois — **écarté comme enrichissement du modèle**. Le flux temps réel DATEX reste pertinent, mais pour une raison **opérationnelle propre et non pour le modèle** : afficher l'état de circulation aux opérateurs de régulation pour optimiser le routage des secours (temps de trajet), un cas d'usage distinct du scoring de gravité. C'est cette valeur opérationnelle, et non un besoin du modèle IA, qui justifie l'ingestion temps réel et le bus de messages (cf. Architecture §2.1) — à défaut, le bus de messages reposerait artificiellement sur le seul volume de signalements (~150/jour), largement insuffisant pour le justifier. Les **bulletins d'incidents** sont **retirés** : aucune source réelle identifiée après recherche — validé.
7. **Seuils de performance — tension identifiée entre recall et F1 macro** : le baseline BAAC (sans enrichissement) atteint les seuils nationaux agrégés (recall grave 0,808, F1 macro 0,708), mais un seuil de décision unique masque un angle mort : recall de seulement 0,007 sur le sous-ensemble parisien (taux de gravité structurellement plus faible, ~9 % vs ~36 % national). Calibrer des seuils différenciés par zone répare le recall local (jusqu'à 0,777 par département) mais fait chuter le F1 macro national sous le seuil CDC (jusqu'à 0,573) — **les deux seuils ne sont pas simultanément atteignables avec le modèle actuel par simple calibration de seuil**. À trancher avant mise en production : enrichir les features pour les contextes à faible taux de base, et/ou arbitrer explicitement la priorité entre recall local et F1 macro global (cf. §14 Risques).

---

## 14. Risques

| Risque | Impact | Mitigation |
|---|---|---|
| Fort déséquilibre des classes | Modèle qui ignore les cas graves | Pondération / rééchantillonnage, métriques adaptées |
| Ré-identification des victimes | Violation RGPD | Pseudonymisation, agrégation géo, AIPD |
| Biais discriminatoire du scoring | Décision inéquitable | Tests d'équité, atténuation, human-in-the-loop |
| Variété initialement tabulaire | Architecture moins riche | Enrichissement semi-structuré réel (météo, trafic XML DATEX en temps réel) ; testé comme feature d'entraînement mais écarté (gain nul, cf. §13.6) — la variété non structurée (bulletins) n'a pas de source réelle identifiée |
| Source des signalements simulée (rejeu) | Crédibilité B3 | Flux **trafic temps réel natif** (DATEX) comme charge réelle ; rejeu des signalements assumé |
| Dérive du parc (trottinettes/EDP) | Perte de performance | Monitoring de dérive + réentraînement |
| Seuil de décision unique masquant un angle mort local (recall quasi nul sur des zones à faible taux de gravité de base, ex. Paris ~9 % vs national ~36 %) | Système peu sûr localement malgré un recall national conforme | Vérifier le recall par sous-groupe (zone/dep) avant mise en production, pas seulement l'agrégat national ; arbitrer explicitement recall local vs F1 macro global si les deux seuils ne sont pas simultanément atteignables (cf. §13.7) |

---

## 15. Livrables (alignés au référentiel)

- Plan de gouvernance des données + AIPD (Bloc 1).
- Document et diagramme d'architecture + code IaC (Bloc 2).
- Pipeline de données automatisé + contrôle qualité + monitoring (Bloc 3).
- Solution d'IA : modèle, API, CI/CD, réentraînement, monitoring de dérive (Bloc 4).
- Présentations (slides) et captures vidéo des composants en production.
- Dépôt Git documenté.

---

## 16. Références

- Référentiel RNCP « Architecte en Intelligence Artificielle » — `docs/referentiel.md` (projet LEXIA d'origine)
- Dataset BAAC — https://www.data.gouv.fr/fr/datasets/bases-de-donnees-annuelles-des-accidents-corporels-de-la-circulation-routiere-annees-de-2005-a-2024/
- RGPD — Règlement (UE) 2016/679 (art. 6, 9, 35)
