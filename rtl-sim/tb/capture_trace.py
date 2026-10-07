#!/usr/bin/env python3
#
# Verilator + cocotb + LitePCIe minimal demo
# Cycle-accurate trace capture, used by viz/report.py to render the HTML report.
#
# This is NOT part of the regression suite. It is a separate cocotb test module
# whose only job is to record what actually happened on the buses -- one row per
# clock cycle, plus the decoded TLP fields -- and write it to the JSON file named
# by the LITEPCIE_DEMO_TRACE environment variable.
#
# It deliberately reuses the stimulus helpers from the real testbench so that the
# pictures in the report describe the same experiment the tests assert on.
#
import json
import os
import random
import sys

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_tlp_packetizer as tb       # noqa: E402  (reuse stimulus + config)


async def _monitor(dut, rows, beats, next_ready):
    """Record one row per clock cycle and collect the beats accepted on `source`."""
    req_beats = 0
    while True:
        await FallingEdge(dut.sys_clk)
        dut.source_ready.value = next_ready()
        await ReadOnly()

        row = {
            "v_req"    : int(dut.req_sink_valid.value),
            "r_req"    : int(dut.req_sink_ready.value),
            "first_req": int(dut.req_sink_first.value),
            "last_req" : int(dut.req_sink_last.value),
            "dat_req"  : int(dut.req_sink_dat.value),
            "v_src"    : int(dut.source_valid.value),
            "r_src"    : int(dut.source_ready.value),
            "first_src": int(dut.source_first.value),
            "last_src" : int(dut.source_last.value),
            "dat_src"  : int(dut.source_dat.value),
            "be_src"   : int(dut.source_be.value),
        }
        # x_* = the beat is transferred on the next rising edge.
        row["x_req"] = row["v_req"] & row["r_req"]
        row["x_src"] = row["v_src"] & row["r_src"]
        if row["x_req"]:
            row["n_req"] = req_beats
            req_beats += 1

        rows.append(row)

        if row["x_src"]:
            beats.append({
                "cycle"   : len(rows) - 1,
                "dat"     : row["dat_src"],
                "be"      : row["be_src"],
                "first"   : row["first_src"],
                "last"    : row["last_src"],
                "bubble"  : 0,      # filled in below by _count_bubbles()
            })


async def _run(dut, *, ready_mode, seed, req, drain):
    """Drive one request and return (per-cycle rows, accepted output beats)."""
    rows, beats = [], []
    rng = random.Random(seed)

    def next_ready():
        return 1 if ready_mode == "always" else rng.randint(0, 1)

    monitor = cocotb.start_soon(_monitor(dut, rows, beats, next_ready))
    try:
        await tb.drive_req_sink(dut, **req)
        for _ in range(drain):
            await RisingEdge(dut.sys_clk)
    finally:
        tb.cancel_task(monitor)

    _annotate_waits(dut, rows, beats)
    return rows, beats


def _annotate_waits(dut, rows, beats):
    """Attach, to each output beat, how many cycles it had to wait for `ready`
    and how many dwords of payload it carries."""
    for beat in beats:
        cycle = beat["cycle"]
        # Walk back over the cycles where this beat was already valid but the
        # sink was not ready.
        wait = 0
        i = cycle - 1
        while i >= 0 and rows[i]["v_src"] == 1 and rows[i]["dat_src"] == beat["dat"]:
            wait += 1
            i -= 1
        beat["wait_cycles"] = wait
        beat["dwords"] = [
            "0x%08x" % ((beat["dat"] >> (32 * lane)) & 0xFFFFFFFF)
            for lane in range(tb.DWS_PER_BEAT)
            if ((beat["be"] >> (4 * lane)) & 0xF) == 0xF
        ]


def _bubbles(rows):
    """Count the cycles where the source was valid but the sink was not ready."""
    return sum(1 for r in rows if r["v_src"] == 1 and r["r_src"] == 0)


@cocotb.test()
async def capture(dut):
    out_path = os.environ.get("LITEPCIE_DEMO_TRACE")
    if not out_path:
        raise RuntimeError("LITEPCIE_DEMO_TRACE is not set")

    await tb.start_clock_and_reset(dut)

    write_req = dict(we=1, adr=0x1_0000_1000, length=6, req_id=0x1234, tag=0x5A)
    read_req  = dict(we=0, adr=0x1_2345_6789, length=1, req_id=0x1234, tag=0x5A)

    # 1) How the output size scales with the requested length.
    scaling = []
    for length in [1, 2, 4, 6, 8]:
        _, beats = await _run(
            dut, ready_mode="always", seed=0, drain=60,
            req=dict(write_req, length=length),
        )
        scaling.append({
            "length"       : length,
            "beats"        : len(beats),
            "valid_dwords" : tb.valid_dword_count(beats),
        })

    # 2) A read request, captured so the header can be decoded field by field.
    _, read_beats = await _run(dut, ready_mode="always", seed=0, drain=60, req=read_req)
    read_dws = tb.extract_header(read_beats)

    # 3) The same write, first with the sink always ready (the golden run) ...
    w_rows_a, w_beats_a = await _run(
        dut, ready_mode="always", seed=0, drain=60, req=write_req)

    # 4) ... then under random backpressure, captured cycle by cycle.
    w_rows_b, w_beats_b = await _run(
        dut, ready_mode="random", seed=7, drain=200, req=write_req)

    # 5) A second random seed, only for the robustness comparison.
    _, w_beats_c = await _run(dut, ready_mode="random", seed=11, drain=200, req=write_req)

    trace = {
        "config": {
            "dut"          : "litepcie.tlp.packetizer.LitePCIeTLPPacketizer",
            "data_width"   : tb.DATA_WIDTH,
            "address_width": tb.ADDR_WIDTH,
            "dws_per_beat" : tb.DWS_PER_BEAT,
            "header_dws"   : tb.HEADER_DWS,
            "clk_period_ns": tb.CLK_PERIOD_NS,
            "lane_order"   : tb.LANE_ORDER,
        },
        "scaling": scaling,
        "read": {
            "req"    : read_req,
            "beats"  : read_beats,
            "header" : read_dws,
            "decoded": tb.decode_request_header(read_dws),
        },
        "write_always": {"req": write_req, "rows": w_rows_a, "beats": w_beats_a},
        "write_random": {
            "req"    : write_req,
            "seed"   : 7,
            "rows"   : w_rows_b,
            "beats"  : w_beats_b,
            "bubbles": _bubbles(w_rows_b),
        },
        "write_random_alt": {"seed": 11, "beats": w_beats_c},
    }

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(trace, f, indent=1)
    dut._log.info("trace written to %s" % out_path)
