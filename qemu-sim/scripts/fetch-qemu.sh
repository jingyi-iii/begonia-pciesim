#!/usr/bin/env bash
#
# begonia-pciesim / qemu-sim
# Fetch the pinned QEMU sources into the build tree described by env.sh.
#
#   bash scripts/fetch-qemu.sh              # uses QEMU_DIR from env.sh
#   bash scripts/fetch-qemu.sh --force      # re-download and re-extract
#
# The tarball is cached in qemu-sim/cache/ so a second checkout or a rebuild
# does not fetch 135 MB again. The cache is *not* tracked by git (unlike
# rtl-sim's 270 KB LitePCIe tarball, 135 MB does not belong in a repository);
# QEMU_VERSION and QEMU_TARBALL_SHA256 below are the pin instead.
#
# Where things live (all overridable, see env.sh):
#   <checkout>/../.qemu/qemu-11.1.2/    the sources, as unpacked
#   <checkout>/../.qemu/build/          the build directory (meson/ninja)
#
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
CACHE_DIR="$DEMO_ROOT/cache"

QEMU_VERSION="${QEMU_VERSION:-11.1.2}"
QEMU_URL="${QEMU_URL:-https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz}"
# Recorded from the download this demo was built against. Upstream publishes
# GPG signatures (qemu-<version>.tar.xz.sig) rather than a sha256 file, so this
# value is our pin: if it ever mismatches, the tarball is not the one this
# project was verified against.
QEMU_TARBALL_SHA256="${QEMU_TARBALL_SHA256:-731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016}"

FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --url)   QEMU_URL="$2"; shift 2 ;;
        -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' \
                        "${BASH_SOURCE[0]:-$0}"; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '[fetch] %s\n' "$*"; }
die() { printf '[fetch] ERROR: %s\n' "$*" >&2; exit 1; }

# shellcheck source=../env.sh
source "$DEMO_ROOT/env.sh" >/dev/null

TARBALL="$CACHE_DIR/qemu-${QEMU_VERSION}.tar.xz"
SRC_DIR="$QEMU_DIR/qemu-${QEMU_VERSION}"

mkdir -p "$CACHE_DIR"

# ---------------------------------------------------------------------------------
# 1. Download (or reuse) the tarball, and check it is whole
# ---------------------------------------------------------------------------------
if [ "$FORCE" = 1 ] || [ ! -f "$TARBALL" ]; then
    say "downloading $QEMU_VERSION ..."
    say "  $QEMU_URL"
    # Download to a temporary name: a half-written tarball must never look like
    # a cached one, which is exactly how the previous attempt got stuck with a
    # 14 MB "cache hit" that could not be unpacked.
    tmp="$TARBALL.part"
    rm -f "$tmp"
    if ! curl -fSL --progress-bar -o "$tmp" "$QEMU_URL"; then
        rm -f "$tmp"
        die "download failed. Set QEMU_URL to a mirror, or drop a tarball at
       $TARBALL yourself."
    fi
    mv "$tmp" "$TARBALL"
else
    say "using the cached $TARBALL"
fi

size=$(stat -c %s "$TARBALL" 2>/dev/null || stat -f %z "$TARBALL")
say "tarball: $size bytes"
if ! xz -t "$TARBALL" 2>/dev/null; then
    die "$TARBALL is not a complete xz archive (truncated download?). Delete it
       and re-run, or pass --force."
fi

actual_sha=$(sha256sum "$TARBALL" | awk '{print $1}')
if [ "$QEMU_TARBALL_SHA256" = "__PENDING__" ]; then
    say "sha256 = $actual_sha"
    say "(not pinned yet -- put this value in QEMU_TARBALL_SHA256 to pin it)"
elif [ "$actual_sha" != "$QEMU_TARBALL_SHA256" ]; then
    die "sha256 mismatch: this is not the tarball this demo was verified against
       expected $QEMU_TARBALL_SHA256
       got      $actual_sha"
else
    say "sha256 matches the pin"
fi

# ---------------------------------------------------------------------------------
# 2. Unpack
# ---------------------------------------------------------------------------------
if [ -d "$SRC_DIR" ] && [ "$FORCE" = 0 ]; then
    say "$SRC_DIR is already unpacked"
else
    say "unpacking into $QEMU_DIR ..."
    mkdir -p "$QEMU_DIR"
    # 135 MB of xz takes a few seconds; -J for xz, falling back to tar's own
    # auto-detection for tars built without xz support.
    #
    # --no-same-owner: the tarball records uid/gid 1000, which fails when this
    # runs as a user that cannot chown to an arbitrary uid (root in a container
    # without CAP_CHOWN to that id). The failure is per-file, so without the
    # flag tar exits non-zero on a *successful* extraction and the fallback
    # below would run for no reason.
    tar --no-same-owner -xJf "$TARBALL" -C "$QEMU_DIR" ||
        tar --no-same-owner -xf "$TARBALL" -C "$QEMU_DIR"
    [ -f "$SRC_DIR/meson.build" ] || die "unpacked, but $SRC_DIR/meson.build is missing"
fi

cat <<EOF

[fetch] ready.

  sources : $SRC_DIR
  build   : $QEMU_DIR/build   (created by \`make build\`)
  binary  : $QEMU_BIN

  Next:
    make device     # drop the PCIe device model into the QEMU tree
    make build      # configure + ninja
    make test       # functional test against the built binary

EOF
