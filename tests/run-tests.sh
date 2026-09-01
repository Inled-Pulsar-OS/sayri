#!/bin/bash
# ==============================================================================
# Sayri - test runner
# Runs every tests/test_*.py with the app library on PYTHONPATH.
# The WebKit bridge test needs a display. GTK4 dropped the Broadway backend,
# so use X11/Wayland (or `xvfb-run -a` if only a session is available).
# ==============================================================================
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="$HERE/../usr/share/sayri/lib"

export PYTHONPATH="$LIB${PYTHONPATH:+:$PYTHONPATH}"

failures=0
total=0
for t in "$HERE"/test_*.py; do
    total=$((total + 1))
    name="$(basename "$t")"
    echo "== $name =="
    if python3 "$t"; then
        echo "== $name OK =="
    else
        echo "== $name FAILED =="
        failures=$((failures + 1))
    fi
done

echo
if [ "$failures" -eq 0 ]; then
    echo "Todos los tests pasaron ($total)."
    exit 0
else
    echo "$failures de $total suites fallaron."
    exit 1
fi
