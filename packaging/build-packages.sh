#!/bin/bash
# ==============================================================================
# Sayri - build-packages.sh
# Builds native packages for Arch (PKGBUILD/tar), Debian (.deb),
# Fedora (.rpm) and Flatpak (.flatpak) from the Sayri source tree.
#
# The repo IS a Debian-style package tree (usr/ + etc/ + DEBIAN/). This script
# copies that tree into a clean staging dir, injects the real version from
# packaging/VERSION, and hands each packaging backend its inputs.
#
# Usage:
#   build-packages.sh [deb|rpm|arch|flatpak|all]
#   build-packages.sh                      # build everything tooling allows
#
# Output goes to ./dist/ at the repository root.
# ==============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
VERSION="$(cat "$HERE/VERSION")"
DIST="$ROOT/dist"
mkdir -p "$DIST"

TARGET="${1:-all}"
USE_STAGING="${STAGING:-}"

log()  { printf '\033[1;36m[packaging]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[packaging]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[packaging]\033[0m %s\n' "$*" >&2; exit 1; }

# --- Staging directory -------------------------------------------------------
# Prepare the payload tree (usr/ + etc/) in STAGING. Either a user-provided
# staging dir (already laid out) or a fresh copy of the repo's package tree.
prepare_staging() {
    if [ -n "$USE_STAGING" ]; then
        STAGING="$USE_STAGING"
        log "Using provided staging dir: $STAGING"
        return
    fi
    STAGING="$(mktemp -d "${TMPDIR:-/tmp}/sayri-stage.XXXXXX")"
    last_staging="$STAGING"
    log "Staging tree in $STAGING"
    cp -a "$ROOT/usr" "$STAGING/usr"
    cp -a "$ROOT/etc" "$STAGING/etc"
    # Debian control (only meaningful for the .deb, harmless elsewhere).
    mkdir -p "$STAGING/DEBIAN"
    sed "s/__VERSION__/$VERSION/g" "$ROOT/DEBIAN/control" > "$STAGING/DEBIAN/control"
}

last_staging=""
cleanup_staging() {
    if [ -n "$last_staging" ] && [ -z "$USE_STAGING" ]; then
        rm -rf "$last_staging"
        last_staging=""
    fi
}

# --- Debian -----------------------------------------------------------------
build_deb() {
    log "Building Debian .deb (v$VERSION)"
    command -v dpkg-deb >/dev/null || { warn "dpkg-deb not found, skipping .deb"; return 1; }
    prepare_staging
    fakeroot=""
    if command -v fakeroot >/dev/null 2>&1; then fakeroot="fakeroot"; fi
    $fakeroot dpkg-deb --build --root-owner-group "$STAGING" "$ROOT/sayri_${VERSION}_all.deb" >/dev/null
    mv -f "$ROOT/sayri_${VERSION}_all.deb" "$DIST/"
    cleanup_staging
    log "  -> $DIST/sayri_${VERSION}_all.deb"
}

# --- Fedora / RPM ------------------------------------------------------------
build_rpm() {
    log "Building Fedora .rpm (v$VERSION)"
    command -v rpmbuild >/dev/null || { warn "rpmbuild not found, skipping .rpm"; return 1; }
    prepare_staging
    local rpmbuilddir
    rpmbuilddir="$(mktemp -d "${TMPDIR:-/tmp}/sayri-rpm.XXXXXX")"
    # Copy the payload into the spec's expected SOURCES layout.
    mkdir -p "$rpmbuilddir/SOURCES"
    tar -C "$ROOT" --exclude='dist' --exclude='web/node_modules' --exclude='web/.expo' \
        --exclude='.git' -cf "$rpmbuilddir/SOURCES/sayri-$VERSION.tar.gz" \
        README.md packaging usr etc
    rpmbuild --define "_topdir $rpmbuilddir" \
             --define "sayri_version $VERSION" \
             -bb "$HERE/sayri.spec"
    cp "$rpmbuilddir"/RPMS/noarch/sayri-*.rpm "$DIST/" 2>/dev/null \
        || cp "$rpmbuilddir"/RPMS/*/sayri-*.rpm "$DIST/"
    rm -rf "$rpmbuilddir"
    log "  -> $DIST/sayri-*.rpm"
}

# --- Arch / PKGBUILD ---------------------------------------------------------
build_arch() {
    log "Building Arch package (v$VERSION)"
    command -v makepkg >/dev/null || { warn "makepkg not found, skipping Arch package"; return 1; }
    local archdir
    archdir="$(mktemp -d "${TMPDIR:-/tmp}/sayri-arch.XXXXXX")"
    # Copy PKGBUILD and payload tree into a build dir. makepkg uses the source
    # dir; we ship a pre-built tree, so we install directly from it.
    cp "$HERE/PKGBUILD" "$archdir/PKGBUILD"
    cp "$ROOT/README.md" "$archdir/README.md"
    tar -C "$ROOT" -czf "$archdir/usr.tar.gz" usr
    tar -C "$ROOT" -czf "$archdir/etc.tar.gz" etc
    sed -i "s/^pkgver=.*/pkgver=$VERSION/" "$archdir/PKGBUILD"
    ( cd "$archdir" && makepkg -f --noconfirm --nodeps )
    cp "$archdir"/sayri-*.pkg.tar.* "$DIST/"
    rm -rf "$archdir"
    log "  -> $DIST/sayri-*.pkg.tar.zst"
}

# --- Flatpak -----------------------------------------------------------------
build_flatpak() {
    log "Building Flatpak bundle (v$VERSION)"
    command -v flatpak-builder >/dev/null || { warn "flatpak-builder not found, skipping Flatpak"; return 1; }
    local work
    work="$(mktemp -d "${TMPDIR:-/tmp}/sayri-flatpak.XXXXXX")"
    # flatpak-builder resolves `dir` source paths relative to the manifest root
    # (the build dir it is invoked on) and requires them to live beneath it.
    # Assemble a temp root with the manifest next to usr/ and etc/ and build
    # there. The dir is fresh so --force-clean is not needed.
    cp "$ROOT/packaging/io.github.inled.sayri.yml" "$work/io.github.inled.sayri.yml"
    cp -a "$ROOT/usr" "$work/usr"
    cp -a "$ROOT/etc" "$work/etc"
    # --user keeps the runtime/cache in the calling user's home so non-root CI
    # runners and headless systems work without a system installation.
    # --install-deps-from=flathub pulls org.gnome.Platform/Sdk automatically
    # (the flathub remote must already be added).
    if ! flatpak-builder --user --install-deps-from=flathub --state-dir="$work/.state" \
        --repo="$work/repo" "$work" "$work/io.github.inled.sayri.yml"; then
        rm -rf "$work"
        die "flatpak-builder failed"
    fi
    flatpak build-bundle "$work/repo" "$DIST/sayri-$VERSION.flatpak" es.inled.sayri
    rm -rf "$work"
    log "  -> $DIST/sayri-$VERSION.flatpak"
}

# --- Dispatch ----------------------------------------------------------------
case "$TARGET" in
    deb)     build_deb ;;
    rpm)     build_rpm ;;
    arch)    build_arch ;;
    flatpak) build_flatpak ;;
    all)
        build_deb || true
        build_rpm || true
        build_arch || true
        build_flatpak || true
        ;;
    *) die "Unknown target '$TARGET' (deb|rpm|arch|flatpak|all)" ;;
esac

log "Done. Artifacts in $DIST"
