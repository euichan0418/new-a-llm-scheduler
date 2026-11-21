# SPDX-License-Identifier: GPL-2.0
PYTHON ?= python3
.PHONY: help test test-lite build build-lite check
help:
	@echo "make test | make build NEW_BUILD=/new/output | make build-lite NEW_BUILD=/new/external/output | make check"
	@echo "Research prototype. No automatic BPF loading or inference."
test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_runtime_lite/test_runtime_lite.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_portable/test_portable.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_portable_runtime/test_runtime.py
build:
	@test -n "$(NEW_BUILD)" || { echo "Set NEW_BUILD to a new directory" >&2; exit 2; }
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_portable/build.py --output "$(NEW_BUILD)"
check:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B scripts/summarize_research.py --check
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B scripts/verify_release.py

test-lite:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_runtime_lite/test_runtime_lite.py
build-lite:
	@test -n "$(NEW_BUILD)" || { echo "Set NEW_BUILD to a new external directory" >&2; exit 2; }
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_runtime_lite/build.py --output "$(NEW_BUILD)"
