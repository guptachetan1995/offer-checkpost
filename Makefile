# Every target runs from a fresh clone. pyenv picks the interpreter from .python-version;
# without pyenv, pass PY=/path/to/python3.11 or newer.
PY ?= python3.13
VENV := .venv
PIP := $(VENV)/bin/pip --disable-pip-version-check
PYTHON := $(VENV)/bin/python
PIP_TOOLS := 7.6.1

.PHONY: setup run test lint fmt record-fixtures demo-record demo-render lock

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

# Spends live SerpApi searches with the key in .env, at most 24 for these three samples, and
# refuses before any search when this month's usage would pass 40: MAX_TOTAL=n moves that cap.
record-fixtures: setup
	$(PYTHON) -m offer_checkpost record a=samples/offers/a.txt b=samples/offers/b.txt \
		c=samples/offers/c.txt $(if $(MAX_TOTAL),--max-total $(MAX_TOTAL))

# A real screen recording of the app on 127.0.0.1 (demo/record_demo.py), captured into
# out/demo-capture and rendered to DEMO_OUT. Live SerpApi by default: a take spends at most 18
# searches and refuses before any search when this month's usage could pass 54 (MAX_USAGE=n
# moves that cap); the replay cutaway it also records spends none. DEMO_PROVIDER=replay spends
# none at all. demo-render re-renders the saved capture, after a narration change, without
# searching again; ALLOW_UNCHECKED=1 renders narration the capture never checked, once a person
# has looked at the footage. Needs Google Chrome, ffmpeg and macOS say.
DEMO_PROVIDER ?= live
DEMO_WORKDIR ?= out/demo-capture
DEMO_OUT ?= out/offer-checkpost-demo.mp4

demo-record: setup
	$(PYTHON) -m demo.record_demo all --provider $(DEMO_PROVIDER) --workdir $(DEMO_WORKDIR) \
		--out $(DEMO_OUT) $(if $(MAX_USAGE),--max-usage $(MAX_USAGE))

demo-render: setup
	$(PYTHON) -m demo.record_demo render --workdir $(DEMO_WORKDIR) --out $(DEMO_OUT) \
		$(if $(ALLOW_UNCHECKED),--allow-unchecked)

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
