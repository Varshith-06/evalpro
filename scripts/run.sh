#!/usr/bin/env bash
# EvalPro - start the platform on Linux or macOS.
#
#   ./scripts/run.sh            start the server (seeds the demo course on first run)
#   ./scripts/run.sh test       run the test suite
#   ./scripts/run.sh demo       run the narrative walkthrough
#   ./scripts/run.sh deck       regenerate the SIH presentation from the template
#   ./scripts/run.sh reset      drop the demo database so it rebuilds from scratch
#
# On a POSIX host the sandbox additionally applies rlimits and a fresh session,
# so more of the isolation stack is real here than on Windows. The system
# reports exactly which layers it applied at /api/admin/system-health.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO/backend"
PORT="${PORT:-8000}"
COMMAND="${1:-serve}"

echo "EvalPro - Automated Programming Lab Evaluation Platform"

if [ "$COMMAND" = "reset" ]; then
    rm -rf "$BACKEND/var"
    echo "Removed $BACKEND/var - the demo course will rebuild on next start."
    exit 0
fi

cd "$BACKEND"
python3 -m pip install -q -r requirements.txt

case "$COMMAND" in
    test)
        python3 -m pip install -q -r requirements-dev.txt
        exec python3 -m pytest
        ;;
    demo)
        exec python3 "$REPO/scripts/demo.py"
        ;;
    deck)
        python3 -m pip install -q python-pptx
        exec python3 "$REPO/scripts/build_presentation.py"
        ;;
    serve)
        echo "Starting on http://127.0.0.1:$PORT"
        echo "First run builds the demo course by grading ~90 submissions through the real"
        echo "cascade in the real sandbox. That takes about 80 seconds and is the point."
        exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
        ;;
    *)
        echo "unknown command: $COMMAND" >&2
        exit 2
        ;;
esac
