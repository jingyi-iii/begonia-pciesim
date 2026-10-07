#!/usr/bin/env bash
#
# begonia-pciesim / qemu-sim
# Boot a real Linux with the device attached, and capture what the guest's PCI
# code makes of it.
#
# This is the manual check -- boot a machine, look at the device from inside --
# made repeatable:
#
#   1. take Alpine's netboot initramfs, add one script (scripts/guest-check.sh)
#      to it, repack it. No bootloader, no CD, no interactive console.
#   2. boot it with `rdinit=/begonia-check`, so that script *is* the init: the
#      kernel has already enumerated PCI by then, and the script reports what it
#      found and prints a single VERDICT line.
#   3. the host greps the serial log for that verdict.
#
# `make guest` is the only target that needs the guest image, and it is
# deliberately separate from `make test`: the qtest suite is the part that can
# run unattended in CI, this is the part that shows the real thing.
#
#   bash scripts/guest-smoke.sh                 # 90 s timeout
#   bash scripts/guest-smoke.sh --timeout 180   # slower machine
#   bash scripts/guest-smoke.sh --interactive   # hand over the console instead
#
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
TIMEOUT=90
INTERACTIVE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --timeout)     TIMEOUT="$2"; shift 2 ;;
        --interactive) INTERACTIVE=1; shift ;;
        -h|--help)     awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' \
                            "${BASH_SOURCE[0]:-$0}"; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '[guest] %s\n' "$*"; }
die() { printf '[guest] ERROR: %s\n' "$*" >&2; exit 1; }

# shellcheck source=../env.sh
source "$DEMO_ROOT/env.sh" >/dev/null

CACHE="$DEMO_ROOT/cache/alpine"
LOG="$DEMO_ROOT/guest-serial.log"
CHECK="$DEMO_ROOT/scripts/guest-check.sh"

[ -x "$QEMU_BIN" ] || die "no built QEMU at $QEMU_BIN -- run \`make build\` first"

# ---------------------------------------------------------------------------------
# 1. Guest image
# ---------------------------------------------------------------------------------
if [ ! -s "$CACHE/initramfs-virt" ] || [ ! -s "$CACHE/vmlinuz-virt" ]; then
    say "guest image missing, fetching it (see scripts/fetch-guest.sh)"
    bash "$DEMO_ROOT/scripts/fetch-guest.sh"
fi

# ---------------------------------------------------------------------------------
# 2. Add the check script to the initramfs
# ---------------------------------------------------------------------------------
# Unpack/repack in TMPDIR: the initramfs expands to ~30 MB, which does not fit
# in this machine's 10 MB /tmp, and env.sh has already picked somewhere with room.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/begonia-guest.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

say "unpacking the initramfs into $WORK ..."
mkdir -p "$WORK/root"
( cd "$WORK/root" && gzip -dc "$CACHE/initramfs-virt" | cpio -idm --quiet --no-absolute-filenames )

cp "$CHECK" "$WORK/root/begonia-check"
chmod 0755 "$WORK/root/begonia-check"
[ -x "$WORK/root/begonia-check" ] || die "could not install the check script"
say "added /begonia-check to the initramfs"

say "repacking ..."
( cd "$WORK/root" && find . | cpio -o -H newc --quiet | gzip -9 ) > "$WORK/initramfs.gz"
size=$(stat -c %s "$WORK/initramfs.gz" 2>/dev/null || stat -f %z "$WORK/initramfs.gz")
say "initramfs: $((size / 1048576)) MB"

# ---------------------------------------------------------------------------------
# 3. Boot
# ---------------------------------------------------------------------------------
# rdinit= rather than init=: the kernel prefers the initramfs's own /init (which
# Alpine ships) over init=, so overriding it is the only way to run our script.
APPEND="console=ttyS0 rdinit=/begonia-check panic=-1"
QEMU_ARGS=(
    -M q35 -m 256M
    -device my-pcie-dev
    -kernel "$CACHE/vmlinuz-virt"
    -initrd "$WORK/initramfs.gz"
    -append "$APPEND"
    -no-reboot
)
if [ "$INTERACTIVE" = 1 ]; then
    say "booting interactively; the console is yours (Ctrl-a x to quit QEMU)"
    exec "$QEMU_BIN" "${QEMU_ARGS[@]}" -nographic
fi

say "booting the Alpine kernel with the device attached ..."
say "  $QEMU_BIN ${QEMU_ARGS[*]} -display none -serial file:$LOG"
rm -f "$LOG"

set +e
timeout "$TIMEOUT" "$QEMU_BIN" "${QEMU_ARGS[@]}" -display none -serial "file:$LOG" >/dev/null 2>&1
rc=$?
set -e
case "$rc" in
    0)   say "QEMU exited cleanly (the guest powered off)" ;;
    124) say "timeout after ${TIMEOUT}s; using whatever the serial log captured" ;;
    *)   say "QEMU exited with status $rc" ;;
esac

# ---------------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------------
[ -s "$LOG" ] || die "the serial log is empty; the guest produced no output.
       Try --interactive to see what is happening, or --timeout with a larger value."

say "serial log: $LOG ($(wc -c < "$LOG") bytes)"
echo
awk '/###begonia begin/,/###begonia end/' "$LOG" | grep -v "^$" || true
echo

if grep -q '^VERDICT: PASS' "$LOG"; then
    say "the guest enumerated the device. Full log: $LOG"
    exit 0
fi

say "no PASS verdict in the serial log. Last 40 lines:"
tail -40 "$LOG" | sed 's/^/    /'
exit 1
