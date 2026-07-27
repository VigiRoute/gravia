.PHONY: help dev down restart ps logs test test-unit test-integration cov lint format validate-data

# Le fichier compose vit dans infra/ mais le `.env` est à la racine : sans --env-file,
# Docker Compose chercherait le `.env` à côté du compose et ne le trouverait pas.
# `-` devant le chemin : la stack démarre même sans `.env` (toutes les variables ont
# une valeur par défaut dans le compose).
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env

help:  ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

dev:  ## Démarrer la stack dev (Docker Compose)
	@test -f .env || (echo "Pas de .env — copie depuis .env.example" && cp .env.example .env)
	$(COMPOSE) up -d
	@echo ""
	@echo "  Airflow     http://localhost:8080  (admin / admin)"
	@echo "  MLflow      http://localhost:5000"
	@echo "  MinIO       http://localhost:9001  (minioadmin / minioadmin)"
	@echo "  Grafana     http://localhost:3000  (admin / admin)"
	@echo "  Prometheus  http://localhost:9090"

down:  ## Arrêter la stack dev
	$(COMPOSE) down

restart:  ## Redémarrer la stack dev
	$(COMPOSE) restart

ps:  ## État des services
	$(COMPOSE) ps

logs:  ## Suivre les logs (make logs S=airflow-scheduler pour un service)
	$(COMPOSE) logs -f $(S)

test:  ## Lancer tous les tests
	pytest -v

test-unit:  ## Tests unitaires
	pytest tests/unit/ -v

test-integration:  ## Tests d'intégration (Docker requis)
	pytest tests/integration/ -v

cov:  ## Couverture de tests
	pytest --cov=gravia --cov-report=html --cov-report=term

lint:  ## Linting (ruff)
	ruff check src/ ml/ tests/ notebooks/

format:  ## Formatage (black)
	black src/ ml/ tests/ notebooks/

validate-data:  ## Validation Great Expectations
	python -m gravia.validate

# NB : le Terraform (LocalStack -> AWS) vit dans le dépôt VigiRoute/gravia-mlops,
# pas ici (cf. Architecture_GRAVIA.md §6.3). La cible `tf-localstack` a donc été
# retirée de ce Makefile : elle appartient au Makefile de gravia-mlops.
