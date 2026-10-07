#!/usr/bin/env bash
#
# begonia-pciesim / qemu-sim
# Fetch the Alpine guest used by `make guest`.
#
# Two things are downloaded:
#
#   alpine-virt-<version>-x86_64.iso   the live ISO, for exploring by hand
#   netboot/{vmlinuz,initramfs,modloop}-virt
#                                      kernel + initramfs, which is what the
#                                      automated check boots: with the netboot
#                                      initramfs we can run our own init (see
#                                      scripts/guest-smoke.sh) and get an
#                                      unattended, machine-checkable result,
#                                      without a bootloader or an interactive
#                                      console
#
# The ISO is verified against the sha256 Alpine publishes next to it, and that
# hash is pinned below. 69 MB + 12 MB + 10 MB does not belong in a git
# repository, so cache/ is ignored and this script is the way to get it back.
#
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
CACHE="$DEMO_ROOT/cache/alpine"

ALPINE_VERSION="${ALPINE_VERSION:-3.24.2}"
ALPINE_BRANCH="${ALPINE_BRANCH:-3.24}"
BASE_URL="${ALPINE_BASE_URL:-https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_BRANCH}/releases/x86_64}"

ISO="alpine-virt-${ALPINE_VERSION}-x86_64.iso"
ISO_SHA256="${ISO_SHA256:-3ab424762af704b2c2a9e57df1dc37f982af260071504d977f2fb96822e7130b}"
NETBOOT_FILES=(vmlinuz-virt initramfs-virt modloop-virt)

FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' \
                        "${BASH_SOURCE[0]:-$0}"; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '[guest] %s\n' "$*"; }
die() { printf '[guest] ERROR: %s\n' "$*" >&2; exit 1; }

download() {           # download <url> <destination>
    local url="$1" dest="$2" tmp
    if [ -s "$dest" ] && [ "$FORCE" = 0 ]; then
        say "cached: $(basename "$dest")"
        return 0
    fi
    tmp="$dest.part"
    rm -f "$tmp"
    say "fetching $(basename "$dest") ..."
    curl -fSL --progress-bar -o "$tmp" "$url" || { rm -f "$tmp"; die "download failed: $url"; }
    mv "$tmp" "$dest"
}

mkdir -p "$CACHE"

# ---------------------------------------------------------------------------------
# 1. The live ISO
# ---------------------------------------------------------------------------------
download "$BASE_URL/$ISO" "$CACHE/$ISO"
download "$BASE_URL/$ISO.sha256" "$CACHE/$ISO.sha256"

actual="$(sha256sum "$CACHE/$ISO" | awk '{print $1}')"
if [ "$actual" != "$ISO_SHA256" ]; then
    die "sha256 mismatch for $ISO
       pinned $ISO_SHA256
       got    $actual
       Delete $CACHE/$ISO and re-run if the download was interrupted."
fi
say "ISO sha256 matches the pin"

# ---------------------------------------------------------------------------------
# 2. Kernel + initramfs (+ modloop for the hand-driven boot)
# ---------------------------------------------------------------------------------
for f in "${NETBOOT_FILES[@]}"; do
    download "$BASE_URL/netboot/$f" "$CACHE/$f"
done

# An initramfs that is not a gzip stream would fail deep inside QEMU with an
# error that is hard to read, so check it here.
for f in initramfs-virt; do
    if ! gzip -t "$CACHE/$f" 2>/dev/null; then
        die "$CACHE/$f is not a valid gzip stream (truncated download?)"
    fi
done
say "kernel/initramfs look intact"

cat <<EOF

[guest] ready: $CACHE
        $(ls -1 "$CACHE" | tr '\n' ' ')

  Next:  make guest
  Or boot it by hand, with the device attached and a console on the terminal:
         \$QEMU_BIN -M q35 -m 512M -device my-pcie-dev \\
             -cdrom $CACHE/$ISO -boot d -nographic

EOF
