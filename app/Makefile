IMAGE_NAME ?= posthog-contributor-impact
CONTAINER_NAME ?= posthog-contributor-impact
PORT ?= 8080

.PHONY: help build run stop logs dev

help:
	@echo "Targets:"
	@echo "  make build  Build the local Docker image"
	@echo "  make run    Build and run on http://localhost:$(PORT)"
	@echo "  make stop   Stop the running container"
	@echo "  make logs   Follow container logs"
	@echo "  make dev    Serve ./public with Python (no Docker)"

build:
	docker build -t $(IMAGE_NAME) .

run: build
	docker run --rm -p $(PORT):8080 --name $(CONTAINER_NAME) $(IMAGE_NAME)

stop:
	-docker stop $(CONTAINER_NAME)

logs:
	docker logs -f $(CONTAINER_NAME)

dev:
	python3 -m http.server $(PORT) --directory public
