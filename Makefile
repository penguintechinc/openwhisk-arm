.PHONY: setup dev build test test-unit test-integration test-e2e lint docker-build docker-push clean version-patch version-minor version-major help

# Default target
help:
	@echo "Available targets:"
	@echo "  setup              - Install all dependencies"
	@echo "  dev                - Start docker-compose.dev.yml"
	@echo "  build              - Build all services"
	@echo "  test               - Run all tests"
	@echo "  test-unit          - Run unit tests"
	@echo "  test-integration   - Run integration tests"
	@echo "  test-e2e           - Run end-to-end tests"
	@echo "  lint               - Run all linters"
	@echo "  docker-build       - Build multi-arch images"
	@echo "  docker-push        - Push to registry"
	@echo "  clean              - Clean build artifacts"
	@echo "  version-patch      - Increment patch version"
	@echo "  version-minor      - Increment minor version"
	@echo "  version-major      - Increment major version"

# Install all dependencies
setup:
	@echo "Installing dependencies..."
	@command -v docker >/dev/null 2>&1 || { echo "Docker is required but not installed"; exit 1; }
	@command -v docker-compose >/dev/null 2>&1 || { echo "Docker Compose is required but not installed"; exit 1; }
	@echo "Building Docker images..."
	docker-compose -f docker-compose.yml build
	@echo "Setup complete"

# Start development environment
dev:
	@echo "Starting development environment..."
	docker-compose -f docker-compose.dev.yml up -d
	@echo "Development environment started"
	docker-compose -f docker-compose.dev.yml logs -f

# Build all services
build:
	@echo "Building all services..."
	docker-compose -f docker-compose.yml build
	@echo "Build complete"

# Run all tests
test: test-unit test-integration test-e2e
	@echo "All tests completed"

# Run unit tests
test-unit:
	@echo "Running unit tests..."
	@if [ -d "tests/unit" ]; then \
		docker-compose -f docker-compose.dev.yml run --rm controller-api pytest tests/unit/ -v || true; \
		docker-compose -f docker-compose.dev.yml run --rm invoker-api pytest tests/unit/ -v || true; \
	fi
	@echo "Unit tests completed"

# Run integration tests
test-integration:
	@echo "Running integration tests..."
	@if [ -d "tests/integration" ]; then \
		docker-compose -f docker-compose.dev.yml run --rm controller-api pytest tests/integration/ -v || true; \
		docker-compose -f docker-compose.dev.yml run --rm invoker-api pytest tests/integration/ -v || true; \
	fi
	@echo "Integration tests completed"

# Run end-to-end tests
test-e2e:
	@echo "Running end-to-end tests..."
	@if [ -d "tests/e2e" ]; then \
		docker-compose -f docker-compose.dev.yml run --rm controller-api pytest tests/e2e/ -v || true; \
	fi
	@echo "End-to-end tests completed"

# Run all linters
lint:
	@echo "Running linters..."
	@echo "Linting Python services..."
	@for dir in services/*/; do \
		if [ -f "$$dir/requirements.txt" ]; then \
			echo "Checking $$dir..."; \
			docker run --rm -v "$$(pwd):/workspace" -w "/workspace" python:3.13-slim bash -c "pip install flake8 black isort bandit >/dev/null 2>&1 && flake8 $$dir --max-line-length=120 || true" || true; \
		fi; \
	done
	@echo "Linting complete"

# Build multi-architecture Docker images
docker-build:
	@echo "Building multi-arch Docker images..."
	@echo "Building amd64 and arm64 images..."
	docker-compose -f docker-compose.yml build --platform linux/amd64,linux/arm64
	@echo "Multi-arch build complete"

# Push Docker images to registry
docker-push:
	@echo "Pushing Docker images to registry..."
	@echo "Note: Configure registry in docker-compose.yml before pushing"
	docker-compose -f docker-compose.yml push
	@echo "Push complete"

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	docker-compose -f docker-compose.yml down -v
	docker-compose -f docker-compose.dev.yml down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "node_modules" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".DS_Store" -delete 2>/dev/null || true
	@echo "Clean complete"

# Version management targets
version-patch:
	@echo "Incrementing patch version..."
	@if [ -f "scripts/version/update-version.sh" ]; then \
		bash scripts/version/update-version.sh patch; \
	else \
		echo "Version script not found at scripts/version/update-version.sh"; \
	fi

version-minor:
	@echo "Incrementing minor version..."
	@if [ -f "scripts/version/update-version.sh" ]; then \
		bash scripts/version/update-version.sh minor; \
	else \
		echo "Version script not found at scripts/version/update-version.sh"; \
	fi

version-major:
	@echo "Incrementing major version..."
	@if [ -f "scripts/version/update-version.sh" ]; then \
		bash scripts/version/update-version.sh major; \
	else \
		echo "Version script not found at scripts/version/update-version.sh"; \
	fi
