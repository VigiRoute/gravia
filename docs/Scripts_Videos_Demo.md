# Scripts des 3 vidéos de démonstration (CDC §15)

Livrable manquant identifié en croisant `docs/referentiel.md` et `docs/AVANCEMENT_GRAVIA.md` :
une capture vidéo par bloc (infrastructure, pipeline, solution IA) montrant le composant **en
fonctionnement réel**, pas un plan statique. Ce document donne un déroulé minute par minute pour
chacune, écrit pour être suivi pendant l'enregistrement.

**État constaté au moment de la rédaction (à revérifier avant de filmer)** : tout tourne déjà —
stack dev `gravia` complète, LocalStack avec Terraform appliqué (14 ressources), cluster `kind`
avec 2 pods `serving` `Running`. Pas besoin de tout redéployer depuis zéro pour ces vidéos ; les
commandes de redéploiement complet sont données en annexe si tu préfères montrer le cycle complet.

Convention : **MONTRE** = ce qui doit être à l'écran, **DIS** = ce que tu dis (script indicatif,
à reformuler avec tes mots — parle comme tu expliquerais ton projet à un pote, pas comme un
rapport d'audit).

---

## Vidéo 1 — Infrastructure (Bloc 2)

**Répond à** : « Une capture d'écran vidéo de l'infrastructure en production. »
**Durée cible** : 3-4 min. **Terminal** : ouvre-le dans `gravia-mlops/`.

### 1. Terraform / LocalStack (~1 min 30)

**MONTRE** : terminal, commande en direct.
```bash
docker exec gravia-mlops-localstack-1 awslocal s3 ls
docker exec gravia-mlops-localstack-1 awslocal ec2 describe-vpcs --region eu-west-1 --query "Vpcs[].{Id:VpcId,Cidr:CidrBlock}"
docker exec gravia-mlops-localstack-1 awslocal secretsmanager get-secret-value --region eu-west-1 --secret-id gravia-dev-db-credentials --query SecretString --output text
```
**DIS** : « Ça c'est mon infra AWS, mais tournée vers LocalStack plutôt qu'un vrai compte AWS —
j'ai pas de budget cloud, donc je simule. Le code Terraform est exactement le même que si je
ciblais un vrai AWS, juste l'endpoint qui change. Là je vérifie pas juste que Terraform dit que
ça a marché, je vais interroger LocalStack directement : mon bucket S3 existe, mon VPC existe
avec ses sous-réseaux, et mon secret pour la base de données est bien dedans. »

**MONTRE** : `cd terraform && terraform plan` (doit afficher "No changes").
**DIS** : « Et si je relance un plan, aucun changement détecté — ça prouve que ce qui tourne
correspond exactement à ce que dit mon code, pas de dérive. »

### 2. Kubernetes / kind (~2 min)

**MONTRE** : terminal.
```bash
kubectl --context kind-gravia get pods -n gravia
kubectl --context kind-gravia get svc -n gravia
```
**DIS** : « Là c'est mon cluster Kubernetes, pas un vrai EKS parce que ça coûte cher, mais un
vrai cluster quand même — `kind`, qui fait tourner Kubernetes dans des conteneurs Docker. Deux
pods de mon API tournent dessus. »

**MONTRE** : `kubectl --context kind-gravia port-forward -n gravia service/gravia-serving 8001:8000` (dans un terminal séparé), puis dans le navigateur ou un autre terminal :
```bash
curl http://localhost:8001/health
```
**DIS** : « Je fais un tunnel vers le cluster et j'interroge mon API qui tourne dedans — elle
répond, le modèle est chargé. »

**MONTRE** (la partie la plus parlante) : supprime un pod en direct.
```bash
kubectl --context kind-gravia delete pod -n gravia <nom-du-premier-pod-affiché-plus-haut>
kubectl --context kind-gravia get pods -n gravia --watch
```
**DIS** : « Et là je tue un pod exprès, pour montrer l'auto-guérison — Kubernetes le redémarre
tout seul, et pendant ce temps le service reste dispo via l'autre pod. » (laisse tourner le
`--watch` 10-15 secondes pour voir le nouveau pod passer `Running`, puis `Ctrl+C`.)

**Optionnel, si tu as le temps** : `kubectl --context kind-gravia scale deployment/gravia-serving -n gravia --replicas=3` puis retour à `--replicas=2`, pour montrer le scaling.

---

## Vidéo 2 — Pipeline de données (Bloc 3)

**Répond à** : « Une capture d'écran vidéo du pipeline en production. »
**Durée cible** : 3-4 min. **Navigateur** : Airflow (http://localhost:8080), MinIO
(http://localhost:9001), Adminer (http://localhost:8081).

⚠️ Le DAG traite 5 millésimes (2019-2023) en parallèle — ça prend quelques minutes de bout en
bout. Déclenche-le **avant** de lancer l'enregistrement pour ne pas avoir de temps mort, ou coupe
l'attente au montage.

### 1. Vue d'ensemble du DAG (~1 min)

**MONTRE** : Airflow → DAG `etl_medallion_baac` → vue Grid.
**DIS** : « Voilà mon pipeline de données, Bronze → Silver → Quality → Gold, un groupe de tâches
par millésime BAAC, ils tournent en parallèle parce qu'ils sont indépendants. »

**MONTRE** : clique sur un run récent (vert = succès), puis vue Graph d'un groupe `millesime_2023`.
**DIS** : « Bronze ingère le CSV brut tel quel, Silver le nettoie et pseudonymise les données
personnelles, Quality vérifie que tout est conforme avant de laisser passer, et Gold construit le
schéma en étoile final. Si Quality échoue, Gold ne se lance pas — ça bloque la promotion d'un lot
pas fiable. »

### 2. Déclenchement en direct (~1 min, si tu ne l'as pas déjà lancé avant)

**MONTRE** : bouton "Trigger DAG" dans Airflow, puis la vue Grid qui se met à jour.
**DIS** : « Je le relance en direct pour montrer que c'est pas un screenshot figé. » (bascule
ensuite sur un run déjà terminé si l'attente est trop longue, en le disant à l'oral : « pendant
que ça tourne, je vous montre un run précédent complet. »)

**MONTRE** : ouvre les logs d'une tâche `quality`.
**DIS** : « Là c'est Great Expectations qui valide les données — types, valeurs attendues, pas de
doublons — avant que Gold ne les joigne. »

### 3. Le résultat dans les trois couches (~1 min 30)

**MONTRE** : console MinIO → bucket `gravia` → `bronze/baac/...` puis `silver/baac/...`.
**DIS** : « Et voilà le résultat physique : le brut en Bronze, le nettoyé/pseudonymisé en Silver,
en Parquet sur mon stockage objet. »

**MONTRE** : Adminer → base `gravia` → `gold_fact_accident` (clique pour voir les lignes réelles), puis "Schéma de la base de données" pour le diagramme ER.
**DIS** : « Et Gold, la couche finale, en PostgreSQL, schéma en étoile — une table de faits et ses
dimensions, prête pour le modèle. »

---

## Vidéo 3 — Solution IA (Bloc 4)

**Répond à** : « Une capture d'écran vidéo de la solution d'IA fonctionnant en production. »
**Durée cible** : 4-5 min. **Navigateur** : MLflow (http://localhost:5000), démo API
(http://localhost:8000/demo), Grafana (http://localhost:3000). **Terminal** pour la dérive.

### 1. Le modèle dans MLflow (~1 min)

**MONTRE** : MLflow → expérience `gravia-severity-classifier` → onglet "Models" → version avec
l'alias `staging`.
**DIS** : « Mon modèle, LightGBM, versionné et suivi dans MLflow. Celui-là, avec l'alias
"staging", c'est celui réellement chargé par mon API en ce moment. Je peux voir ses métriques —
recall, F1 macro — et comparer avec les autres runs que j'ai trackés. »

### 2. Prédiction en direct (~2 min)

**MONTRE** : navigateur sur `/demo`, remplis le formulaire (garde les valeurs par défaut ou
change-en une pour rendre ça vivant), clique "Lancer la prédiction".
**DIS** : « Voilà mon API en action. Je décris un accident — ici collision frontale, 90 km/h — et
le modèle me sort une probabilité de gravité, avec le seuil de décision, et l'explication SHAP :
qu'est-ce qui a poussé la prédiction dans un sens ou dans l'autre. Tout ça en moins de 300 ms,
c'est une contrainte du cahier des charges parce que ça doit rester utilisable en temps réel par
un opérateur de secours. »

**Optionnel, fort à l'oral** : change juste le département (75 → un département rural) et relance,
pour montrer la limite du seuil unique que tu as documentée (angle mort) — assume-le à l'oral
plutôt que de le cacher, ça montre de la rigueur.

### 3. Monitoring (~1 min 30)

**MONTRE** : Grafana → dashboard « GRAVIA — API de prédiction » (panels p50/p95, taux d'erreur,
total requêtes, API en ligne).
**DIS** : « Et ça, c'est le monitoring de l'API en production — latence, débit, taux d'erreur,
avec des alertes configurées si ça dérive du seuil du cahier des charges. »

**MONTRE** : terminal.
```bash
python -m ml.monitoring.drift
```
**DIS** : « Et là je vérifie la dérive des données — est-ce que les accidents récents ressemblent
statistiquement à ceux sur lesquels j'ai entraîné le modèle. Si ça dérive trop, ça déclenche un
réentraînement. »

**MONTRE** (si le temps le permet) : GitHub → onglet Actions du dépôt `gravia` → workflow
`retrain.yml`, ou `gravia-mlops` → workflow `deploy.yml`.
**DIS** : « Et ça c'est mon réentraînement planifié et mon déploiement continu, automatisés en
CI/CD — pas besoin d'intervention manuelle pour remettre un modèle à jour. »

---

## Annexe — redéployer depuis zéro (si tu préfères montrer le cycle complet)

Commandes vérifiées, détail complet dans `gravia-mlops/docs/Deploiement_GRAVIA_MLOps.md` :

```bash
# Terraform / LocalStack
cd gravia-mlops
docker compose -f terraform/docker-compose.localstack.yml up -d
cd terraform && cp terraform.tfvars.example terraform.tfvars
terraform init && terraform plan -out=tfplan && terraform apply tfplan

# Kubernetes / kind
docker compose -f ../gravia/infra/docker-compose.yml --env-file ../gravia/.env build serving
kind create cluster --name gravia --config ../k8s/kind-config.yaml
kind load docker-image gravia-serving:python3.12 --name gravia
cp ../k8s/serving-secret.example.yaml ../k8s/serving-secret.yaml
kubectl apply -f ../k8s/namespace.yaml -f ../k8s/serving-configmap.yaml -f ../k8s/serving-secret.yaml -f ../k8s/serving-deployment.yaml -f ../k8s/serving-service.yaml
```

Compte ~1-2 min pour que les pods chargent le modèle (`kubectl get pods -n gravia --watch`).
