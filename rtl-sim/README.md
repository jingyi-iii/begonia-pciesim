# Verilator + cocotb + LitePCIe minimal demo

Runs **LitePCIe**'s real RTL under **Verilator**, driven by a **cocotb** testbench,
and renders a self-contained HTML report of what happened.

The DUT is `LitePCIeTLPPacketizer` (`litepcie/tlp/packetizer.py`) — the block that
packs LitePCIe's internal request stream into real PCIe TLP packets, header
included. It is the most central piece of pure RTL in LitePCIe and the easiest to
verify in isolation.

```
┌─────────────────────────── Python (migen / LiteX) ─────────────────────────┐
│  litepcie/tlp/packetizer.py  ──rtl/gen_rtl.py──►  rtl/*.v + *.ports.json   │
└────────────────────────────────────────────────────────────────────────────┘
                                     │
                          Verilator build (run.py: cocotb_tools.runner)
                                     ▼
┌──────────────────────────── Verilator simulation ──────────────────────────┐
│  tb/test_tlp_packetizer.py (cocotb)  ⇄  rtl/litepcie_tlp_packetizer.v      │
│  tb/capture_trace.py     (cocotb)     →  report/trace.json → report/index.html
└────────────────────────────────────────────────────────────────────────────┘
```

## Requirements

| | |
| --- | --- |
| Verilator | **≥ 5.0**, built with `--timing` (cocotb needs the timing scheduler) |
| C++ compiler | any `g++` / `clang++` on `PATH` |
| Python | ≥ 3.9, with `cocotb` (and `litex`, which pulls in `migen`) |
| bash + GNU make | the `Makefile` recipes use bash's `source` builtin |
| `vendor/litepcie-master/` | the upstream LitePCIe sources — see below |

Nothing needs root. If you would rather not touch your system, `bash setup-env.sh`
installs all of the first three into a self-contained directory next to the
checkout — see [Environment](#environment).

## Getting the LitePCIe sources

`vendor/litepcie-master/` is required, and it is the one input git does not
track: it is ~1.6 MB of unmodified third-party code, so the repository keeps the
pinned tarball instead and unpacks it on demand.

```bash
make vendor            # cache/litepcie.tar.gz  ->  vendor/litepcie-master/
```

`make rtl` — and therefore `make sim`, `make waves` and `make report` — depends
on that target, so a fresh clone needs no manual step and no network access. The
tarball is pinned by

```
sha256  35c46061ae2cf6b4310126b614625447e28fd388f842b788bc890afba7ed30da
```

If you would rather follow upstream, fetch it yourself. Note that `master` moves,
so a newer LitePCIe may need the demo adjusting:

```bash
mkdir -p vendor && curl -L \
  https://github.com/enjoy-digital/litepcie/archive/refs/heads/master.tar.gz \
  | tar xz -C vendor/
```

To pin the *unpacked* tree in git instead, delete the `vendor/` line from
`.gitignore` and commit it; see also "What is not in the repository" below.

## Running it

```bash
bash setup-env.sh      # first time only, and only if you don't already have
                       # Verilator >= 5.0 + cocotb  (see Environment)

source env.sh          # put the toolchain on PATH and report what was found
make vendor            # unpack the LitePCIe sources (implied by the targets below)
make rtl               # generate Verilog (prints the port list, self-checks the names)
make sim               # Verilator build + cocotb regression
make waves             # same, plus a waveform dump to tb/dump.vcd
make report            # same, plus report/index.html
make doctor            # just show which toolchain and tools are in use
make clean             # remove the generated artefacts (keeps vendor/ and cache/)
```

Equivalent by hand, once `env.sh` has been sourced:

```bash
python rtl/gen_rtl.py --print-ports
python run.py
python run.py --waves                                    # with a waveform dump
python run.py --testcase test_read_request_tlp_header    # one test case only
python run.py --clean                                    # force a full rebuild
```

## Visualization

`make report` runs the regression suite *and* a cycle-accurate trace capture, then
renders **`report/index.html`** — one self-contained file, no CDN, no JavaScript.
Open it in any browser (`xdg-open`, `open`, `start`, or just double-click).

Every number in it comes from that live run. Nothing is hand-typed:

| Section | What it shows |
| --- | --- |
| Tool flow | Python source → Verilog → C++ model → cocotb → report |
| Regression verdict | Per-test PASS/FAIL, simulated and wall-clock times |
| Port map | The 24 ports of the generated top level, grouped by interface |
| Header decode | The real request header, decoded field by field over the bit ruler |
| Bus waveform | One request cycle by cycle: `valid`/`ready`/`data` on both buses |
| Backpressure | The same write, always-ready vs random `ready`, side by side |
| Scaling | `valid_dwords == 4 + length`, measured rather than asserted |
| Packets on the wire | Every emitted beat with its bus dwords and `first`/`last` markers |

The drawing helpers are in `viz/svg.py` (plain string SVG). To zoom into a single
figure without a browser, dump it and rasterise it with `rsvg-convert` (package
`librsvg2-bin` on Debian/Ubuntu, `librsvg` via Homebrew):

```bash
python - <<'EOF'
import re
h = open('report/index.html').read()
open('wave.svg','w').write(re.findall(r'<svg.*?</svg>', h, re.S)[3])
EOF
rsvg-convert -z 2 -b white wave.svg -o wave.png
```

## Status

Verified from a clean tree (`make clean && make sim`):

```
** test_tlp_packetizer.test_write_beat_structure        PASS   **
** test_tlp_packetizer.test_read_request_tlp_header     PASS   **
** test_tlp_packetizer.test_backpressure_no_data_loss   PASS   **
** TESTS=3 PASS=3 FAIL=0 SKIP=0                          **
```

Verified configuration matrix (3/3 in every case):

| Configuration | Result |
| --- | --- |
| `--data-width` 128 / 256 / 512 | 3/3 PASS (the testbench measures the width off the DUT's own ports — no file to edit) |
| `--endianness` big / little | 3/3 PASS (the two differ, but see the note below — no test touches payload byte order) |
| `--address-width` 32 | All 3 tests report "this demo only covers 64-bit addresses" — a deliberate, explicit failure rather than a silently wrong answer |

**Cross-validation.** The first assertion is copied from LitePCIe's upstream
`test/test_request_packetizer.py`. Running that upstream suite — which uses
**migen's built-in simulator**, a completely separate code path — agrees:

```bash
python -m pytest vendor/litepcie-master/test/test_request_packetizer.py -v
# → 2 passed, 12 subtests passed
```

So the same `LitePCIeTLPPacketizer` and the same golden assertion
(`valid_dwords == 4 + length`) hold under **two independent simulators**. That
decouples "is my testbench right?" from "is the DUT right?" — passing under
Verilator alone would not settle the first question, since the assertions could
themselves be wrong.

## Layout

| Path | Purpose |
| --- | --- |
| `setup-env.sh` | Creates the optional self-contained toolchain |
| `env.sh` | Environment entry point; `source` it and everything is on `PATH` |
| `rtl/gen_rtl.py` | Exports Verilog from the LitePCIe sources |
| `rtl/litepcie_tlp_packetizer.v` | Generated RTL (do not edit by hand) |
| `rtl/litepcie_tlp_packetizer.ports.json` | Generated port manifest, written after the self-check |
| `tb/test_tlp_packetizer.py` | cocotb testbench (the regression suite) |
| `tb/capture_trace.py` | cocotb trace-capture module, used only by the report |
| `viz/svg.py` | SVG drawing helpers |
| `viz/report.py` | Runs the suite + capture and renders `report/index.html` |
| `run.py` | Verilator build + cocotb run |
| `Makefile` | Wraps `setup` / `vendor` / `doctor` / `rtl` / `sim` / `waves` / `report` / `clean` |
| `vendor/litepcie-master/` | Upstream LitePCIe sources, unpacked by `make vendor` — **not tracked by git** |
| `cache/litepcie.tar.gz` | The pinned upstream tarball `make vendor` unpacks (tracked: this is what pins the version) |
| `.gitignore` | Everything generated, plus the unpacked `vendor/` tree (the repository root has a second one, for the toolchain) |

### What is not in the repository

| Ignored | Why | How to get it back |
| --- | --- | --- |
| `sim_build/`, `report/`, `tb/dump.vcd`, `tb/results.xml` | Simulator and report output; `tb/results.xml` even embeds the absolute paths of the machine that produced it | `make sim` / `make waves` / `make report` |
| `rtl/*.v`, `rtl/*.ports.json` | Exported from the LitePCIe sources by `make rtl` | `make rtl` |
| `vendor/` | ~1.6 MB of unmodified upstream code, recreatable byte for byte from the pinned tarball | `make vendor` |
| `__pycache__/`, `.pytest_cache/` | Python bytecode and pytest's cache | regenerated automatically |
| `.tools/` | The optional toolchain from `setup-env.sh`, several GB. It sits *next to* this checkout (`../.tools`), so it is ignored by the `.gitignore` in the repository root, not by this one | `make -C rtl-sim setup` |

Note that `__pycache__/` is worth clearing after the checkout is *moved*: the
bytecode embeds the source path it was compiled from, and that stale path finds
its way into `tb/results.xml`. `make clean` does not cover it (it is not build
output); `find . -name __pycache__ -type d -exec rm -rf {} +` does.

`cache/litepcie.tar.gz` is deliberately *tracked*: it is small (270 KB), it is
the pin that makes `make vendor` reproducible without network access, and it
saves the repository from carrying a second, unpacked copy of upstream code.
That leaves 12 tracked files and ~390 KB — of which the tarball is 270 KB.

Two things follow from how git resolves ignore rules, and both are easy to get
wrong:

* **This checkout is one directory inside the repository, not the repository
  root.** A `.gitignore` resolves its paths relative to the directory the file
  sits in, so the rules here cover `rtl-sim/` and nothing above it. The
  toolchain `setup-env.sh` builds is a *sibling* of this checkout (`../.tools`,
  ~3 GB) and therefore outside these rules — the repository root
  (`begonia-pciesim/`) carries a short `.gitignore` of its own for exactly that
  reason. If you move this checkout, move that rule with it.
* **`make clean` does not remove `vendor/`.** It is an input, not output:
  unpacking it costs a `tar` and nothing else, and `make vendor` is a no-op
  while it is present, so there is no reason to make `clean` slower and the
  next `make sim` depend on the tarball still being there.

## Why the `gen_rtl.py` step exists

LitePCIe's RTL is **not** hand-written Verilog — migen generates it from Python
(the whole repository contains only two hand-written `.v` files). So the first
step of "run LitePCIe under Verilator" is necessarily to export the module of
interest as a standalone Verilog top level.

`rtl/gen_rtl.py` instantiates `LitePCIeTLPPacketizer`, collects the leaf signals
of its `req_sink` / `source` `stream.Endpoint`s into top-level ports, and calls
LiteX's `convert()`. Three things bite here, all documented in the code:

1. **Clock domains must be declared, and their clk/rst signals added to the IO
   set.** migen's own `convert()` has `create_clock_domains=True`, which both
   introduces the domains *and* puts `cd.clk` / `cd.rst` into `ios` — that is why
   `sys_clk` / `sys_rst` end up as top-level ports. LiteX's `convert()` has no
   such parameter and raises `Unresolved clock domain` instead. So the script
   creates the missing `ClockDomain`s itself and adds `cd.clk` / `cd.rst` to `ios`
   by hand. Creating the domain alone is not enough.
2. **`Module.get_fragment()` may only be called once** (there is a
   `get_fragment_called` assertion inside). Take the fragment once, patch the
   clock domains on it, and hand *the fragment* to `convert()` — never call back
   into the module.
3. **Port names must be assigned explicitly.** Many of LitePCIe's Endpoints are
   unnamed (`self.req_sink = req_sink = stream.Endpoint(...)`; migen's tracer
   cannot infer a unique name from that line), so the leaf signals are called
   `valid` / `first` / `payload_adr` and collide across Endpoints — migen then
   appends `_1` to the later ones and you get unpredictable ports like `first`,
   `first_1`, `dat_1`. `gen_rtl.py` renames every leaf to `<endpoint>_<field>` by
   setting **`sig.name_override`** — the Verilog emitter resolves port names from
   `name_override`, *not* from `sig.name`. The script then re-parses its own
   output and fails loudly if the emitted ports are not the expected ones, and
   only then writes `rtl/litepcie_tlp_packetizer.ports.json` for the report to
   draw from.

## What the tests check

| Test case | What it asserts |
| --- | --- |
| `test_write_beat_structure` | Memory Write TLP beat structure: valid dwords == 4 header DW + payload length; `last` asserted exactly once |
| `test_read_request_tlp_header` | Memory Read 4DW header, decoded field by field: `fmt` / `type` / `length` / `requester_id` / `tag` / `address` |
| `test_backpressure_no_data_loss` | Under random `source_ready`, payload content, ordering and beat count are identical to the always-ready run |

The first assertion matches LitePCIe's upstream golden assertion
(`valid_dwords == 4 + length`) and can be used as a regression reference; the
other two add semantic and stress coverage on the cocotb side.

Measured decode for `test_read_request_tlp_header` (driven with
`adr=0x1_2345_6789`, `req_id=0x1234`, `tag=0x5A`):

```
TLP header dwords = ['0x20000001', '0x12345a0f', '0x00000001', '0x23456789']
decoded = {'fmt': 1, 'type': 0, 'tc': 0, 'length': 1,
           'requester_id': 0x1234, 'tag': 0x5A, 'first_be': 0xF, 'last_be': 0x0,
           'address': 0x123456789}
```

The field positions match the PCIe spec: DW0 is `fmt/type/tc/length`, DW1 is
`requester_id/tag/last_be/first_be`, DW2/DW3 are the upper and lower 32 bits of
the address.

## Timing convention (important)

The easiest thing to get wrong under cocotb + Verilator is *which phase* you read
and write signals in. This demo does everything on the **falling edge**. The
falling edge sits in the middle of the cycle, where:

* register outputs (`valid` / `dat` / `be` / `first` / `last`) already hold this
  cycle's value, and
* combinational logic (`ready`) has long settled.

So "will the DUT accept this beat in this cycle?" can be read in one go on the
falling edge, while the handshake it describes actually happens on the **next
rising edge**. Wait for `ReadOnly()` before reading, to let combinational logic
settle.

Why not the more common "rising edge + `ReadOnly()`"? The rising edge is exactly
when registers update (Verilog NBA), so whether you read the old or the new value
depends on whether the simulator lands `ReadOnly` before or after the NBA. In this
demo, sampling on the rising edge **loses the first beat of the source stream**
(which is why the backpressure test failed). Pinned to the falling edge, the
verdict corresponds one-to-one with what the DUT actually accepted, and no amount
of random backpressure makes it over- or under-count.

## Changing the parameters

```bash
python rtl/gen_rtl.py --data-width 256      # 64 / 128 / 256 / 512
make sim
```

**The testbench needs no edits.** `tb/test_tlp_packetizer.py` measures
`data_width` / `address_width` off the DUT's own ports (`len(dut.source_dat)` and
friends) and derives `dws_per_beat` and the dword lane layout from that.

Two caveats:

* Only **64-bit addresses** (4DW request headers) are covered. With
  `--address-width 32` the packetizer emits a 3DW header instead, the assertions
  do not hold, and the testbench says so explicitly rather than failing obscurely.
* `--endianness` affects **byte order inside a dword** and does **not** reorder
  dwords across lanes: the n-th dword is always in `dat[32n : 32n+32]` (see
  `# Header always starts at lane 0.` in LitePCIe's header inserter). So
  `LANE_ORDER` is always ascending.

### A counter-intuitive `endianness` behaviour (measured)

`endianness` does **not** affect the request header and the payload the same way.
Measured with `--data-width 128`, driving `0xA1B2C3D4` into every payload dword:

| Setting | Header beat lanes | Payload beat lanes |
| --- | --- | --- |
| `big` | `0x60000004 0x12345AFF 0x00000001 0x00001000` | `0xD4C3B2A1` (byte-reversed) |
| `little` | **identical** to the row above | `0xA1B2C3D4` (as driven) |

The reason is that `dword_endianness_swap` runs **twice** along this path:

* `litepcie/tlp/packetizer.py:693`: `tlp_raw_req_header` → `tlp_raw_req.header`
* `litepcie/tlp/packetizer.py:944`: header-inserter output → top-level `source.dat` / `source.be`

For the request header the two swaps **cancel out**, so both settings produce an
identical header; the payload only picks up the second one, so under `big` the
payload on the bus is byte-reversed.

**Consequence:** all three test cases pass under both endianness settings, because
they assert on quantities that are insensitive to this — the number of valid
dwords per lane (`be` lane `== 0xF`, unaffected by bit reversal), the request
header fields (identical either way), and a `big`-vs-`big` self-comparison. None
of them touches payload byte order. To check payload order yourself, drive a
distinctive dword and read the `source_dat` lanes, as in the table above.

> Debugging tip: after changing a `gen_rtl.py` parameter, if the result looks
> unchanged, run `python run.py --clean` to force a Verilator rebuild before
> drawing any conclusions — otherwise you may be measuring a stale model.

## Waveforms

```bash
make waves      # waveform dump to tb/dump.vcd
gtkwave tb/dump.vcd
```

Do **not** try to enable waveforms by adding `--trace` to `BUILD_ARGS` in
`run.py`. Verilator's tracing is a compile-time switch that needs two things at
once: `verilator --trace` (the RTL side) **and** `make VM_TRACE=1` (the C++ side's
`VM_TRACE` macro / VPI dump support). cocotb's runner only does both when you call
`runner.build(waves=True)`. Adding `--trace` alone gives you tracing on the RTL
side with `VM_TRACE=0` on the C++ side, and the run fails at start-up with
`--trace requires the design to be built with trace support`.

## Environment

There are two supported ways to get a toolchain, and `env.sh` handles both
without any configuration. Nothing in this checkout depends on a particular
machine, user name or directory layout.

### Option A — use the tools you already have

If `verilator` (≥ 5.0, built with `--timing`), `g++`, `make` and `python` are on
your `PATH`, that is all you need:

```bash
pip install cocotb litex      # litex pulls in migen
source env.sh
make sim
```

### Option B — let the checkout build a self-contained toolchain

Useful when your distro's Verilator is older than 5.0, or when you cannot install
system packages at all. `setup-env.sh` creates a conda environment containing
Verilator (which brings its own gcc toolchain), `make` and Python, then installs
cocotb and LiteX into it.

```bash
bash setup-env.sh                     # uses micromamba / mamba / conda if present
bash setup-env.sh --fetch-micromamba  # ...or downloads a static micromamba first
source env.sh
make report
```

| Component | Default location |
| --- | --- |
| Toolchain root | `<checkout>/../.tools` |
| conda environment | `<toolchain>/env` — Verilator + make + Python |
| micromamba launcher | `<toolchain>/bin/micromamba` |
| conda package cache | `<toolchain>/mamba-root` |
| Python packages | `cocotb`, `litex` (→ `migen`) inside that environment |

To keep it elsewhere, create it with `LITEPCIE_DEMO_TOOLS=/some/path bash setup-env.sh`
and export the same variable before sourcing `env.sh`.

### How `env.sh` decides

| Question | Answer |
| --- | --- |
| Where is the toolchain? | `$LITEPCIE_DEMO_TOOLS` → else `<checkout>/../.tools` → else *use whatever is on `PATH`* |
| `$HOME` unset or read-only? | falls back to `<toolchain>/home` — pip and Verilator both need a writable cache |
| `TMPDIR` too small? | takes the first of `$LITEPCIE_DEMO_TMPDIR`, `$TMPDIR`, `/tmp` with ≥ 1 GB free; failing that, a scratch dir next to the toolchain, or one from `mktemp`. Verilator's C++ intermediates do not fit in a small tmpfs |
| Where is `g++`? | looks for `*-conda-linux-gnu-g++` inside the conda environment, then plain `g++` on `PATH`. conda-forge ships triple-prefixed compilers and **no** file called `g++`, so naming "g++" makes Verilator's compile stage fail to find a compiler |
| Something missing? | prints which tool or Python module is absent, and the command that fixes it |

`source env.sh` prints a one-line summary of all of the above; `make doctor` does
the same on its own.

### Overrides

| Variable | Meaning | Default |
| --- | --- | --- |
| `LITEPCIE_DEMO_TOOLS` | toolchain root | `<checkout>/../.tools` |
| `LITEPCIE_DEMO_TMPDIR` | scratch dir for Verilator build artefacts | first with ≥ 1 GB free |
| `LITEPCIE_DEMO_HOME` | `HOME` to use if the real one is not writable | your `$HOME` |
| `LITEPCIE_DEMO_CONDA_CHANNEL` | conda channel or URL for `setup-env.sh` | `conda-forge` |
| `LITEPCIE_DEMO_PIP_INDEX` | pip index URL for `setup-env.sh` | pip's own default |
| `LITEPCIE_DEMO_PYTHON` | Python version for the bundled environment | `3.12` |
| `LITEPCIE_DEMO_MICROMAMBA_URL` | what `--fetch-micromamba` downloads | micromamba's official URL |
| `MAMBA_ROOT_PREFIX` | conda package cache root | `<toolchain>/mamba-root` |

**Behind a mirror or a firewall:** nothing here hardcodes one. Pass `--channel`
and `--pip-index`, for example:

```bash
bash setup-env.sh \
  --channel https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge \
  --pip-index https://pypi.tuna.tsinghua.edu.cn/simple
```

### Note on read-only root filesystems

The machine this demo was originally built on runs Deepin 25 with `/usr` on a
read-only overlay, so `apt install verilator` is impossible there. That is the
reason Option B exists and the reason the toolchain ends up as a plain conda
environment next to the checkout rather than anything system-wide. On an ordinary
machine `apt install verilator` (≥ 5.0) and Option A are perfectly fine — nothing
in the checkout assumes either setup.
