# begonia-pciesim

The same PCIe design, simulated at two different levels of abstraction.

`rtl-sim` asks *"does my RTL put the right bytes on the wire?"* — cycle accurate,
Verilator plus cocotb, driving upstream [LitePCIe][litepcie]'s real RTL, with a
Root Complex model standing in for the host and no operating system anywhere.

`qemu-sim` asks *"does a real Linux driver see my device?"* — a PCIe endpoint
device model compiled into [QEMU][qemu], driven two ways: by a `qtest` suite with
no guest OS at all, and by a real Alpine Linux kernel that enumerates it.

Both run as an ordinary user on an ordinary Linux box. There is no `sudo`
anywhere, no KVM, no system package manager, and no editing of files outside the
checkout: every dependency is either pinned and downloaded on demand, or built
into a self-contained toolchain next to the checkout.

|  | [`rtl-sim`](rtl-sim/) | [`qemu-sim`](qemu-sim/) |
|---|---|---|
| question | does the RTL behave on the wire? | does a real Linux see the device? |
| engine | Verilator + cocotb | QEMU (built from source) |
| design under test | LitePCIe's `LitePCIeTLPPacketizer` | `my-pcie-dev`, a PCIe endpoint |
| host side comes from | the testbench's Root Complex model | QEMU's q35 machine |
| operating system | none | a real Alpine Linux kernel |
| evidence | 3 cocotb tests, cross-checked against a second simulator | 18 `qtest` assertions + a kernel enumeration check |
| time to first result | about a minute | 10–20 minutes (it compiles QEMU) |

They are complementary rather than redundant. `rtl-sim` proves properties that a
device model cannot express (beat structure, backpressure, byte order on the
bus); `qemu-sim` proves properties that a cycle-level testbench cannot (does
firmware assign the BAR, does the kernel's own resource allocator size it
correctly, does MSI-X actually arrive).

---

## Layout

```
begonia-pciesim/
├── rtl-sim/          Verilator + cocotb simulation of LitePCIe's TLP packetizer
│   ├── rtl/          generated Verilog (from the LitePCIe sources)
│   ├── tb/           the cocotb testbench
│   ├── viz/          the HTML report renderer
│   └── cache/        the pinned upstream LitePCIe tarball (the only tracked cache)
└── qemu-sim/         QEMU device model + real-guest enumeration
    ├── device/       my_pcie_dev.c — the PCIe endpoint
    ├── scripts/      fetch / verify / install / boot helpers
    └── tests/        a stdlib-only qtest client and the assertion suite

.tools/               self-contained toolchain, several GB (not tracked)
.qemu/                QEMU source + build tree, several GB (not tracked)
```

`.tools/` and `.qemu/` are *siblings of the two checkouts*, which is where each
`env.sh` looks for them by default. Both are rebuilt on demand and neither is
tracked; see [What is not in the repository](#what-is-not-in-the-repository).

## Quick start

### `rtl-sim` — cycle-level, no OS

```bash
cd rtl-sim
bash setup-env.sh      # first time only, and only if you lack Verilator >= 5.0
source env.sh
make sim               # ~1 min: Verilator build + cocotb regression
make report            # also render report/index.html
```

### `qemu-sim` — functional, with a real kernel

```bash
cd qemu-sim
source env.sh          # reports what it found and what is missing
make fetch             # ~135 MB download, checked against a sha256 pin
make test              # configure + build QEMU, then the 18 qtest assertions
make guest             # optional: boot a real Linux and capture its verdict
```

Both directories have their own `Makefile` with the same shape
(`fetch` / `setup` / `doctor` / `test` / `clean`), and each `env.sh` prints a
one-line summary of the toolchain it resolved — so `source env.sh` is also the
first thing to run when something is off. `make doctor` prints the same
information from `make`.

## What each half actually proves

**`rtl-sim`** — `TESTS=3 PASS=3 FAIL=0`:

| test | asserts |
|---|---|
| `test_write_beat_structure` | Memory Write TLP beat structure: valid dwords == 4 header DW + payload |
| `test_read_request_tlp_header` | Memory Read 4DW header, decoded field by field |
| `test_backpressure_no_data_loss` | under random `source_ready`, payload, ordering and beat count are unchanged |

The first assertion is copied from LitePCIe's own upstream suite, which runs on
**migen's built-in simulator** — a completely separate code path. Running that
suite agrees (`2 passed, 12 subtests passed`), so the same golden assertion holds
under two independent simulators. That decouples "is my testbench right?" from
"is the DUT right?".

**`qemu-sim`** — `TESTS=18 PASS=18 FAIL=0`:

The suite walks configuration space, the BAR, the register file, real DMA through
the PCI address space, and the whole MSI-X state machine (disabled → enabled →
masked → function-masked → cleared), plus a Function Level Reset. Separately,
`make guest` boots Alpine, lets the *kernel* enumerate the device, and prints one
machine-checkable line:

```
VERDICT: PASS - Linux enumerated 1af4:1000 as 0000:00:03.0
```

`make test` is the part that tells you *which* property broke; `make guest` is
the part that shows the device to a real guest. Both are repeatable, which the
manual `lspci -vvv` version of that check is not.

## Requirements

| | needed | what the checkout does about it |
|---|---|---|
| root | never | nothing is installed system-wide and no file outside the checkout is written |
| KVM | not needed | `rtl-sim` never emulates a CPU; `qemu-sim` runs QEMU under TCG, and the qtest suite uses `-accel qtest` |
| system packages | optional | `setup-env.sh` in each half builds a conda environment *next to* the checkout (`../.tools`) with the compiler, meson/ninja or Verilator, glib, pixman, Python and the Python packages |
| a big `/tmp` | not assumed | each `env.sh` prefers a scratch directory with enough free space, and falls back rather than failing |
| network | only for the first run | every download is pinned by version **and** sha256, re-verified on use |

The machine these were originally built on is Deepin with `/usr` on a read-only
overlay, no `g++` and no Verilator, a 10 MB `/tmp` tmpfs, and no `/dev/kvm`. That
is why both halves default to a bundled toolchain and why neither passes
`-enable-kvm`. Nothing in the tree assumes that setup, and on an ordinary machine
your existing `verilator` / `gcc` / `qemu` are used as-is.

Exact pins live next to the URLs that use them:

| what | pin | where |
|---|---|---|
| LitePCIe | `sha256 35c46061…ed30da`, 270 KB tarball | `rtl-sim/cache/litepcie.tar.gz`, `Makefile` |
| QEMU | `11.1.2`, `sha256 731b5681…38016`, 141 815 216 bytes | `qemu-sim/scripts/fetch-qemu.sh` |
| Alpine guest | `3.24.2`, ISO `sha256 3ab42476…e7130b` | `qemu-sim/scripts/fetch-guest.sh` |

## What is not in the repository

Nothing generated, downloaded or enormous is tracked. Each subdirectory carries
its own `.gitignore`, and the repository root has a short one for the two
toolchain directories that sit *outside* those subdirectories.

| not tracked | where it is | how to get it |
|---|---|---|
| `.tools/` | sibling of the checkouts | `bash rtl-sim/setup-env.sh` or `bash qemu-sim/setup-env.sh` |
| `.qemu/` | sibling of `qemu-sim` | `make -C qemu-sim fetch` |
| `rtl-sim/vendor/litepcie-master/` | ~1.2 MB, 127 files of unmodified upstream code | `make -C rtl-sim vendor` — unpacks the tracked tarball, no network needed |
| `rtl-sim/sim_build/`, `report/`, `tb/dump.vcd`, `tb/results.xml` | simulator and report output | `make -C rtl-sim sim` / `waves` / `report` |
| `rtl-sim/rtl/*.v`, `rtl/*.ports.json` | generated from the LitePCIe sources | `make -C rtl-sim rtl` |
| `qemu-sim/cache/` | the 135 MB QEMU tarball and ~110 MB of Alpine guest images | `make -C qemu-sim fetch` / `guest` |
| `qemu-sim/guest-serial.log` | the hand-driven guest console | `make -C qemu-sim guest` |
| `__pycache__/`, `*.pyc`, `.pytest_cache/` | Python bytecode and caches | regenerated automatically |

Two details are worth calling out because they are easy to get wrong:

* **`cache/litepcie.tar.gz` in `rtl-sim` is deliberately tracked.** It is small,
  it is the pin that makes `make vendor` reproducible without network access, and
  keeping it means the repository does not have to carry a second, unpacked copy
  of upstream code. `qemu-sim`'s caches are *not* tracked: at 135 MB and 110 MB
  they are two orders of magnitude larger, so for those the pin is the version
  and the sha256 alone.
* **`__pycache__/` is worth clearing after the checkout is moved.** Bytecode
  embeds the source path it was compiled from, and in `rtl-sim` that stale path
  finds its way into the generated `tb/results.xml`. `make clean` does not cover
  it — `find . -name __pycache__ -type d -exec rm -rf {} +` does.

## Third-party components

| component | used by | licence |
|---|---|---|
| [LitePCIe][litepcie] | `rtl-sim` — the RTL under test | BSD 2-Clause |
| [LiteX][litex] / [migen][migen] | `rtl-sim` — exports the Verilog, and the cross-check simulator | BSD 2-Clause |
| [cocotb][cocotb] | `rtl-sim` — the testbench harness | BSD 3-Clause |
| [Verilator][verilator] | `rtl-sim` — the simulator | LGPL 3 / Artistic 2.0 |
| [QEMU][qemu] | `qemu-sim` — the machine, and the framework the device model plugs into | GPL 2.0 |

None of them is vendored except the pinned LitePCIe tarball. `qemu-sim` builds
QEMU from an unmodified upstream release and adds one file to it; see
[`qemu-sim/README.md`](qemu-sim/README.md) for exactly where it lands and how to
take it back out.

## Licence

MIT — see [LICENSE](LICENSE).

One practical note, because it is the only place where the choice interacts with
anything: `qemu-sim/device/my_pcie_dev.c` is designed to be copied into QEMU's
source tree and compiled in. QEMU is GPL 2.0 and MIT is compatible with it, so
that works — the QEMU binary you end up with is GPL 2.0, exactly as building
QEMU normally is. Nothing here redistributes QEMU.

The third-party components listed above keep their own licences. The only one
redistributed in this repository is the pinned LitePCIe tarball, under its
BSD 2-Clause terms; Verilator, cocotb, LiteX/migen and QEMU are all fetched from
upstream at build time.

[litepcie]: https://github.com/enjoy-digital/litepcie
[litex]: https://github.com/enjoy-digital/litex
[migen]: https://github.com/m-labs/migen
[cocotb]: https://github.com/cocotb/cocotb
[verilator]: https://github.com/verilator/verilator
[qemu]: https://www.qemu.org/
