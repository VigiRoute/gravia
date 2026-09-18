-- Schéma en étoile Gold — cf. docs/Architecture_GRAVIA.md §4.2.
-- Grain de gold_fact_accident : un accident. Chargé par src/gravia/gold.py à partir des
-- 4 tables Silver (caracteristiques, lieux, vehicules, usagers), jointes ici pour la première
-- fois dans le pipeline (Bronze et Silver ne font aucune jointure inter-table).
--
-- Les colonnes naturelles des dimensions ne contiennent jamais NULL : les codes manquants sont
-- normalisés à -1 (convention déjà utilisée par le BAAC lui-même) avant chargement, pour que la
-- contrainte UNIQUE ci-dessous serve de clé de déduplication fiable (PostgreSQL ne déduplique
-- pas des colonnes NULL sous UNIQUE/ON CONFLICT).

CREATE TABLE IF NOT EXISTS gold_dim_date (
    date_key        SERIAL PRIMARY KEY,
    jour            DATE NOT NULL,
    heure           SMALLINT NOT NULL,
    jour_semaine    SMALLINT NOT NULL,     -- ISO 8601 : 1 = lundi .. 7 = dimanche
    weekend         BOOLEAN NOT NULL,
    mois            SMALLINT NOT NULL,
    jour_ferie      BOOLEAN NOT NULL,      -- jour férié légal France métropolitaine
    UNIQUE (jour, heure)
);

-- Regroupe les attributs structurels du lieu (route/infrastructure), qu'ils viennent de la
-- rubrique BAAC `lieux` ou de `caracteristiques` (`int`, intersection : trait durable du lieu,
-- pas une condition transitoire — cf. gold_dim_conditions pour météo/luminosité/état surface).
-- Étendu le 2026-08-24 : le diagramme d'origine (Architecture_GRAVIA.md, avant tout test) ne
-- prévoyait que 4 attributs, tous ceux ci-dessous sont nécessaires pour reproduire le baseline
-- déjà validé (notebooks/eda_baseline_baac.ipynb, recall 0,808 / F1 macro 0,708).
CREATE TABLE IF NOT EXISTS gold_dim_lieu (
    lieu_key            SERIAL PRIMARY KEY,
    departement         VARCHAR(3) NOT NULL,   -- code INSEE : "2A"/"2B" en Corse, non numérique
    agglomeration       BOOLEAN NOT NULL,
    intersection        SMALLINT NOT NULL,     -- -1 = non renseigné ; caracteristiques.int
    categorie_route     SMALLINT NOT NULL,     -- -1 = non renseigné (cf. dictionnaire ONISR)
    regime_circulation  SMALLINT NOT NULL,     -- -1 = non renseigné ; lieux.circ
    nb_voies            SMALLINT NOT NULL,     -- lieux.nbv
    voie_reservee       SMALLINT NOT NULL,     -- -1 = non renseigné ; lieux.vosp
    profil_route        SMALLINT NOT NULL,     -- -1 = non renseigné ; lieux.prof (déclivité)
    trace_plan          SMALLINT NOT NULL,     -- -1 = non renseigné ; lieux.plan
    vitesse_max         SMALLINT NOT NULL,     -- -1 = non renseigné
    infrastructure      SMALLINT NOT NULL,     -- -1 = non renseigné ; lieux.infra
    situation           SMALLINT NOT NULL,     -- -1 = non renseigné ; lieux.situ
    UNIQUE (
        departement, agglomeration, intersection, categorie_route, regime_circulation, nb_voies,
        voie_reservee, profil_route, trace_plan, vitesse_max, infrastructure, situation
    )
);

CREATE TABLE IF NOT EXISTS gold_dim_conditions (
    conditions_key   SERIAL PRIMARY KEY,
    luminosite       SMALLINT NOT NULL,     -- -1 = non renseigné
    meteo            SMALLINT NOT NULL,     -- -1 = non renseigné
    etat_surface     SMALLINT NOT NULL,     -- -1 = non renseigné
    UNIQUE (luminosite, meteo, etat_surface)
);

CREATE TABLE IF NOT EXISTS gold_dim_collision (
    collision_key    SERIAL PRIMARY KEY,
    type_collision   VARCHAR(60) NOT NULL UNIQUE   -- libellé décodé depuis `col` (dictionnaire ONISR)
);

-- Le schéma initial (Architecture_GRAVIA.md, avant tout test) ne prévoyait que 3 flags
-- (flag_moto / flag_poids_lourd / flag_pieton), jamais implémentés ni testés à l'époque.
-- Les flags ci-dessous suivent la configuration réellement validée empiriquement dans
-- notebooks/eval_enrichissement_vs_seuil.ipynb (meilleure config testée : recall Paris 0,812,
-- F1 macro national 0,609, combinée à un seuil par département au moment du scoring).
CREATE TABLE IF NOT EXISTS gold_fact_accident (
    accident_id             VARCHAR(20) PRIMARY KEY,   -- Num_Acc
    date_key                INTEGER NOT NULL REFERENCES gold_dim_date(date_key),
    lieu_key                INTEGER NOT NULL REFERENCES gold_dim_lieu(lieu_key),
    conditions_key          INTEGER NOT NULL REFERENCES gold_dim_conditions(conditions_key),
    collision_key            INTEGER NOT NULL REFERENCES gold_dim_collision(collision_key),
    nb_vehicules             SMALLINT NOT NULL,
    nb_usagers               SMALLINT NOT NULL,
    flag_2roues_motorise      BOOLEAN NOT NULL,
    flag_poids_lourd          BOOLEAN NOT NULL,
    flag_velo_edp              BOOLEAN NOT NULL,
    flag_pieton                BOOLEAN NOT NULL,
    is_grave                   BOOLEAN NOT NULL,   -- LABEL : au moins un usager grav in {2,3}
    _millesime                  SMALLINT NOT NULL   -- traçabilité gouvernance (cf. Bronze/Silver)
);

CREATE INDEX IF NOT EXISTS idx_fact_accident_date_key ON gold_fact_accident (date_key);
CREATE INDEX IF NOT EXISTS idx_fact_accident_lieu_key ON gold_fact_accident (lieu_key);
CREATE INDEX IF NOT EXISTS idx_fact_accident_conditions_key ON gold_fact_accident (conditions_key);
CREATE INDEX IF NOT EXISTS idx_fact_accident_collision_key ON gold_fact_accident (collision_key);
CREATE INDEX IF NOT EXISTS idx_fact_accident_millesime ON gold_fact_accident (_millesime);
