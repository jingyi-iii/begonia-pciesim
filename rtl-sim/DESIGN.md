# rtl-sim: design notes

Why the RTL export, the timing convention and the parameter matrix are the way they are. The
landing page is [README.md](README.md).

## Why the `gen_rtl.py` step exists

migen generates LitePCIe's RTL from Python (the repository has only two hand-written `.v`
files), so running it under Verilator starts by exporting the module of interest as a
standalone Verilog top level. `rtl/gen_rtl.py` instantiates `LitePCIeTLPPacketizer`, collects
the leaf signals of its `req_sink` / `source` `stream.Endpoint`s into top-level ports, and
calls LiteX's `convert()`. Three things need care, each commented in the file:

1. Clock domains must be declared and their clk/rst added to the IO set. migen's `convert()`
   has `create_clock_domains=True`, which introduces the domains and puts `cd.clk` / `cd.rst`
   into `ios`. That is where the top-level `sys_clk` / `sys_rst` come from. LiteX's `convert()`
   has no such parameter and raises `Unresolved clock domain`. The script therefore creates the
   missing `ClockDomain`s itself and adds `cd.clk` / `cd.rst` to `ios` by hand. Creating the
   domain alone is not enough.
2. `Module.get_fragment()` may only be called once, with a `get_fragment_called` assertion
   inside. Take the fragment and patch the clock domains on it, then hand the fragment to
   `convert()` and never call back into the module.
3. Port names must be set explicitly, because LitePCIe's Endpoints are often unnamed
   (`self.req_sink = req_sink = stream.Endpoint(...)`) and migen's tracer cannot infer a unique
   name from that line. Leaves then collide as `valid` / `first` / `payload_adr`, migen appends
   `_1` to the later ones, and ports come out as `first`, `first_1`, `dat_1`. `gen_rtl.py`
   renames every leaf to `<endpoint>_<field>` via `sig.name_override`, since the Verilog
   emitter takes port names from `name_override` and not from `sig.name`. It then re-parses its
   own output, fails loudly on unexpected ports, and only then writes
   `rtl/litepcie_tlp_packetizer.ports.json`.

## Timing convention

Everything here is read and written on the falling edge, mid-cycle. Register outputs (`valid` /
`dat` / `be` / `first` / `last`) already hold this cycle's value there, and combinational
`ready` has settled. One read after `ReadOnly()` therefore tells you whether the DUT will
accept this beat, while the handshake it describes happens on the next rising edge.

The usual rising edge plus `ReadOnly()` loses the first beat of the source stream, which is how
the backpressure test failed. The rising edge is when registers update (Verilog NBA), so
`ReadOnly` may land either side of the NBA. On the falling edge the verdict matches what the
DUT accepted one-to-one, and random backpressure cannot make it over- or under-count.

## Changing the parameters

```bash
python rtl/gen_rtl.py --data-width 256      # 64 / 128 / 256 / 512
make sim
```

No test edits are needed: `tb/test_tlp_packetizer.py` measures `data_width` / `address_width`
off the DUT's own ports (`len(dut.source_dat)` and friends) and derives `dws_per_beat` and the
lane layout.

The configuration matrix is 3/3 everywhere: `--data-width` 128 / 256 / 512 and `--endianness`
big / little pass because the width comes off the DUT's own ports and no test touches payload
byte order. `--address-width 32` fails all three tests with "this demo only covers 64-bit
addresses", since the packetizer emits a 3DW header there and the assertions do not hold. Only
64-bit addresses (4DW request headers) are covered, and `--endianness` affects byte order
inside a dword and never reorders dwords across lanes. The n-th dword is always in
`dat[32n : 32n+32]` (see `# Header always starts at lane 0.` in LitePCIe's header inserter), so
`LANE_ORDER` is always ascending.

### A counter-intuitive `endianness` behaviour (measured)

`endianness` does not affect the request header and the payload the same way. Measured with
`--data-width 128`, driving `0xA1B2C3D4` into every payload dword:

| Setting | Header beat lanes | Payload beat lanes |
| --- | --- | --- |
| `big` | `0x60000004 0x12345AFF 0x00000001 0x00001000` | `0xD4C3B2A1` (byte-reversed) |
| `little` | identical to the row above | `0xA1B2C3D4` (as driven) |

`dword_endianness_swap` runs twice along this path:

* `litepcie/tlp/packetizer.py:693`: `tlp_raw_req_header` → `tlp_raw_req.header`
* `litepcie/tlp/packetizer.py:944`: header-inserter output → top-level `source.dat` /
  `source.be`

For the request header the two swaps cancel out, so both settings produce an identical header.
The payload only picks up the second one, so under `big` the payload on the bus is
byte-reversed.

All three test cases pass under both endianness settings, because they assert on quantities
that are insensitive to this. Those are the number of valid dwords per lane (`be` lane
`== 0xF`, unaffected by bit reversal), the request header fields (identical either way), and a
`big`-vs-`big` self-comparison. None of them touches payload byte order. To check payload order
yourself, drive a distinctive dword and read the `source_dat` lanes, as in the table above.

After changing a `gen_rtl.py` parameter, if the result looks unchanged, run
`python run.py --clean` to force a Verilator rebuild before drawing any conclusions. Otherwise
you may be measuring a stale model.

## Waveforms

`make waves` adds a waveform dump to `tb/dump.vcd`, which `gtkwave tb/dump.vcd` opens. Do not
add `--trace` to `BUILD_ARGS` in `run.py` to enable it. Verilator's tracing is a compile-time
switch needing `verilator --trace` on the RTL side and `make VM_TRACE=1` for the C++ side's
`VM_TRACE` macro and VPI dump support together. cocotb's runner only does both for
`runner.build(waves=True)`. With `--trace` alone the C++ side stays at `VM_TRACE=0`, and the
run fails at start-up with `--trace requires the design to be built with trace support`.

## Visualization

`make report` runs `viz/report.py`, which runs the regression suite plus the cycle-accurate trace
capture in `tb/capture_trace.py` and renders one self-contained `report/index.html`. There is no
CDN and no JavaScript, and every number in it comes from that run. It shows the tool flow,
per-test PASS/FAIL with simulated and wall-clock times, the 24 generated top-level ports grouped
by interface, and the request header decoded field by field over a bit ruler. It also shows one
request cycle by cycle with
`valid`/`ready`/`data` on both buses, the same write under always-ready and random `ready`
backpressure, and the `valid_dwords == 4 + length` scaling check. Every emitted beat appears
with its dwords and `first`/`last` markers, and the file opens in any browser (`xdg-open`,
`open`, `start`, double-click). The helpers in `viz/svg.py` are plain string SVG. To rasterise
one figure, dump it and run

```bash
python -c "import re; h=open('report/index.html').read(); open('wave.svg','w').write(re.findall(r'<svg.*?</svg>', h, re.S)[3])"
rsvg-convert -z 2 -b white wave.svg -o wave.png
```

`rsvg-convert` is `librsvg2-bin` on Debian/Ubuntu, `librsvg` via Homebrew.

## Environment details

`source env.sh` prints a one-line summary, and `make doctor` prints it alone; when something is
missing, it names the tool or Python module and the command that fixes it.

The requirements are Verilator 5.0 or newer built with `--timing`, since cocotb needs the timing
scheduler, a `g++` or `clang++` on `PATH`, Python 3.9 or newer with `cocotb` and `litex`
(`pip install cocotb litex`, and `litex` pulls in `migen`), and bash plus GNU make for the
`Makefile` recipes' `source` builtin. Nothing here needs root.

`env.sh` resolves the toolchain as `$LITEPCIE_DEMO_TOOLS`, else `<checkout>/../.tools`, else
`PATH`, and uses `<toolchain>/home` when `$HOME` is unset or read-only, since pip and Verilator
need a writable cache. It takes the first of `$LITEPCIE_DEMO_TMPDIR`, `$TMPDIR`, `/tmp` with at
least 1 GB free, then a scratch directory beside the toolchain or one from `mktemp`, since
Verilator's C++ intermediates do not fit in a small tmpfs. For `g++` it prefers
`*-conda-linux-gnu-g++` inside the conda environment, then plain `g++` on `PATH`. conda-forge
ships triple-prefixed compilers and no file called `g++`, which would otherwise make
Verilator's compile stage fail to find a compiler.

The toolchain root is `<checkout>/../.tools`, the conda environment `<toolchain>/env`, the
micromamba launcher `<toolchain>/bin/micromamba` and the package cache
`<toolchain>/mamba-root`. For another location, create it with
`LITEPCIE_DEMO_TOOLS=/some/path bash setup-env.sh` and export the same variable before sourcing
`env.sh`.

`setup-env.sh` uses an existing `micromamba`, `mamba` or `conda` if it finds one, and
`bash setup-env.sh --fetch-micromamba` downloads a static `micromamba` first.

### Overrides

| Variable | Meaning | Default |
| --- | --- | --- |
| `LITEPCIE_DEMO_TOOLS` | toolchain root | `<checkout>/../.tools` |
| `LITEPCIE_DEMO_TMPDIR` | scratch dir for Verilator build artefacts | first with at least 1 GB free |
| `LITEPCIE_DEMO_HOME` | `HOME` to use if the real one is not writable | your `$HOME` |
| `LITEPCIE_DEMO_CONDA_CHANNEL` | conda channel or URL for `setup-env.sh` | `conda-forge` |
| `LITEPCIE_DEMO_PIP_INDEX` | pip index URL for `setup-env.sh` | pip's own default |
| `LITEPCIE_DEMO_PYTHON` | Python version for the bundled environment | `3.12` |
| `LITEPCIE_DEMO_MICROMAMBA_URL` | what `--fetch-micromamba` downloads | micromamba's official URL |
| `MAMBA_ROOT_PREFIX` | conda package cache root | `<toolchain>/mamba-root` |

Nothing here hardcodes a mirror; behind one, pass `--channel` and `--pip-index`:

```bash
bash setup-env.sh --channel https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge --pip-index https://pypi.tuna.tsinghua.edu.cn/simple
```

### Note on read-only root filesystems

The machine this demo was originally built on runs Deepin 25 with `/usr` on a read-only
overlay. `apt install verilator` is impossible there, which is why the conda route exists and
why the toolchain is a plain conda environment next to the checkout with nothing installed
system-wide. On an ordinary machine `apt install verilator` (5.0 or newer) with your own tools
is fine. Nothing in the checkout assumes either setup.

## What is not in the repository

`sim_build/`, `report/`, `tb/dump.vcd` and `tb/results.xml` are build and report output,
rebuilt by the make targets. `tb/results.xml` embeds the absolute paths of the machine that
produced it. `rtl/*.v` and `rtl/*.ports.json` come from `make rtl`, `__pycache__/` and
`.pytest_cache/` regenerate on their own, and `.tools/`, the several-GB optional toolchain,
comes from `make -C rtl-sim setup`.

Clear `__pycache__/` after moving the checkout. The bytecode embeds the source path it was
compiled from, and that stale path reaches `tb/results.xml`. `make clean` does not cover it,
since it is not build output; `find . -name __pycache__ -type d -exec rm -rf {} +` does.

`vendor/` is ignored too, and `cache/litepcie.tar.gz` is tracked on purpose. It is 270 KB, pinned
by sha256 `35c46061ae2cf6b4310126b614625447e28fd388f842b788bc890afba7ed30da`, the pin that makes
`make vendor` reproducible offline. The unpacked tree is about 1.6 MB of unmodified upstream code,
so the tarball is cheaper than a second copy of it. That leaves 12 tracked files and about 390 KB,
of which the tarball is 270 KB.

To follow upstream instead, fetch `master` yourself. It moves, so a newer LitePCIe may need the
demo adjusting:

```bash
mkdir -p vendor && curl -L \
  https://github.com/enjoy-digital/litepcie/archive/refs/heads/master.tar.gz \
  | tar xz -C vendor/
```

To pin the unpacked tree in git instead, delete the `vendor/` line from `.gitignore` and commit
it.

A `.gitignore` resolves paths relative to its own directory, so `rtl-sim/`'s rules cover
nothing above it. The toolchain is a sibling (`../.tools`, about 3 GB) and sits outside them,
which is why the repository root (`begonia-pciesim/`) has a short `.gitignore` of its own. Move
that rule with the checkout. `make clean` does not remove `vendor/` either, since unpacking
costs a `tar` and nothing else and `make vendor` is a no-op while the tree is present.

## Test cases

`tb/test_tlp_packetizer.py` runs three cases. `test_write_beat_structure` checks the Memory Write
beat structure: valid dwords equal 4 header DW plus the payload length, and `last` is asserted
exactly once. `test_read_request_tlp_header` checks the Memory Read 4DW header, decoded below.
`test_backpressure_no_data_loss` checks that payload content, ordering and beat count under random
`source_ready` match the always-ready run. All three pass from a clean tree
(`make clean && make sim`).

The first assertion is copied from LitePCIe's upstream `test/test_request_packetizer.py`, which
agrees on migen's built-in simulator, a completely separate code path:

```bash
python -m pytest vendor/litepcie-master/test/test_request_packetizer.py -v
# → 2 passed, 12 subtests passed
```

Two independent simulators on the same `LitePCIeTLPPacketizer` and the same golden assertion
(`valid_dwords == 4 + length`) separate whether the testbench is right from whether the DUT is
right.

## Measured decode for `test_read_request_tlp_header`

```
TLP header dwords = ['0x20000001', '0x12345a0f', '0x00000001', '0x23456789']
decoded = {'fmt': 1, 'type': 0, 'tc': 0, 'length': 1,
           'requester_id': 0x1234, 'tag': 0x5A, 'first_be': 0xF, 'last_be': 0x0,
           'address': 0x123456789}
```

DW0 is `fmt/type/tc/length`, DW1 is `requester_id/tag/last_be/first_be`, and DW2 and DW3 are
the upper and lower 32 bits of the address, matching the PCIe spec. The dump above was driven
with `adr=0x1_2345_6789`, `req_id=0x1234`, `tag=0x5A`.
