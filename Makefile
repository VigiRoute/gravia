.PHONY: help dev down test test-unit test-integration cov lint format validate-data tf-localstack

help:  ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

dev:  ## Démarrer la stack dev (Docker Compose)
	docker compose -f infra/docker-compose.yml up -d

down:  ## Arrêter la stack dev
	docker compose -f infra/docker-compose.yml down

test:  ## Lancer tous les tests
	pytest -v

test-unit:  ## Tests unitaires
	pytest tests/unit/ -v

test-integration:  ## Tests d'intégration (Docker requis)
	pytest tests/integration/ -v

cov:  ## Couverture de tests
	pytest --cov=gravia --cov-report=html --cov-report=term

lint:  ## Linting (ruff)
	ruff check src/ ml/ tests/

format:  ## Formatage (black)
	black src/ ml/ tests/

validate-data:  ## Validation Great Expectations
	python -m gravia.validate

tf-localstack:  ## Terraform apply contre LocalStack
	cd infra/terraform && terraform init && terraform apply -auto-approve
