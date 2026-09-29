PY ?= python

.PHONY: install run preview test test-unit test-integration lint fix smoke

install:          ## dev dependencies (Python 3.12)
	$(PY) -m pip install -r requirements-dev.txt

run:              ## local app on http://127.0.0.1:5000 (needs .env with API keys)
	$(PY) -m flask --app wsgi run --port 5000

preview:          ## UI preview with canned answers, no API keys
	$(PY) tests/preview_server.py

test: test-unit test-integration

test-unit:        ## web layer against stubbed src/
	$(PY) -m pytest -q tests

test-integration: ## real parser + retrieval against fixtures and samples/
	$(PY) -m pytest -q tests_integration

lint:
	$(PY) -m ruff check .

fix:
	$(PY) -m ruff check --fix .

smoke:            ## adapter check on any filing: make smoke FILE=path/to/filing.xhtml
	$(PY) tests/smoke_real.py $(FILE)
