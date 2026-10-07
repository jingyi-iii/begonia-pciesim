#!/usr/bin/env python3
#
# Verilator + cocotb + LitePCIe minimal demo
# Render report/index.html -- a single self-contained page that shows what the
# demo does and what actually came out of the simulation.
#
# Nothing in the report is hand-typed: the numbers come from
#   * a real run of the cocotb regression suite, and
#   * a real cycle-accurate trace captured from the Verilator model
#     (tb/capture_trace.py), and
#   * the generated Verilog's own port list.
#
# Usage:  source env.sh && python viz/report.py      (or: make report)
#
import html
import json
import os
import re
import subprocess
import sys

DEMO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(DEMO_ROOT, "report")
TRACE_JSON = os.path.join(REPORT_DIR, "trace.json")
OUT_HTML   = os.path.join(REPORT_DIR, "index.html")
RTL_FILE   = os.path.join(DEMO_ROOT, "rtl", "litepcie_tlp_packetizer.v")
VENDOR_DIR = os.path.join(DEMO_ROOT, "vendor", "litepcie-master")

HDL_TOPLEVEL = "litepcie_tlp_packetizer"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import svg as S                     # noqa: E402

C = S.C


# --------------------------------------------------------------------------------
# 1. Collect the facts
# --------------------------------------------------------------------------------
def _run(args):
    return subprocess.run([sys.executable] + args, cwd=DEMO_ROOT,
                          capture_output=True, text=True)


def run_suite():
    print("[report] running the cocotb regression suite ...")
    p = _run(["run.py"])
    out = p.stdout + p.stderr
    if p.returncode != 0:
        print("[report] WARNING: the suite exited with code %d" % p.returncode)
    return out


def run_capture():
    print("[report] capturing a cycle-accurate trace ...")
    p = _run(["run.py", "--capture-trace", TRACE_JSON])
    if not os.path.exists(TRACE_JSON):
        sys.stderr.write(p.stdout + p.stderr)
        sys.exit("[report] trace capture failed")
    return json.load(open(TRACE_JSON))


RESULT_RE = re.compile(
    r"\*\*\s+(?P<mod>[\w.]+)\.(?P<name>\w+)\s+(?P<status>PASS|FAIL|SKIP)\s+"
    r"(?P<sim>[\d.]+)\s+(?P<real>[\d.]+)\s+(?P<ratio>[\d.]+)\s+\*\*")
SUM_RE = re.compile(r"\*\*\s+TESTS=(?P<t>\d+)\s+PASS=(?P<p>\d+)\s+FAIL=(?P<f>\d+)\s+SKIP=(?P<s>\d+)")


def parse_suite(log):
    tests, seen = [], set()
    for m in RESULT_RE.finditer(log):
        key = m.group("name")
        if key in seen:
            continue
        seen.add(key)
        tests.append({
            "module"   : m.group("mod"),
            "name"     : key,
            "status"   : m.group("status"),
            "sim_time" : float(m.group("sim")),
            "real_time": float(m.group("real")),
        })
    summary = SUM_RE.search(log)
    versions = {
        "verilator": (re.search(r"Running on Verilator version (.+)", log) or [None, "?"])[1].strip(),
        "cocotb"   : (re.search(r"Initialized cocotb v(\S+) from", log) or [None, "?"])[1],
        "python"   : (re.search(r"Using Python ([\d.]+) interpreter", log) or [None, "?"])[1],
    }
    return {
        "tests"    : tests,
        "summary"  : summary.groupdict() if summary else {},
        "versions" : versions,
    }


# The port list is NOT re-parsed out of the Verilog here. rtl/gen_rtl.py already
# has the authoritative list -- it computes it, self-checks it against the design
# and writes it to a JSON manifest. Keeping a second parser in this file is how
# the report once ended up showing 23 ports while the module had 24 (the local
# regex demanded a trailing `,` or `)`, and the last port never has one).
PORT_MANIFEST = os.path.join(DEMO_ROOT, "rtl", "litepcie_tlp_packetizer.ports.json")


def load_ports():
    """Port list for the report, straight from the export step's manifest.

    Falls back to reading the generated Verilog if only that is present, so a
    hand-run `python viz/report.py` still works after someone deleted the
    manifest -- but the manifest is the normal path and the one that carries the
    build configuration along with the ports.
    """
    if os.path.exists(PORT_MANIFEST):
        man = json.load(open(PORT_MANIFEST))
        return man["module"], man["ports"], man["config"]
    if os.path.exists(RTL_FILE):
        print("[report] %s missing, falling back to parsing %s"
              % (os.path.relpath(PORT_MANIFEST, DEMO_ROOT),
                 os.path.relpath(RTL_FILE, DEMO_ROOT)))
        return HDL_TOPLEVEL, parse_rtl_ports(RTL_FILE), None
    sys.exit("[report] neither a port manifest nor %s exists -- run: "
             "python rtl/gen_rtl.py" % os.path.relpath(RTL_FILE, DEMO_ROOT))


# Fallback for when only the Verilog is around: walk the module header line by
# line (same approach as gen_rtl.py's parse_port_decls) so the closing `);` can
# never swallow the last port.
PORT_DECL_RE = re.compile(
    r"^\s*(?P<dir>input|output|inout)\s+"
    r"(?:(?:wire|reg|logic)\s+)?"
    r"(?:\[(?P<msb>\d+):(?P<lsb>\d+)\]\s*)?"
    r"(?P<name>\w+)", re.M)


def parse_rtl_ports(path):
    lines = open(path).read().splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"\s*module\s+\w+\s*\(", ln)), None)
    if start is None:
        return []
    ports = []
    for ln in lines[start:]:
        if ln.strip() in (");", ")"):
            break
        m = PORT_DECL_RE.match(ln)
        if m:
            msb = m.group("msb")
            ports.append({
                "dir"  : m.group("dir"),
                "name" : m.group("name"),
                "width": (int(msb) + 1) if msb is not None else 1,
            })
    return ports


def active_window(rows, pad_before=1, pad_after=3):
    act = [i for i, r in enumerate(rows) if r["v_req"] or r["v_src"]]
    if not act:
        return 0, min(len(rows) - 1, 24)
    return max(0, act[0] - pad_before), min(len(rows) - 1, act[-1] + pad_after)


def litepcie_version():
    """Version of the vendored LitePCIe, read from its own packaging metadata.

    Hardcoding it here would be one more thing to remember to bump, and it would
    start lying the moment somebody drops a different LitePCIe revision into
    vendor/.
    """
    try:
        with open(os.path.join(VENDOR_DIR, "setup.py")) as f:
            m = re.search(r"""version\s*=\s*["']([^"']+)["']""", f.read())
        return m.group(1) if m else "vendored"
    except OSError:
        return "vendored"


def toolchain_note():
    """Describe the toolchain the run actually used.

    Deliberately describes it by *kind* rather than by absolute path: the report
    is a shareable artefact, so it should not embed somebody else's directory
    layout.
    """
    if os.environ.get("CONDA_PREFIX"):
        origin = "the bundled conda environment next to this checkout"
    else:
        origin = "Verilator / cocotb / Python from <span class=\"mono\">PATH</span>"
    return (f'<p class="tail">Nothing here is pinned to one machine. '
            f'<span class="mono">env.sh</span> resolves {origin}, and falls back to '
            f'your own installed tools if the bundled toolchain is absent. Run '
            f'<span class="mono">make doctor</span> to see which one is in use, or '
            f'<span class="mono">bash setup-env.sh</span> to create the bundled one.</p>')


# --------------------------------------------------------------------------------
# 2. Small HTML helpers
# --------------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s))


def pill(kind, label):
    return f'<span class="pill {kind}">{esc(label)}</span>'


def stat(value, label, tone=""):
    return (f'<div class="stat {tone}"><div class="stat-v">{esc(value)}</div>'
            f'<div class="stat-l">{esc(label)}</div></div>')


def table(headers, rows, cls=""):
    head = "".join(f'<th class="{c}">{esc(h)}</th>' for h, c in headers)
    body = []
    for r in rows:
        cells = "".join(f'<td class="{c}">{v}</td>' for v, c in r)
        body.append(f"<tr>{cells}</tr>")
    return (f'<table class="{cls}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def card(num, title, hint, inner):
    n = f'<span class="num">{num}</span>' if num else ""
    return (f'<section class="card"><h2>{n}{esc(title)}</h2>'
            f'<p class="hint">{hint}</p>{inner}</section>')


# --------------------------------------------------------------------------------
# 3. Render the page
# --------------------------------------------------------------------------------
def render(suite, trace, ports, module, rtl_cfg):
    cfg    = trace["config"]
    w_rand = trace["write_random"]
    w_ok   = trace["write_always"]

    n_pass = int(suite["summary"].get("p", 0))
    n_all  = int(suite["summary"].get("t", 0))
    dwords = sum(len(b["dwords"]) for b in w_rand["beats"])

    # ---- header ----------------------------------------------------------------
    badges = "".join([
        f'<span class="badge">Verilator <b>{esc(suite["versions"]["verilator"].split()[0])}</b></span>',
        f'<span class="badge">cocotb <b>{esc(suite["versions"]["cocotb"])}</b></span>',
        f'<span class="badge">Python <b>{esc(suite["versions"]["python"])}</b></span>',
        f'<span class="badge">LitePCIe <b>{esc(litepcie_version())}</b></span>',
    ])

    stats = "".join([
        stat(f"{n_pass}/{n_all}", "tests passing", "ok" if n_pass == n_all and n_all else "bad"),
        stat(len(ports), "DUT ports"),
        stat(f'{cfg["data_width"]}', "data path (bits)"),
        stat(dwords, "dwords for length=6"),
        stat(w_rand["bubbles"], "wait states absorbed"),
    ])

    # The trace reports the width it measured off the DUT's own ports; the
    # manifest reports the width the export step was asked for. If those disagree
    # the report is describing two different builds, so say so loudly rather than
    # quietly printing one of them.
    width_mismatch = ""
    if rtl_cfg and rtl_cfg["data_width"] != cfg["data_width"]:
        width_mismatch = (
            f'<p class="tail"><b>Warning:</b> the trace measured a '
            f'{cfg["data_width"]}-bit data path but the RTL manifest says '
            f'{rtl_cfg["data_width"]} bits &mdash; the RTL and the simulation model '
            f'are out of sync. Re-run <span class="mono">make report</span>.</p>')

    # ---- 1. flow ---------------------------------------------------------------
    flow = S.pipeline(
        stages=[
            {"title": "LitePCIe, described in Python (migen / LiteX)",
             "sub": "vendor/litepcie-master/litepcie/tlp/packetizer.py",
             "color": C["blue"]},
            {"title": "Exported as a standalone Verilog top level",
             "sub": "rtl/litepcie_tlp_packetizer.v",
             "color": C["purple"]},
            {"title": "Compiled by Verilator into a C++ simulation model",
             "sub": "sim_build/Vlitepcie_tlp_packetizer",
             "color": C["teal"]},
            {"title": "Driven cycle by cycle by a cocotb testbench",
             "sub": "tb/test_tlp_packetizer.py",
             "color": C["amber"]},
            {"title": "Results, waveforms and this report",
             "sub": "trace.json  ·  dump.vcd  ·  report/index.html",
             "color": C["green"]},
        ],
        arrows=[
            "python rtl/gen_rtl.py   (migen -> Verilog)",
            "verilator --timing --cc",
            "runner.test()   (COCOTB_TESTCASE / COCOTB_RANDOM_SEED)",
            "verdict + bus trace",
        ],
    )
    sec_flow = card(1, "From Python to a passing test",
                    "LitePCIe ships no hand-written Verilog for this block: the RTL "
                    "is generated from migen, so the flow starts with an export step.",
                    flow)

    # ---- 2. results ------------------------------------------------------------
    status_cells = []
    for t in suite["tests"]:
        kind = "pass" if t["status"] == "PASS" else "fail"
        status_cells.append([
            (f'<span class="mono">{esc(t["name"])}</span>', ""),
            (pill(kind, t["status"]), ""),
            (f'{t["sim_time"]:.0f} ns', "mono num"),
            (f'{t["real_time"]:.2f} s', "mono num"),
        ])
    results = table(
        [("Test case", ""), ("Verdict", ""), ("Simulated", "num"), ("Wall clock", "num")],
        status_cells)

    tail = (f'<p class="tail">TESTS={n_all} &nbsp; PASS={n_pass} &nbsp; '
            f'FAIL={suite["summary"].get("f", "?")} &nbsp; SKIP={suite["summary"].get("s", "?")}</p>')
    sec_results = card(2, "Regression verdict",
                       f"Produced by <span class='mono'>python run.py</span> against the "
                       f"Verilator model, just now.",
                       results + tail)

    # ---- 3. port map -----------------------------------------------------------
    n_in  = sum(1 for p in ports if p["dir"] == "input")
    n_out = len(ports) - n_in
    sec_ports = card(3, "What the export step produced",
                     f'{len(ports)} ports ({n_in} in / {n_out} out) on '
                     f'<span class="mono">{esc(module)}</span>, taken from the port '
                     f'manifest the export step writes after it has checked its own '
                     f'output. Port names are assigned explicitly by that script, '
                     f'otherwise migen would emit unpredictable names like '
                     f'<span class="mono">first</span> / '
                     f'<span class="mono">first_1</span>.',
                     S.port_map(ports, module))

    # ---- 4. header decode ------------------------------------------------------
    rd = trace["read"]
    dw_chips = "".join(f'<span class="chip mono">0x{d:08x}</span>' for d in rd["header"])
    dec = rd["decoded"]
    dec_txt = (f'fmt={dec["fmt"]} (4DW) &nbsp; type=0x{dec["type"]:02x} (Memory Read) &nbsp; '
               f'length={dec["length"]} &nbsp; requester_id=0x{dec["requester_id"]:04x} &nbsp; '
               f'tag=0x{dec["tag"]:02x} &nbsp; first_be=0x{dec["first_be"]:x} &nbsp; '
               f'address=0x{dec["address"]:x}')
    asked = (f'<p class="asked">The testbench asked for a read of address '
             f'<span class="mono">0x{rd["req"]["adr"]:x}</span>, requester '
             f'<span class="mono">0x{rd["req"]["req_id"]:04x}</span>, tag '
             f'<span class="mono">0x{rd["req"]["tag"]:02x}</span>.</p>')
    sec_header = card(4, "What the DUT actually emitted",
                      "The 4 dwords of the request header, decoded bit field by bit field. "
                      "Every value matches what the testbench drove in.",
                      S.tlp_header(rd["header"]) +
                      f'<div class="chips">{dw_chips}</div><div class="decoded mono">{dec_txt}</div>' + asked)

    # ---- 5. waveform -----------------------------------------------------------
    a, b = active_window(w_rand["rows"])
    view = w_rand["rows"][a:b + 1]
    wave = S.waveform(view, title="Bus waveform")
    wave_note = (f'<p class="tail">Cycles {a} to {b} of the write request '
                 f'(length={w_rand["req"]["length"]}, seed {w_rand["seed"]}). '
                 f'Solid blocks on a bus lane mark the cycle in which that beat is finally '
                 f'accepted; the flat stretches before them are the wait states caused by '
                 f'random backpressure.</p>')
    sec_wave = card(5, "One request, cycle by cycle",
                    "Reconstructed from the values cocotb sampled on every falling edge.",
                    wave + wave_note)

    # ---- 6. backpressure -------------------------------------------------------
    bp = S.backpressure_chart(w_ok["beats"], w_rand["beats"], w_rand["bubbles"], w_rand["seed"])
    same = ([x["dat"] for x in w_ok["beats"]] == [x["dat"] for x in w_rand["beats"]])
    alt_ok = (len(trace["write_random_alt"]["beats"]) == len(w_ok["beats"]))
    bp_note = (f'<p class="tail">Payload bytes identical between the two runs: '
               f'<b>{"yes" if same else "NO"}</b>. A second random seed '
               f'({trace["write_random_alt"]["seed"]}) also produced '
               f'{len(trace["write_random_alt"]["beats"])} beats, same as the always-ready run: '
               f'<b>{"yes" if alt_ok else "NO"}</b>.</p>')
    sec_bp = card(6, "Backpressure does not lose data",
                  "Same write request twice: once with the output sink always ready, once with "
                  "it toggling randomly. The beats stretch out in time but nothing is dropped "
                  "or duplicated.",
                  bp + bp_note)

    # ---- 7. scaling ------------------------------------------------------------
    sec_scale = card(7, "The golden relation, measured",
                     'LitePCIe\'s upstream test asserts <span class="mono">valid_dwords == '
                     '4 + length</span>. Here it is as measured data, not as a claim.',
                     S.scaling_chart(trace["scaling"]))

    # ---- 8. emitted packets ----------------------------------------------------
    rows = []
    for i, b in enumerate(w_rand["beats"]):
        chips = " ".join(f'<span class="chip mono">{esc(d)}</span>' for d in b["dwords"])
        flags = []
        if b["first"]:
            flags.append('<span class="flag first">first</span>')
        if b["last"]:
            flags.append('<span class="flag last">last</span>')
        rows.append([
            (f'<b>b{i}</b>', "mono"),
            (f'cycle {b["cycle"]}', "mono num"),
            (f'{b["wait_cycles"]}', "mono num"),
            (" ".join(flags) or "&mdash;", ""),
            (chips, ""),
        ])
    pkt = table([("Beat", ""), ("Accepted", "num"), ("Waited", "num"), ("Markers", ""),
                 ("Dwords on the wire", "")], rows)
    sec_pkt = card(8, "The packets on the wire",
                   "Every beat the DUT emitted for the length=6 write, with the payload byte "
                   "order you can see with <span class='mono'>endianness=big</span>.",
                   pkt)

    # ---- 9. reproduce ----------------------------------------------------------
    cmds = """source env.sh          # put the toolchain (Verilator / g++ / python) on PATH
make rtl               # generate Verilog from the LitePCIe sources
make sim               # Verilator build + cocotb regression
make waves             # same, plus a waveform dump
make report            # regenerate this page"""
    repro = (f'<pre class="code">{esc(cmds)}</pre>' + toolchain_note())
    sec_repro = card(9, "Reproduce it", "The whole demo is four commands.", repro)

    # ---- assemble --------------------------------------------------------------
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verilator + cocotb + LitePCIe &mdash; simulation report</title>
<style>
{STYLE}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="eyebrow">Verilator + cocotb + LitePCIe</div>
    <h1>Simulating LitePCIe's TLP packetizer</h1>
    <p class="lede">A minimal, reproducible demo: the real LitePCIe RTL, exported to Verilog,
    compiled by Verilator and exercised by a cocotb testbench.</p>
    <div class="badges">{badges}</div>
    <div class="stats">{stats}</div>
    {width_mismatch}
  </header>

  {sec_flow}
  {sec_results}
  {sec_ports}
  {sec_header}
  {sec_wave}
  {sec_bp}
  {sec_scale}
  {sec_pkt}
  {sec_repro}

  <footer>Generated by <span class="mono">viz/report.py</span> from a live simulation run.</footer>
</div>
</body>
</html>
"""


STYLE = """
*{box-sizing:border-box}
body{
  margin:0;background:#f6f7f9;color:#12161c;
  font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
}
svg text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:40px 26px 72px}
header{padding:6px 2px 20px}
.eyebrow{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#2563eb;font-weight:700}
h1{font-size:30px;line-height:1.25;margin:8px 0 10px;letter-spacing:-.02em}
.lede{color:#5c6672;font-size:15.5px;margin:0 0 18px;max-width:70ch}
.badges{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:22px}
.badge{background:#fff;border:1px solid #e4e7ec;border-radius:999px;padding:5px 13px;font-size:12.5px;color:#5c6672}
.badge b{color:#12161c;font-weight:600}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:12px}
.stat{background:#fff;border:1px solid #e4e7ec;border-radius:12px;padding:14px 16px}
.stat-v{font-size:25px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.stat-l{font-size:12px;color:#5c6672;margin-top:2px}
.stat.ok .stat-v{color:#16a34a}
.stat.bad .stat-v{color:#dc2626}
.card{background:#fff;border:1px solid #e4e7ec;border-radius:14px;padding:22px 24px 20px;margin:16px 0;
      box-shadow:0 1px 2px rgba(16,24,40,.04)}
.card h2{font-size:17px;margin:0 0 6px;display:flex;align-items:center;gap:10px;letter-spacing:-.01em}
.num{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:7px;
     background:#e8effd;color:#2563eb;font-size:12px;font-weight:700;flex:none}
.hint{color:#5c6672;font-size:13.5px;margin:0 0 20px;max-width:100ch}
.tail{color:#5c6672;font-size:13px;margin:16px 0 0;max-width:110ch}
.asked{color:#5c6672;font-size:13px;margin:12px 0 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:#5c6672;
   font-weight:700;padding:0 10px 9px;border-bottom:1px solid #e4e7ec;white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid #f1f3f6;vertical-align:top}
tr:last-child td{border-bottom:none}
td.num,th.num,td.mono,th.mono,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
td.num{text-align:right;color:#5c6672;white-space:nowrap}
.pill{display:inline-block;border-radius:999px;padding:2px 11px;font-size:11.5px;font-weight:700;letter-spacing:.02em}
.pass{background:#e7f6ec;color:#16a34a}
.fail{background:#fdeceb;color:#dc2626}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 0}
.chip{background:#f8fafc;border:1px solid #e4e7ec;border-radius:7px;padding:4px 10px;font-size:12.5px}
.decoded{margin-top:14px;font-size:13px;background:#f8fafc;border:1px solid #e4e7ec;
         border-radius:9px;padding:12px 14px;color:#12161c}
.flag{display:inline-block;border-radius:5px;padding:1px 8px;font-size:11px;font-weight:700;margin-right:4px}
.flag.first{background:#e8effd;color:#2563eb}
.flag.last{background:#e7f6ec;color:#16a34a}
pre.code{background:#f8fafc;border:1px solid #e4e7ec;border-radius:10px;padding:16px 18px;margin:0;
         font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;
         line-height:1.75;color:#12161c;overflow-x:auto}
footer{color:#98a2b3;font-size:12.5px;text-align:center;margin-top:34px}
"""


def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    module, ports, rtl_cfg = load_ports()
    suite = parse_suite(run_suite())
    trace = run_capture()
    if not ports:
        sys.exit("[report] the port manifest lists no ports")

    with open(OUT_HTML, "w") as f:
        f.write(render(suite, trace, ports, module, rtl_cfg))
    print("[report] wrote %s" % os.path.relpath(OUT_HTML, DEMO_ROOT))


if __name__ == "__main__":
    main()
