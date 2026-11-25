# SPDX-License-Identifier: GPL-2.0
PYTHON ?= python3
.PHONY: help test test-hybrid test-lite test-universal build build-lite build-universal check
help:
	@echo "make test | make build NEW_BUILD=/new/output | make build-lite NEW_BUILD=/new/external/output | make check"
	@echo "Research prototype. No automatic BPF loading or inference."
test: test-universal test-autotune test-bootstrap test-power-state
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_hybrid/test_hybrid.py
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

test-hybrid:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_hybrid/test_hybrid.py

test-universal:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_universal_runtime/test_universal.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_universal_runtime/test_raw.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_universal_runtime/test_cleanup.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_universal_runtime/test_process_cpu.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_universal_runtime/test_boundary_auth.py
build-universal:
	@test -n "$(NEW_BUILD)" || { echo "Set NEW_BUILD to a new external directory" >&2; exit 2; }
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_universal_runtime/build.py --output "$(NEW_BUILD)"

.PHONY: test-autotune
test-autotune:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_hybrid_autotune/test_autotune.py

.PHONY: test-bootstrap
test-bootstrap:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_bootstrap/test_package.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_bootstrap/test_auto.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_bootstrap/test_provision.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_bootstrap/test_auth_unmanaged.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_bootstrap/test_concurrent.py

.PHONY: test-power-state
test-power-state:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_bootstrap/test_power_state.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B tools/sched_ext/new_a_bootstrap/test_recovery_review.py

.PHONY: manifest check-manifest test-publication
manifest:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B scripts/update_publication_manifest.py --write
check-manifest:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B scripts/update_publication_manifest.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B scripts/verify_release.py
test-publication:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B scripts/test_publication_manifest.py
