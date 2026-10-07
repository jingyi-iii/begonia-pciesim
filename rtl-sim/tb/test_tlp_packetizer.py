#!/usr/bin/env python3
#
# Verilator + cocotb + LitePCIe minimal demo
# Step 3/4: the cocotb testbench -- drives the LitePCIe RTL through Verilator.
#
# Timing convention (the most robust one under cocotb + Verilator):
#   All stimulus and all sampling happen on the FALLING edge. The falling edge
#   sits in the middle of the period, at which point
#     * the registered outputs (valid / dat / be / first / last) already hold
#       this cycle's stable value, and
#     * the combinational logic (ready) has long settled.
#   So "will the DUT accept this beat in this cycle?" can be read out in one go
#   on the falling edge, while the handshake itself takes effect on the NEXT
#   rising edge.
#   That avoids the "am I reading the old or the new value?" trap entirely: the
#   rising edge is exactly where registers update (Verilog NBA), so reading one
#   cycle early or late there silently drops or duplicates beats.
#
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

# ---------------------------------------------------------------------------------
# Configuration: MEASURED from the DUT ports, never hard-coded
# ---------------------------------------------------------------------------------
# These are deliberately not kept in sync with rtl/gen_rtl.py by hand -- they are
# measured from the DUT's port widths. Otherwise changing --data-width would mean
# remembering to edit two files, and one of them would silently go stale.
# Filled in by configure_from_dut() at the start of every test case.
DATA_WIDTH    = None
ADDR_WIDTH    = None
DWS_PER_BEAT  = None    # dwords per beat (= number of lanes in `be`)
LANE_ORDER    = None

HEADER_DWS    = 4       # LitePCIe's 64-bit-address request TLP header is 16B = 4 dwords
CLK_PERIOD_NS = 10


def configure_from_dut(dut):
    """Measure the data/address widths from the DUT and derive the lane layout."""
    global DATA_WIDTH, ADDR_WIDTH, DWS_PER_BEAT, LANE_ORDER
    DATA_WIDTH   = len(dut.source_dat)
    ADDR_WIDTH   = len(dut.req_sink_adr)
    DWS_PER_BEAT = DATA_WIDTH // 32

    # Dword n of a TLP sits in lane n of the data bus, counting from the LSB, and
    # this is independent of `endianness` -- see LitePCIe's header inserter:
    #   `# Header always starts at lane 0.` + `_header_dw(i) = sink.header[32*i:32*(i+1)]`
    # `endianness` only affects the byte order INSIDE each dword (see
    # dword_endianness_swap -> reverse_bytes in litepcie/tlp/common.py); it does
    # not reverse the order of the lanes.
    # Consequence: every assertion in this file is endianness-insensitive, which
    # is why both `big` and `little` pass. To see the difference you have to look
    # at the payload byte order -- see the measured data in the README section
    # "A counter-intuitive endianness behaviour".
    LANE_ORDER = list(range(DWS_PER_BEAT))

    # The assertions below are written for a 64-bit address 4DW request header.
    # With a 32-bit address the packetizer only emits a 3DW header (fmt=0b00) and
    # they do not hold -- so fail loudly with one clear message instead of letting
    # three tests each trip over an obscure assertion.
    assert ADDR_WIDTH == 64, (
        "This demo only covers 64-bit addresses (4DW request headers), but the "
        "DUT was built with address_width=%d.\n"
        "Re-generate the RTL with: python rtl/gen_rtl.py --address-width 64" % ADDR_WIDTH
    )

    dut._log.info("DUT configuration: data_width=%d, address_width=%d, dws_per_beat=%d"
                  % (DATA_WIDTH, ADDR_WIDTH, DWS_PER_BEAT))


# The clock only needs to be started once, but across test cases we must know
# whether it is still alive. Storing the task handle rather than a bool matters:
# cocotb runs several test cases in one process, and if the clock task gets
# collected at the end of a case a bool would "lie" that it had been started,
# leaving later cases waiting forever for an edge that never comes.
_clock_task = None


def cancel_task(task):
    """Cancel a cocotb task: 2.x has cancel(), 1.x only has kill()."""
    for method in ("cancel", "kill"):
        fn = getattr(task, method, None)
        if fn is not None:
            fn()
            return
    raise AttributeError("cannot cancel task: neither cancel() nor kill() exists")


# ---------------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------------
async def start_clock_and_reset(dut):
    global _clock_task
    configure_from_dut(dut)

    if _clock_task is None or _clock_task.done():
        _clock_task = cocotb.start_soon(
            Clock(dut.sys_clk, CLK_PERIOD_NS, unit="ns").start()
        )
        await RisingEdge(dut.sys_clk)

    dut.req_sink_valid.value = 0
    dut.source_ready.value   = 1
    dut.sys_rst.value        = 1
    for _ in range(4):
        await RisingEdge(dut.sys_clk)
    dut.sys_rst.value = 0
    await RisingEdge(dut.sys_clk)


async def drive_req_sink(dut, *, we, adr, length, req_id=0x1234, tag=0x5A):
    """Push one LitePCIe request into the packetizer's req_sink (full handshake).

    The handshake is evaluated on the falling edge: the `req_sink_ready` read
    there means "does the DUT accept this beat in this cycle?", and the actual
    acceptance happens on the following rising edge. `ReadOnly()` is awaited
    first so the combinational logic has settled before it is sampled.
    """
    nbeats = (length + DWS_PER_BEAT - 1) // DWS_PER_BEAT

    await FallingEdge(dut.sys_clk)
    dut.req_sink_we.value     = we
    dut.req_sink_adr.value    = adr
    dut.req_sink_len.value    = length
    dut.req_sink_req_id.value = req_id
    dut.req_sink_tag.value    = tag
    dut.req_sink_attr.value   = 0
    dut.req_sink_valid.value  = 0

    for beat in range(nbeats):
        await FallingEdge(dut.sys_clk)
        dut.req_sink_dat.value   = 0x12345678 << (32 * (beat % DWS_PER_BEAT))
        dut.req_sink_first.value = int(beat == 0)
        dut.req_sink_last.value  = int(beat == nbeats - 1)
        dut.req_sink_valid.value = 1

        # Hold these values until some cycle reports ready=1 (i.e. the beat was
        # taken), keeping the payload stable while waiting.
        await ReadOnly()
        while int(dut.req_sink_ready.value) == 0:
            await FallingEdge(dut.sys_clk)
            await ReadOnly()

    await FallingEdge(dut.sys_clk)
    dut.req_sink_valid.value = 0


async def sample_source(dut, beats, next_ready):
    """Continuously sample the packetizer's TLP output stream.

    `source_ready` is driven on the falling edge, then `ReadOnly()` waits for the
    combinational logic to settle, and the beat is judged by `valid & ready`: that
    is exactly what determines whether the next rising edge consumes it. The
    judgement therefore matches the DUT's behaviour one-for-one, so even random
    backpressure cannot make it record too many or too few beats.
    """
    while True:
        await FallingEdge(dut.sys_clk)
        dut.source_ready.value = next_ready()
        await ReadOnly()
        if int(dut.source_valid.value) == 1 and int(dut.source_ready.value) == 1:
            beats.append({
                "dat"  : int(dut.source_dat.value),
                "be"   : int(dut.source_be.value),
                "first": int(dut.source_first.value),
                "last" : int(dut.source_last.value),
            })


async def run_transaction(dut, *, ready="always", seed=1, drain=60, **req):
    """Run one full request and return every TLP beat the packetizer emitted."""
    beats = []
    rng   = random.Random(seed)

    def next_ready():
        return 1 if ready == "always" else rng.randint(0, 1)

    sampler = cocotb.start_soon(sample_source(dut, beats, next_ready))
    try:
        await drive_req_sink(dut, **req)
        for _ in range(drain):
            await RisingEdge(dut.sys_clk)
    finally:
        cancel_task(sampler)
    return beats


# ---------------------------------------------------------------------------------
# Packet decoding helpers
# ---------------------------------------------------------------------------------
def valid_dword_count(beats):
    """Count the dwords that actually carry data (lanes where `be` is 0xf)."""
    return sum(
        1
        for beat in beats
        for lane in range(DWS_PER_BEAT)
        if ((beat["be"] >> (4 * lane)) & 0xF) == 0xF
    )


def extract_header(beats):
    """Pull the first HEADER_DWS valid dwords (i.e. the request header) in TLP order."""
    dws = []
    for beat in beats:
        for lane in LANE_ORDER:
            if ((beat["be"] >> (4 * lane)) & 0xF) == 0xF:
                dws.append((beat["dat"] >> (32 * lane)) & 0xFFFFFFFF)
        if len(dws) >= HEADER_DWS:
            break
    assert len(dws) >= HEADER_DWS, "the output stream never produced %d header dwords" % HEADER_DWS
    return dws[:HEADER_DWS]


def decode_request_header(dws):
    """Decode a 4DW request header according to the PCIe specification."""
    dw0, dw1, dw2, dw3 = dws
    return {
        "fmt"          : (dw0 >> 29) & 0x3,
        "type"         : (dw0 >> 24) & 0x1F,
        "tc"           : (dw0 >> 20) & 0x7,
        "length"       : dw0 & 0x3FF,
        "requester_id" : (dw1 >> 16) & 0xFFFF,
        "tag"          : (dw1 >> 8) & 0xFF,
        "last_be"      : (dw1 >> 4) & 0xF,
        "first_be"     : dw1 & 0xF,
        # 4DW header: DW2 = upper 32 address bits, DW3 = lower 32 address bits
        "address"      : (dw2 << 32) | dw3,
    }


# ---------------------------------------------------------------------------------
# Test 1: beat structure of a write request
#   Kept identical to the golden assertion in LitePCIe's upstream
#   test/test_request_packetizer.py
# ---------------------------------------------------------------------------------
@cocotb.test()
async def test_write_beat_structure(dut):
    """A Memory Write is packed as (4 header DW + `length` payload DW) with a single `last`."""
    await start_clock_and_reset(dut)

    for length in [1, 2, 4, 6, 8]:
        beats = await run_transaction(
            dut, we=1, adr=0x1_0000_1000, length=length, req_id=0x1234, tag=0x5A,
        )

        total = valid_dword_count(beats)
        dut._log.info("length=%d -> %d beats, %d valid dwords" % (length, len(beats), total))

        # LitePCIe's upstream golden assertion.
        assert total == HEADER_DWS + length, \
            "length=%d: expected %d valid dwords, got %d" % (length, HEADER_DWS + length, total)
        assert [b["last"] for b in beats].count(1) == 1, \
            "length=%d: `last` must appear exactly once" % length
        assert beats[0]["first"] == 1, "length=%d: the first beat must carry `first`" % length
        assert beats[-1]["last"] == 1, "length=%d: the last beat must carry `last`" % length


# ---------------------------------------------------------------------------------
# Test 2: TLP header contents of a read request (a stronger, semantic check than
#         upstream performs)
# ---------------------------------------------------------------------------------
@cocotb.test()
async def test_read_request_tlp_header(dut):
    """A Memory Read must produce a valid 4DW header: fmt/type/length/requester_id/tag/address."""
    await start_clock_and_reset(dut)

    adr, req_id, tag = 0x1_2345_6789, 0x1234, 0x5A
    beats = await run_transaction(dut, we=0, adr=adr, length=1, req_id=req_id, tag=tag)

    dws = extract_header(beats)
    hdr = decode_request_header(dws)
    dut._log.info("TLP header dwords = %s" % ["0x%08x" % d for d in dws])
    dut._log.info("decoded = %s" % hdr)

    assert hdr["fmt"]  == 0b01, \
        "a 64-bit address Memory Read must use fmt=0b01 (4DW), got %s" % bin(hdr["fmt"])
    assert hdr["type"] == 0b00000, \
        "a Memory Read must use type=0b00000, got %s" % bin(hdr["type"])
    assert hdr["length"]       == 1
    assert hdr["requester_id"] == req_id
    assert hdr["tag"]          == tag
    assert hdr["address"]      == adr, \
        "address should be 0x%x, got 0x%x" % (adr, hdr["address"])
    assert hdr["first_be"] == 0xF

    # A read request carries no payload.
    assert valid_dword_count(beats) == HEADER_DWS


# ---------------------------------------------------------------------------------
# Test 3: random backpressure must not lose data (what a cocotb testbench can do
#         that migen's built-in simulator cannot)
# ---------------------------------------------------------------------------------
@cocotb.test()
async def test_backpressure_no_data_loss(dut):
    """With `source_ready` toggling randomly, the packets must match the always-ready run."""
    await start_clock_and_reset(dut)

    req = dict(we=1, adr=0x1_0000_1000, length=6, req_id=0xCAFE, tag=0x3C)

    golden = await run_transaction(dut, ready="always", **req)
    for seed in [1, 2, 3]:
        beats = await run_transaction(dut, ready="random", seed=seed, drain=300, **req)

        assert valid_dword_count(beats) == valid_dword_count(golden), \
            "seed=%d: the valid dword count changed under backpressure" % seed
        assert [b["last"] for b in beats].count(1) == 1, \
            "seed=%d: wrong number of `last` beats" % seed
        assert [b["dat"] for b in beats] == [b["dat"] for b in golden], \
            "seed=%d: payload contents/order changed under backpressure" % seed
        assert len(beats) == len(golden), \
            "seed=%d: the beat count changed" % seed
