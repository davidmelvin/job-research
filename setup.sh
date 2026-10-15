#!/bin/sh
set -eu
cd "$(dirname "$0")"
PYTHON="${JOB_RESEARCH_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in \
    /Users/david/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
    python3.13 python3.12 python3.11 python3.10 python3
  do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "Python 3.10 or newer is required. Set JOB_RESEARCH_PYTHON to its executable." >&2
  exit 1
fi
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  "$PYTHON" -m venv --clear .venv
else
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e . pytest
.venv/bin/python -m playwright install chromium
.venv/bin/python -m job_research verify-playwright

