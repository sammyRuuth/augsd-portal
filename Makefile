# =============================================================================
# AUGSD Portal - Makefile
# =============================================================================
# Usage:
#   make help          - Show this help message
#   make build         - Build Docker images
#   make up            - Start all services
#   make down          - Stop all services
#   make deploy-prod   - Deploy to production
#   make backup-db     - Backup database
#   make backup-logs   - Backup logs
# =============================================================================

.PHONY: help build build-no-cache up down restart logs shell \
        deploy-prod deploy-staging \
        backup-db backup-logs backup-all restore-db \
        init-db manage-users \
        clean clean-volumes clean-all \

# Default target
.DEFAULT_GOAL := help

# Variables
COMPOSE := docker compose
APP_CONTAINER := augsd_portal_app
DB_CONTAINER := augsd_portal_db
IMAGE_NAME := augsd-portal
BACKUP_DIR := backups
LOGS_DIR := logs

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

# =============================================================================
# Help
# =============================================================================

help: ## Show this help message
	@echo ""
	@echo "$(BLUE)AUGSD Portal - Available Commands$(NC)"
	@echo "========================================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

# =============================================================================
# Docker Build Commands
# =============================================================================

build: ## Build Docker images
	@echo "$(BLUE)Building Docker images...$(NC)"
	$(COMPOSE) build
	@echo "$(GREEN)✓ Build complete$(NC)"

build-no-cache: ## Build Docker images without cache
	@echo "$(BLUE)Building Docker images (no cache)...$(NC)"
	$(COMPOSE) build --no-cache
	@echo "$(GREEN)✓ Build complete$(NC)"

build-prod: ## Build production-optimized image
	@echo "$(BLUE)Building production image...$(NC)"
	docker build --target production -t $(IMAGE_NAME):latest .
	docker tag $(IMAGE_NAME):latest $(IMAGE_NAME):$$(date +%Y%m%d)
	@echo "$(GREEN)✓ Production build complete$(NC)"

# =============================================================================
# Docker Compose Commands
# =============================================================================

up: ## Start all services
	@echo "$(BLUE)Starting services...$(NC)"
	$(COMPOSE) up -d
	@echo "$(GREEN)✓ Services started$(NC)"
	@echo "  App:      http://localhost:23090"
	@echo "  Database: localhost:5432"

down: ## Stop all services
	@echo "$(BLUE)Stopping services...$(NC)"
	$(COMPOSE) down
	@echo "$(GREEN)✓ Services stopped$(NC)"

restart: ## Restart all services
	@echo "$(BLUE)Restarting services...$(NC)"
	$(COMPOSE) restart
	@echo "$(GREEN)✓ Services restarted$(NC)"

restart-app: ## Restart only the app service
	@echo "$(BLUE)Restarting app service...$(NC)"
	$(COMPOSE) restart app
	@echo "$(GREEN)✓ App restarted$(NC)"

logs: ## View logs from all services
	$(COMPOSE) logs -f

logs-app: ## View logs from app service
	$(COMPOSE) logs -f app

logs-db: ## View logs from database service
	$(COMPOSE) logs -f db

status: ## Show status of all services
	@echo "$(BLUE)Service Status:$(NC)"
	$(COMPOSE) ps

shell: ## Open a shell in the app container
	$(COMPOSE) exec app /bin/bash

shell-db: ## Open a psql shell in the database container
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-postgres} -d $${POSTGRES_DB:-portal_global}

# =============================================================================
# Production Deployment
# =============================================================================

deploy-prod: build ## Deploy to production
	@echo "$(BLUE)Deploying to production...$(NC)"
	@echo "$(YELLOW)Creating backup before deployment...$(NC)"
	@$(MAKE) backup-db --no-print-directory || echo "$(YELLOW)⚠ Backup skipped (database may not be running)$(NC)"
	$(COMPOSE) down
	$(COMPOSE) up -d
	@echo "$(GREEN)✓ Production deployment complete$(NC)"

# =============================================================================
# Backup Commands
# =============================================================================

backup-db: ## Backup database
	@echo "$(BLUE)Backing up database...$(NC)"
	@mkdir -p $(BACKUP_DIR)
	uv run python scripts/backup_database.py --backup-dir $(BACKUP_DIR)
	@echo "$(GREEN)✓ Database backup complete$(NC)"

backup-logs: ## Backup logs
	@echo "$(BLUE)Backing up logs...$(NC)"
	@mkdir -p $(BACKUP_DIR)/logs_bck
	bash scripts/backup_logs.sh
	@echo "$(GREEN)✓ Logs backup complete$(NC)"

backup-all: backup-db backup-logs ## Backup database and logs
	@echo "$(GREEN)✓ All backups complete$(NC)"

backup-daily: ## Run daily backup (for cron jobs)
	@bash scripts/daily_backup.sh

restore-db: ## Restore database from backup (usage: make restore-db BACKUP=path/to/backup.tar.gz)
	@if [ -z "$(BACKUP)" ]; then \
		echo "$(RED)✗ Error: BACKUP variable not set$(NC)"; \
		echo "  Usage: make restore-db BACKUP=backups/portal_backup_YYYYMMDD_HHMMSS.tar.gz"; \
		exit 1; \
	fi
	@echo "$(YELLOW)⚠ WARNING: This will restore the database from backup$(NC)"
	@read -p "Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	uv run python scripts/restore_database.py $(BACKUP) --confirm

list-backups: ## List available backups
	@echo "$(BLUE)Available backups:$(NC)"
	@uv run python scripts/backup_database.py --list

# =============================================================================
# User Management
# =============================================================================

manage-users: ## Run user management CLI (usage: make manage-users CMD="list")
	@if [ -z "$(CMD)" ]; then \
		echo "$(BLUE)User Management Commands:$(NC)"; \
		echo "  make manage-users CMD=\"list\""; \
		echo "  make manage-users CMD=\"create --role admin --email admin@example.com\""; \
		echo "  make manage-users CMD=\"reset-password --email admin@example.com\""; \
		echo "  make manage-users CMD=\"deactivate --email user@example.com\""; \
		echo "  make manage-users CMD=\"activate --email user@example.com\""; \
	else \
		uv run python scripts/manage_users.py $(CMD); \
	fi

create-admin: ## Create an admin user (usage: make create-admin EMAIL=admin@example.com)
	@if [ -z "$(EMAIL)" ]; then \
		echo "$(RED)✗ Error: EMAIL variable not set$(NC)"; \
		echo "  Usage: make create-admin EMAIL=admin@example.com"; \
		exit 1; \
	fi
	uv run python scripts/manage_users.py create --role admin --email $(EMAIL)

create-default-users: ## Create default users from DEFAULT_USERS env var
	@echo "$(BLUE)Creating default users from .env...$(NC)"
	uv run python scripts/manage_users.py create-from-env
	@echo "$(GREEN)✓ Default users created$(NC)"

list-users: ## List all users
	uv run python scripts/manage_users.py list

# =============================================================================
# Development Commands
# =============================================================================

dev: ## Start development server (without Docker)
	@echo "$(BLUE)Starting development server...$(NC)"
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 23090

dev-docker: ## Start development environment with Docker
	@echo "$(BLUE)Starting development environment...$(NC)"
	$(COMPOSE) up -d db
	@echo "Waiting for database..."
	@sleep 5
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 23090

lint: ## Run linter
	@echo "$(BLUE)Running linter...$(NC)"
	uv run ruff check app/
	@echo "$(GREEN)✓ Linting complete$(NC)"

lint-fix: ## Run linter with auto-fix
	@echo "$(BLUE)Running linter with auto-fix...$(NC)"
	uv run ruff check --fix app/
	@echo "$(GREEN)✓ Linting complete$(NC)"

format: ## Format code
	@echo "$(BLUE)Formatting code...$(NC)"
	uv run ruff format app/
	@echo "$(GREEN)✓ Formatting complete$(NC)"

# =============================================================================
# Cleanup Commands
# =============================================================================

clean: ## Remove build artifacts and cache
	@echo "$(BLUE)Cleaning up...$(NC)"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov .coverage 2>/dev/null || true
	@echo "$(GREEN)✓ Cleanup complete$(NC)"

clean-docker: ## Remove Docker containers and images
	@echo "$(BLUE)Cleaning Docker resources...$(NC)"
	$(COMPOSE) down --rmi local
	@echo "$(GREEN)✓ Docker cleanup complete$(NC)"

clean-volumes: ## Remove Docker volumes (WARNING: deletes data!)
	@echo "$(YELLOW)⚠ WARNING: This will delete all Docker volumes including database data!$(NC)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	$(COMPOSE) down -v
	@echo "$(GREEN)✓ Volumes removed$(NC)"

clean-all: clean clean-docker ## Remove all build artifacts and Docker resources
	@echo "$(GREEN)✓ Full cleanup complete$(NC)"

stats: ## Show container resource usage
	docker stats --no-stream $(APP_CONTAINER) $(DB_CONTAINER) 2>/dev/null || \
		echo "$(YELLOW)Containers not running$(NC)"
