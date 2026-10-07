#!/usr/bin/env python3
#
# Verilator + cocotb + LitePCIe minimal demo
# Step 4/4: drive Verilator compilation and the cocotb test run through
#           cocotb's runner API.
#
# Everything lives in this file rather than in a Makefile so that the flow stays
# easy to read and to modify.
#
import argparse
import inspect
import os
import sys

from cocotb_tools.runner import get_runner

DEMO_ROOT = os.path.dirname(os.path.abspath(__file__))
RTL_FILE  = os.path.join(DEMO_ROOT, "rtl", "litepcie_tlp_packetizer.v")
TB_DIR    = os.path.join(DEMO_ROOT, "tb")
BUILD_DIR = os.path.join(DEMO_ROOT, "sim_build")

HDL_TOPLEVEL = "litepcie_tlp_packetizer"
TEST_MODULE  = "test_tlp_packetizer"

# The RTL emitted by migen trips a pile of Verilator "style" warnings that are
# harmless in this context, so downgrade them from errors to warnings.
BUILD_ARGS = [
    "--timing",              # cocotb needs Verilator's timing scheduler
    "-Wno-fatal",
    "-Wno-WIDTH",
    "-Wno-UNOPTFLAT",
    "-Wno-CASEINCOMPLETE",
    "-Wno-COMBDLY",
    "-Wno-LATCH",
    "-Wno-BLKANDNBLK",
    "-Wno-SELRANGE",
    "-Wno-STMTDLY",
]

# Note: do NOT add `--trace` to BUILD_ARGS by hand to enable waveforms.
# Verilator's tracing is a compile-time switch that needs BOTH
#   verilator ... --trace      (turns on tracing inside Verilator)
#   make ... VM_TRACE={0,1}    (defines the VM_TRACE macro and enables VPI dumps)
# and cocotb's runner only does both of them when `runner.build(waves=True)`.
# Adding just `--trace` gives you tracing on the RTL side with VM_TRACE=0 on the
# C++ side, and the run fails with
#   "--trace requires the design to be built with trace support".
TIMESCALE = ("1ns", "1ps")


def _sources_kwarg(runner):
    """cocotb 1.9 calls it `verilog_sources=`, cocotb 2.x calls it `sources=`."""
    params = inspect.signature(runner.build).parameters
    return "sources" if "sources" in params else "verilog_sources"


def build_model(*, waves=False, clean=False, runner=None):
    """Compile the generated RTL into a Verilator model.

    Safe to call repeatedly: cocotb's runner skips the rebuild unless `clean` or
    `waves` is set (a trace-enabled build is not interchangeable with a plain
    one, so `waves=True` always forces a rebuild).
    """
    if not os.path.exists(RTL_FILE):
        sys.exit("Cannot find %s\nRun: python rtl/gen_rtl.py" % RTL_FILE)

    runner = runner or get_runner("verilator")
    print("[run] Verilator: compiling %s ..." % os.path.relpath(RTL_FILE, DEMO_ROOT))

    kwargs = dict(
        hdl_toplevel = HDL_TOPLEVEL,
        build_dir    = BUILD_DIR,
        build_args   = BUILD_ARGS,
        waves        = waves,
        timescale    = TIMESCALE,
        always       = clean or waves,
    )
    kwargs[_sources_kwarg(runner)] = [RTL_FILE]
    return runner.build(**kwargs)


def run_tests(test_module=TEST_MODULE, *, testcase=None, seed=None, waves=False,
              runner=None):
    """Run a cocotb test module against the already-built model."""
    runner = runner or get_runner("verilator")
    print("[run] cocotb: running %s ..." % test_module)
    runner.test(
        hdl_toplevel = HDL_TOPLEVEL,
        test_module  = test_module,
        test_dir     = TB_DIR,
        build_dir    = BUILD_DIR,
        waves        = waves,
        # Keep the JUnit XML out of the source tree: cocotb would otherwise drop
        # it in test_dir, and it embeds absolute paths, which makes a copied or
        # archived checkout leak the machine it was built on.
        results_xml  = os.path.join(BUILD_DIR, "results.xml"),
        # Test selection and the random seed MUST go through runner.test(), which
        # exports COCOTB_TESTCASE / COCOTB_RANDOM_SEED to the simulator process.
        # Passing them as bare arguments through test_args is silently ignored:
        # the tests still run, but "only this test case" never takes effect.
        testcase     = testcase or None,
        seed         = seed,
        timescale    = TIMESCALE,
    )


def main():
    ap = argparse.ArgumentParser(
        description="Run the cocotb tests for the LitePCIe TLP Packetizer")
    ap.add_argument("--testcase", "-t", nargs="*", default=None,
                    help="run only the given test case(s)")
    ap.add_argument("--waves", action="store_true",
                    help="dump a waveform to tb/dump.vcd")
    ap.add_argument("--seed", default=None, help="cocotb random seed")
    ap.add_argument("--clean", action="store_true",
                    help="force a full Verilator rebuild")
    ap.add_argument("--capture-trace", metavar="PATH", default=None,
                    help="run only the trace-capture module and write its JSON to "
                         "PATH (used by viz/report.py)")
    args = ap.parse_args()

    # A single runner instance must be reused for build + test: the runner
    # records the sources it was built from, and runner.test() reads that state.
    runner = get_runner("verilator")
    build_model(waves=args.waves, clean=args.clean, runner=runner)

    if args.capture_trace:
        os.environ["LITEPCIE_DEMO_TRACE"] = os.path.abspath(args.capture_trace)
        run_tests("capture_trace", runner=runner)
        return

    run_tests(testcase=args.testcase, seed=args.seed, waves=args.waves, runner=runner)


if __name__ == "__main__":
    main()
