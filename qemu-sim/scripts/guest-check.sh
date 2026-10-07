#!/bin/sh
#
# begonia-pciesim / qemu-sim
# Runs *inside the guest*, as PID 1 (see scripts/guest-smoke.sh, which passes
# `rdinit=/begonia-check`). Its job is to report what the guest's own PCI code
# made of the device model, and to print one machine-checkable verdict.
#
# Deliberately POSIX sh + busybox only: no compiler, no modules, no network, no
# installed system. That is the point -- every line below is evidence that the
# kernel (not our test script) enumerated the device.
#
# The first thing to know before editing this file: the Alpine netboot
# initramfs ships busybox but *no applet symlinks*. Its own /init creates them
# by running `busybox --install`, and we are replacing /init -- so without the
# line below, /bin/sh works (it is a real file) while every single applet,
# `cat` included, reports "not found". That is exactly what the first run of
# this script looked like, and it reads like a broken guest when all that is
# missing is a symlink.
/bin/busybox --install -s 2>/dev/null
command -v cat >/dev/null 2>&1 || \
    echo "[begonia-check] warning: busybox applets could not be installed"

PATH=/sbin:/bin:/usr/sbin:/usr/bin
export PATH

mount -t proc     proc     /proc 2>/dev/null
mount -t sysfs    sysfs    /sys  2>/dev/null
mount -t devtmpfs devtmpfs /dev  2>/dev/null

echo
echo "###begonia begin"
echo "kernel : $(uname -r)"
echo "cmdline: $(cat /proc/cmdline)"

echo
echo "--- what the kernel said about our vendor id while scanning the bus ---"
if command -v dmesg >/dev/null 2>&1; then
    dmesg 2>/dev/null | grep -i '1af4' || echo "(no line mentions 1af4)"
else
    echo "(no dmesg in this initramfs)"
fi

echo
echo "--- /proc/bus/pci/devices ---"
echo "    (bus  dev.fn  vendor device  irq  BAR0..BAR5)"
if [ -r /proc/bus/pci/devices ]; then
    cat /proc/bus/pci/devices
else
    echo "(not available)"
fi

# ---------------------------------------------------------------- the device
found=""
for d in /sys/bus/pci/devices/*; do
    [ -r "$d/vendor" ] || continue
    [ "$(cat "$d/vendor")" = "0x1af4" ] || continue
    [ "$(cat "$d/device")" = "0x1000" ] || continue
    found="$d"
    break
done

echo
if [ -z "$found" ]; then
    echo "--- device 1af4:1000 ---"
    echo "(not present in /sys/bus/pci/devices)"
    echo "VERDICT: FAIL - the guest kernel did not enumerate the device"
else
    echo "--- device 1af4:1000 ---"
    echo "address: $(basename "$found")"
    echo "ids    : vendor=$(cat "$found/vendor") device=$(cat "$found/device")" \
         "class=$(cat "$found/class") rev=$(cat "$found/revision")"
    echo "bars   : (start end flags)"
    cat "$found/resource"
    echo "size   : $(cat "$found/resource" | wc -l) BAR lines"
    if [ -d "$found/driver" ]; then
        echo "driver : $(basename "$(readlink "$found/driver")")"
    else
        echo "driver : none bound -- expected, this demo ships no kernel driver"
    fi
    # The configuration space the kernel exposes is the device's own, so the
    # first bytes below must read  f4 1a 00 10  ...  -- little-endian vendor
    # 1af4 then device 1000, exactly what my_pcie_dev.c declares. Note that
    # this pair is also virtio-net's *legacy* id, so the guest's virtio-pci
    # driver will probe the device and refuse to bind (no vendor-specific
    # capability, no I/O BAR); that is expected and is why "driver" is printed.
    echo "config : $(dd if="$found/config" bs=1 count=16 2>/dev/null | od -An -tx1 | tr -s ' ')"
    echo "VERDICT: PASS - Linux enumerated 1af4:1000 as $(basename "$found")"
fi
echo "###begonia end"

sync
sleep 1

# End the boot. Any of these will do; the harness has a timeout as a backstop.
if command -v poweroff >/dev/null 2>&1 && poweroff -f 2>/dev/null; then
    :
elif [ -w /proc/sysrq-trigger ]; then
    echo o > /proc/sysrq-trigger 2>/dev/null
fi
