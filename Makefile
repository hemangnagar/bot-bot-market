PY := .venv/bin/python
N ?= 300
SMOKE_N ?= 20
CAP ?= 80
FEE ?= 15
CAMPAIGN ?= phase2

.PHONY: setup test smoke run report check-no-copy clean

setup: .venv/.ok .upstream/.ok
.venv/.ok: pyproject.toml
	uv venv --python 3.12 -q .venv
	uv pip install -q -p $(PY) -e ".[dev,providers]"
	touch .venv/.ok
.upstream/.ok: scripts/setup_upstream.sh
	bash scripts/setup_upstream.sh
	touch .upstream/.ok

test: setup
	$(PY) -m pytest

check-no-copy: setup
	$(PY) scripts/check_no_copy.py

# Phase 1: 20 scenarios x both policies, prints measured cost per transaction and the projection for N.
smoke: setup
	$(PY) -m runner.run --n $(SMOKE_N) --smoke --full-n $(N) --cap $(CAP) --fee-pct $(FEE) --abort-if-over-cap --run-id smoke-$$(date +%Y%m%d-%H%M%S)

# Phase 2: base run (N scenarios x both policies) + fee sweeps (5/10/25%) + broker low-effort run, then the report.
run: setup smoke
	$(PY) -m runner.campaign --n $(N) --cap $(CAP) --campaign $(CAMPAIGN)
	$(MAKE) report CAMPAIGN=$(CAMPAIGN)

report: setup
	$(PY) -m report.build --campaign $(CAMPAIGN) --cap $(CAP)

clean:
	rm -rf .bench runs/*/claude-config
