# rtl-sim

Runs LitePCIe's real TLP packetizer RTL under Verilator with a cocotb testbench.

The DUT is `LitePCIeTLPPacketizer` (`litepcie/tlp/packetizer.py`), the block that packs
LitePCIe's internal request stream into real PCIe TLP packets, header included.

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

## Install

You need Verilator 5.0 or newer built with `--timing`, a `g++` or `clang++` on `PATH`, Python
3.9 or newer with `cocotb` and `litex`, and bash plus GNU make. Nothing needs root.
`bash setup-env.sh` builds a self-contained toolchain next to the checkout if you lack any of
it.

```bash
make vendor            # cache/litepcie.tar.gz  ->  vendor/litepcie-master/
```

The tarball is pinned by sha256. `make rtl`, `make sim`, `make waves` and `make report` all
depend on it, so a fresh clone needs no manual step.

## Use

```bash
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

The 3 cocotb tests cover Memory Write beat structure, the Memory Read 4DW header decoded field
by field, and payload integrity under random `source_ready`. The first assertion is copied from
LitePCIe's own upstream suite running on migen's built-in simulator, which agrees
(`2 passed, 12 subtests passed`), so the same golden assertion holds under two independent
simulators.

Design notes, the parameter matrix, the waveform-tracing trap and the environment overrides are
in [DESIGN.md](DESIGN.md).

## License

MIT
