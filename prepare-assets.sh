#!/bin/bash
# ==============================================================================
# Sayri - prepare-assets.sh
# Rebuilds the web orb (Expo) into usr/share/sayri/web when Node.js is available
# and copies the CanvasKit WASM next to the bundle. Otherwise the committed
# static build is used as-is.
# Usage: prepare-assets.sh [staging-dir]
# ==============================================================================
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
STAGING="$(cd "${1:-$HERE}" && pwd)"
WEB_SRC="$HERE/web"
OUT="$STAGING/usr/share/sayri/web"
TMP="$STAGING/.sayri-web-tmp"

if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    echo "== sayri: rebuilding web orb (node found) =="
    rm -rf "$TMP"
    (cd "$WEB_SRC" && npm install --no-audit --no-fund >/dev/null && \
        npx expo export --platform web --output-dir "$TMP")
    # CanvasKit (react-native-skia web) loads its WASM next to the JS bundle.
    if [ -f "$WEB_SRC/node_modules/canvaskit-wasm/bin/full/canvaskit.wasm" ]; then
        mkdir -p "$TMP/_expo/static/js/web"
        cp "$WEB_SRC/node_modules/canvaskit-wasm/bin/full/canvaskit.wasm" \
           "$TMP/_expo/static/js/web/canvaskit.wasm"
    fi
    rm -rf "$OUT"
    cp -r "$TMP" "$OUT"
    rm -rf "$TMP"
    echo "== sayri: web orb built at $OUT =="
else
    echo "== sayri: node not found, keeping committed web build =="
fi

# Never ship Python bytecode caches.
find "$STAGING" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$STAGING" -type f -name "*.pyc" -delete 2>/dev/null || true
