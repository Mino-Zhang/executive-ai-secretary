ENV ?= local-demo
ENTERPRISE_SLUG ?= demo-enterprise

.PHONY: init upgrade-env up up-release down status logs seed-demo smoke backup config test-infra

init:
	./scripts/prepare-env.sh $(ENV)

upgrade-env:
	./scripts/upgrade-env-secrets.sh $(ENV)

up:
	./scripts/start.sh $(ENV)

up-release:
	./scripts/start-release.sh $(ENV)

down:
	./scripts/stop.sh $(ENV)

status:
	./scripts/status.sh $(ENV)

logs:
	./scripts/logs.sh $(ENV)

seed-demo:
	./scripts/seed-demo.sh local-demo $(ENTERPRISE_SLUG) "SEED local-demo/$(ENTERPRISE_SLUG)"

smoke:
	./scripts/smoke-test.sh $(ENV)

backup:
	./scripts/backup.sh $(ENV)

config:
	./scripts/compose.sh $(ENV) config --quiet

test-infra:
	./scripts/test-infra.sh
