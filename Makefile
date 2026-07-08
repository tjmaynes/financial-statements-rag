install:
	chmod +x ./scripts/install.sh
	./scripts/install.sh

lint:
	. .venv/bin/activate; python -m mypy --strict sec_filings_rag/ tests/
	. .venv/bin/activate; python -m ruff check sec_filings_rag/ tests/

format:
	. .venv/bin/activate; python -m ruff format sec_filings_rag/ tests/

test:
	. .venv/bin/activate; python -m pytest sec_filings_rag tests \
		--doctest-modules \
		--junitxml=test-results-$(shell cat .python-version).xml

.PHONY: build
build: clean lint test
	. .venv/bin/activate; python -m build

deploy: install build

ship_it: build
	git push

start:
	docker compose up --build

clean_docker:
	docker compose down -v --remove-orphans

clean:
	rm -rf dist/ build/ reports/ *.egg-info/ *cache

generate_report:
	chmod +x ./scripts/generate-report.sh
	./scripts/generate-report.sh
