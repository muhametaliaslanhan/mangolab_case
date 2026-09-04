#!/usr/bin/env bash
# Runs your tests. They must pass with no network at all: we run this with
# FX_UPSTREAM_BASE pointing at a closed port.
set -euo pipefail

# Don't overwrite a value the caller (reviewer) already exported — only
# fall back to a local closed port when running this standalone.
export FX_UPSTREAM_BASE="${FX_UPSTREAM_BASE:-http://127.0.0.1:9}"

python -m pytest -q
