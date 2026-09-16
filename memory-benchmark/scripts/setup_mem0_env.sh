#!/usr/bin/env bash
# Convert the official memory-benchmarks Mem0 Docker Python layer into a venv.

set -euo pipefail

BENCH_ROOT="${BENCH_ROOT:-/data/pengshuang/memory-benchmark}"
OFFICIAL_ROOT="${MEM0_BENCHMARKS_ROOT:-$BENCH_ROOT/benchmark/memory-benchmarks}"
OFFICIAL_REPO="${MEM0_BENCHMARKS_REPO:-https://github.com/mem0ai/memory-benchmarks.git}"
MEM0_REPO="${MEM0_REPO:-https://github.com/mem0ai/mem0.git}"
MEM0_GIT_REF="${MEM0_GIT_REF:-v2.0.20}"
SPACY_MODEL_URL="${SPACY_MODEL_URL:-https://huggingface.co/spacy/en_core_web_sm/resolve/main/en_core_web_sm-any-py3-none-any.whl}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${MEM0_VENV_DIR:-$BENCH_ROOT/envs/mem0}"
OFFICIAL_REQUIREMENTS="$OFFICIAL_ROOT/docker/mem0/requirements.txt"
EVAL_REQUIREMENTS="$BENCH_ROOT/datasets/VehicleMem-Eval/requirements.txt"

echo "Benchmark root: $BENCH_ROOT"
echo "Official source: $OFFICIAL_ROOT"
echo "Mem0 venv: $VENV_DIR"

if [[ ! -d "$OFFICIAL_ROOT/.git" ]]; then
  if [[ -e "$OFFICIAL_ROOT" ]]; then
    echo "Path exists but is not a Git repository: $OFFICIAL_ROOT" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$OFFICIAL_ROOT")"
  echo "Cloning official memory-benchmarks: $OFFICIAL_REPO"
  git clone "$OFFICIAL_REPO" "$OFFICIAL_ROOT"
fi

ORIGIN_URL="$(git -C "$OFFICIAL_ROOT" remote get-url origin 2>/dev/null || true)"
case "$ORIGIN_URL" in
  https://github.com/mem0ai/memory-benchmarks.git|git@github.com:mem0ai/memory-benchmarks.git)
    ;;
  *)
    echo "Unexpected memory-benchmarks origin: ${ORIGIN_URL:-<missing>}" >&2
    echo "Expected: https://github.com/mem0ai/memory-benchmarks.git" >&2
    exit 1
    ;;
esac

if [[ ! -f "$OFFICIAL_REQUIREMENTS" ]]; then
  echo "Official Mem0 requirements not found: $OFFICIAL_REQUIREMENTS" >&2
  exit 1
fi

if [[ ! -f "$EVAL_REQUIREMENTS" ]]; then
  echo "VehicleMem-Eval requirements not found: $EVAL_REQUIREMENTS" >&2
  exit 1
fi

echo "Creating Mem0 environment: $VENV_DIR"
mkdir -p "$BENCH_ROOT/envs"
"$PYTHON_BIN" -m venv "$VENV_DIR"

PY="$VENV_DIR/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Python executable was not created: $PY" >&2
  exit 1
fi
"$PY" -m pip install --upgrade pip setuptools wheel

echo "Installing the official memory-benchmarks Mem0 dependencies..."
# memory-benchmarks still references the removed feat/v3-pipeline branch.
# Keep its other Docker dependencies and install Mem0 from a stable official tag.
FILTERED_REQUIREMENTS="$(mktemp)"
SPACY_MODEL_TMP_DIR=""
trap 'rm -f "$FILTERED_REQUIREMENTS"; [[ -z "$SPACY_MODEL_TMP_DIR" ]] || rm -rf "$SPACY_MODEL_TMP_DIR"' EXIT
grep -Ev '^[[:space:]]*mem0ai[[:space:]]*@' \
  "$OFFICIAL_REQUIREMENTS" > "$FILTERED_REQUIREMENTS"
"$PY" -m pip install -r "$FILTERED_REQUIREMENTS"
"$PY" -m pip install "mem0ai @ git+$MEM0_REPO@$MEM0_GIT_REF"

echo "Installing VehicleMem-Eval dependencies..."
"$PY" -m pip install -r "$EVAL_REQUIREMENTS"

echo "Installing the spaCy English model from a direct wheel..."
# spaCy's download command resolves the model through GitHub and can hang on
# restricted networks. The official Hugging Face wheel is en_core_web_sm 3.7.1,
# so keep spaCy on its compatible 3.7.x release line.
"$PY" -m pip install --upgrade "spacy>=3.7.2,<3.8.0"
if ! "$PY" -c 'import spacy; spacy.load("en_core_web_sm")' >/dev/null 2>&1; then
  # Hugging Face names this remote file with "any" in the version slot.
  # Modern pip rejects that URL as an invalid wheel filename, so download it
  # first under its real package version before installing it.
  SPACY_MODEL_TMP_DIR="$(mktemp -d)"
  SPACY_MODEL_WHEEL="$SPACY_MODEL_TMP_DIR/en_core_web_sm-3.7.1-py3-none-any.whl"
  "$PY" - "$SPACY_MODEL_URL" "$SPACY_MODEL_WHEEL" <<'PY'
import sys
import urllib.request

url, destination = sys.argv[1:]
request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(request, timeout=300) as response, open(destination, "wb") as output:
    downloaded = 0
    while chunk := response.read(1024 * 1024):
        output.write(chunk)
        downloaded += len(chunk)
        print(f"Downloaded {downloaded / 1024 / 1024:.1f} MiB", flush=True)
print(f"Saved spaCy model to {destination}")
PY
  "$PY" -m pip install "$SPACY_MODEL_WHEEL"
fi

echo "Verifying imports..."
"$PY" - <<'PY'
from pathlib import Path

import mem0
import qdrant_client
import spacy
from mem0 import Memory

print(f"mem0: {Path(mem0.__file__).resolve()}")
print(f"Memory: {Memory.__module__}.{Memory.__name__}")
print(f"qdrant_client: {Path(qdrant_client.__file__).resolve()}")
print(f"spaCy model: {spacy.load('en_core_web_sm').meta['name']}")
PY

echo "Mem0 environment ready: $PY"
