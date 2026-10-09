# begonia-pciesim

One PCIe design, simulated at two levels of abstraction.

`rtl-sim` runs LitePCIe's real TLP packetizer under Verilator with a cocotb testbench, cycle
accurate, no operating system anywhere. `qemu-sim` compiles a PCIe endpoint device model into
QEMU and drives it two ways: a `qtest` suite with no guest, and a real Alpine Linux kernel that
enumerates the device.

Neither half is redundant. `rtl-sim` proves the things a device model cannot express, like beat
structure, backpressure and byte order on the bus. `qemu-sim` proves the things a cycle-level
testbench cannot, like whether firmware assigns the BAR, whether the kernel's own resource
allocator sizes it correctly, and whether MSI-X actually arrives.

|  | `rtl-sim` | `qemu-sim` |
| --- | --- | --- |
| engine | Verilator + cocotb | QEMU, built from source |
| design under test | LitePCIe's `LitePCIeTLPPacketizer` | `my-pcie-dev`, a PCIe endpoint |
| operating system | none | a real Alpine Linux kernel |
| evidence | 3 cocotb tests | 18 `qtest` assertions plus a kernel enumeration check |
| time to first result | about a minute | 10 to 20 minutes, since it compiles QEMU |

## Install

```bash
# cycle level, no OS
cd rtl-sim
bash setup-env.sh      # first time only, and only if you lack Verilator >= 5.0
source env.sh
make sim               # ~1 min: Verilator build + cocotb regression
make report            # also renders report/index.html

# functional, with a real kernel
cd ../qemu-sim
source env.sh          # reports what it found and what is missing
make fetch             # ~135 MB download, checked against a sha256 pin
make test              # configure + build QEMU, then the 18 qtest assertions
make guest             # optional: boot a real Linux and capture its verdict
```

No root, ever, and no KVM: `rtl-sim` never emulates a CPU, and `qemu-sim` runs QEMU under TCG
with the qtest suite on `-accel qtest`. A system toolchain is optional. `setup-env.sh` builds a
conda environment next to the checkout if you lack Verilator, meson, ninja, glib or pixman. The
network is needed only for the first run, and every download is pinned by version and sha256.

## Use

Both halves have a `Makefile` of the same shape: `fetch`, `setup`, `doctor`, `test`, `clean`.
`source env.sh` prints a one-line summary of the toolchain it resolved, and is the first thing
to run when something looks off.

Details live in the two sub-READMEs: [`rtl-sim/`](rtl-sim/README.md) and
[`qemu-sim/`](qemu-sim/README.md).

## Notes

`.tools/` and `.qemu/` are siblings of the checkouts, several GB each and untracked;
`make setup`, `make vendor` and `make fetch` rebuild them. The one deliberately tracked
artefact is `rtl-sim/cache/litepcie.tar.gz`, which is the pin that makes `make vendor` work
without network access.

LitePCIe and LiteX/migen are BSD 2-Clause, cocotb is BSD 3-Clause, Verilator is LGPL 3 /
Artistic 2.0, and QEMU is GPL 2.0. Nothing is vendored except that tarball. `qemu-sim` builds
QEMU from an unmodified upstream release and adds one file to it, so the QEMU binary you end up
with is GPL 2.0, as building QEMU normally is.

I originally built these on a Deepin 25 box with `/usr` on a read-only overlay, no `g++`, no
Verilator, a 10 MB `/tmp` tmpfs and no `/dev/kvm`. That is why both halves can build their own
toolchain.

## License

MIT
