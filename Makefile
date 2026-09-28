# Every target runs from a fresh clone. pyenv picks the interpreter from .python-version;
# without pyenv, pass PY=/path/to/python3.11 or newer.
PY ?= python3.13
VENV := .venv
PIP := $(VENV)/bin/pip --disable-pip-version-check
PYTHON := $(VENV)/bin/python
PIP_TOOLS := 7.6.1

.PHONY: setup run test lint fmt record-fixtures demo-record lock

setup: $(VENV)/.installed

# Hash mode refuses an editable install, so the package itself goes in by a second call.
# The stamp keeps the files the venv was built from: verify.sh compares contents, not mtimes.
$(VENV)/.installed: requirements-dev.lock pyproject.toml
	$(PY) -m venv $(VENV)
	$(PIP) install --quiet --require-hashes -r requirements-dev.lock
	$(PIP) install --quiet --no-deps -e .
	cat requirements-dev.lock pyproject.toml > $@

run: setup
	$(PYTHON) -m offer_checkpost serve

test: setup
	$(PYTHON) -m pytest

lint: setup
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

fmt: setup
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

record-fixtures:
	@echo "make record-fixtures: arrives in a later slice, with the live provider" >&2
	@exit 1

demo-record:
	@echo "make demo-record: arrives in a later slice, with the screen recorder" >&2
	@exit 1

# Rebuilds the hash locks in a throwaway venv, then deletes it. Run only when a pin in
# requirements*.txt changes. pip-compile hashes every file of each pinned release, so the
# locks install on any OS. Click 8.2+ makes pip-tools 7.6.1 write a false `--no-index` into
# the lock header, hence the click pin.
lock:
	$(PY) -m venv .venv-tools
	.venv-tools/bin/pip install --disable-pip-version-check --quiet pip-tools==$(PIP_TOOLS) click==8.1.8
	.venv-tools/bin/pip-compile --quiet --generate-hashes --strip-extras --output-file requirements.lock requirements.txt
	.venv-tools/bin/pip-compile --quiet --generate-hashes --strip-extras --output-file requirements-dev.lock requirements-dev.txt
	rm -rf .venv-tools
