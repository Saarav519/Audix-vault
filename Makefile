PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: setup db run test seed lint check

setup:
	python3.12 -m venv .venv
	$(PIP) install -r requirements-dev.txt
	[ -f .env ] || cp .env.example .env

db:
	service postgresql start || true
	runuser -u postgres -- psql -tc "SELECT 1 FROM pg_roles WHERE rolname='audix'" | grep -q 1 || runuser -u postgres -- psql -c "CREATE ROLE audix LOGIN PASSWORD 'audix' CREATEDB;"
	runuser -u postgres -- psql -tc "SELECT 1 FROM pg_database WHERE datname='audix'" | grep -q 1 || runuser -u postgres -- psql -c "CREATE DATABASE audix OWNER audix;"

run:
	$(PY) manage.py migrate --noinput
	$(PY) manage.py bootstrap_admin
	$(PY) manage.py runserver 0.0.0.0:8000

test:
	.venv/bin/pytest -q

seed:
	$(PY) manage.py seed_demo

lint:
	.venv/bin/ruff check .

check:
	DEBUG=0 SECRET_KEY=check-only-$$RANDOM-0123456789abcdefghijklmnopqrstuvwxyz ALLOWED_HOSTS=example.com $(PY) manage.py check --deploy
