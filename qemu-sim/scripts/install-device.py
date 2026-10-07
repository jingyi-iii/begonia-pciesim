#!/usr/bin/env python3
#
# begonia-pciesim / qemu-sim
# Drop the PCIe device model into a QEMU source tree: the .c under hw/misc/,
# registered in meson.build, selectable in Kconfig.
#
#   python3 scripts/install-device.py            # install / re-sync
#   python3 scripts/install-device.py --revert   # put the tree back
#   python3 scripts/install-device.py --check    # report, change nothing
#
# It is idempotent: running it twice does not append the same lines twice, and
# re-running after editing device/my_pcie_dev.c just copies the file again.
#
# Everything it touches is marked with BEGIN/END comments, so --revert is exact.
#
import argparse
import glob
import os
import shutil
import sys

BEGIN = "# --- begonia-pciesim qemu-sim: begin (scripts/install-device.py) ---"
END = "# --- begonia-pciesim qemu-sim: end ---"

# Appended at the *end* of hw/misc/Kconfig, which is top level -- there is no
# enclosing `if` there, so the symbol is visible everywhere. `default y if
# PCI_DEVICES` is the same shape hw/misc/Kconfig uses for its other PCI
# devices (see IVSHMEM_DEVICE), and means `-device my-pcie-dev` works on a
# stock x86_64-softmmu build with no --enable flag.
KCONFIG_BLOCK = f"""
{BEGIN}
config MY_PCIE_DEV
    bool
    default y if PCI_DEVICES
    depends on PCI
{END}
"""

DEVICE_C = "my_pcie_dev.c"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def say(msg):
    print(f"[device] {msg}")


def fail(msg):
    print(f"[device] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def find_qemu_src():
    """The unpacked QEMU tree, as env.sh describes it."""
    tools = os.environ.get("QEMU_DIR")
    if not tools:
        # env.sh sets QEMU_DIR; fall back to the documented default so the
        # script still works when invoked outside `make`.
        tools = os.path.join(os.path.dirname(ROOT), ".qemu")
    cands = sorted(glob.glob(os.path.join(tools, "qemu-*", "meson.build")))
    if not cands:
        fail(f"no QEMU source tree under {tools} -- run `make fetch` first")
    if len(cands) > 1:
        say(f"warning: several QEMU trees under {tools}, using the newest")
        cands = [max(cands, key=lambda p: os.path.getmtime(p))]
    return os.path.dirname(cands[0])


def patch_kconfig(path):
    with open(path) as f:
        text = f.read()
    if BEGIN in text:
        return "already registered"
    if not text.endswith("\n"):
        text += "\n"
    with open(path, "w") as f:
        f.write(text + KCONFIG_BLOCK)
    return "appended the MY_PCIE_DEV symbol"


def patch_meson(path):
    with open(path) as f:
        text = f.read()
    if "my_pcie_dev.c" in text:
        return "already registered"

    # hw/misc/meson.build uses several different source sets; pick the one that
    # this tree actually builds for a softmmu target rather than assuming.
    for var in ("system_ss", "softmmu_ss", "specific_ss"):
        if f"{var}.add(" in text:
            break
    else:
        fail(f"cannot find a source set to register into in {path}; "
             f"add `system_ss.add(files('{DEVICE_C}'))` by hand")

    line = f"\n# begonia-pciesim qemu-sim: the PCIe device model\n"
    line += f"# source of truth: qemu-sim/device/{DEVICE_C} (see qemu-sim/README.md)\n"
    line += f"{var}.add(when: 'CONFIG_MY_PCIE_DEV', if_true: files('{DEVICE_C}'))\n"
    if not text.endswith("\n"):
        line = "\n" + line
    with open(path, "a") as f:
        f.write(line)
    return f"registered into {var}"


def revert(src):
    undone = []
    misc = os.path.join(src, "hw", "misc")
    for name in ("Kconfig", "meson.build"):
        path = os.path.join(misc, name)
        if not os.path.exists(path):
            continue
        with open(path) as f:
            text = f.read()
        if BEGIN in text:
            start = text.index(BEGIN)
            end = text.index(END) + len(END)
            # keep the newline that ended the previous line
            text = text[:start].rstrip("\n") + text[end:].lstrip("\n")
            with open(path, "w") as f:
                f.write(text)
            undone.append(f"removed the Kconfig block from hw/misc/{name}")
        elif "my_pcie_dev" in text:
            lines = [ln for ln in text.splitlines(keepends=True) if "my_pcie_dev" not in ln]
            with open(path, "w") as f:
                f.writelines(lines)
            undone.append(f"removed the meson line from hw/misc/{name}")
    c = os.path.join(misc, DEVICE_C)
    if os.path.exists(c):
        os.remove(c)
        undone.append(f"deleted hw/misc/{DEVICE_C}")
    return undone


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--revert", action="store_true", help="undo the installation")
    ap.add_argument("--check", action="store_true", help="report only")
    args = ap.parse_args()

    src = find_qemu_src()
    say(f"QEMU tree: {src}")
    misc = os.path.join(src, "hw", "misc")
    if not os.path.isdir(misc):
        fail(f"{misc} does not look like a QEMU tree")

    if args.revert:
        for line in revert(src):
            say(line)
        say("reverted; QEMU is back to stock (meson/ninja will reconfigure)")
        return

    if args.check:
        present = os.path.exists(os.path.join(misc, DEVICE_C))
        with open(os.path.join(misc, "Kconfig")) as f:
            kc = BEGIN in f.read()
        with open(os.path.join(misc, "meson.build")) as f:
            ms = "my_pcie_dev.c" in f.read()
        say(f"{DEVICE_C}: {'present' if present else 'MISSING'}")
        say(f"Kconfig symbol: {'present' if kc else 'MISSING'}")
        say(f"meson registration: {'present' if ms else 'MISSING'}")
        sys.exit(0 if (present and kc and ms) else 1)

    # The whole point of the installer: one source of truth (device/) that is
    # copied in, so the QEMU tree can be thrown away and rebuilt at any time.
    shutil.copyfile(os.path.join(ROOT, "device", DEVICE_C), os.path.join(misc, DEVICE_C))
    say(f"copied device/{DEVICE_C} -> hw/misc/{DEVICE_C}")
    say(patch_kconfig(os.path.join(misc, "Kconfig")))
    say(patch_meson(os.path.join(misc, "meson.build")))

    # A Kconfig block is only read if some other Kconfig file sources it.
    with open(os.path.join(src, "hw", "Kconfig")) as f:
        hw_kconfig = f.read()
    if "misc/Kconfig" not in hw_kconfig:
        say("WARNING: hw/Kconfig does not source hw/misc/Kconfig -- the new "
            "symbol may be ignored. Check how this QEMU version collects Kconfigs.")

    say("done. `make build` now compiles the device in; "
        "`-device my-pcie-dev` will be available.")


if __name__ == "__main__":
    main()
