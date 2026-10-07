#!/usr/bin/env python3
#
# Verilator + cocotb + LitePCIe minimal demo
# SVG drawing helpers for the HTML report.
#
# Everything here is plain string SVG so the generated report is a single
# self-contained file: no external assets, no CDN, no JavaScript dependencies.
#
import html

# --------------------------------------------------------------------------------
# Palette (light theme)
# --------------------------------------------------------------------------------
C = {
    "ink"      : "#12161c",
    "muted"    : "#5c6672",
    "faint"    : "#98a2b3",
    "line"     : "#e4e7ec",
    "grid"     : "#eef1f5",
    "panel"    : "#f8fafc",
    "blue"     : "#2563eb",
    "blue_bg"  : "#e8effd",
    "teal"     : "#0d9488",
    "teal_bg"  : "#e0f2f1",
    "purple"   : "#7c3aed",
    "purple_bg": "#f0e9fe",
    "amber"    : "#d97706",
    "amber_bg" : "#fdf2e3",
    "green"    : "#16a34a",
    "green_bg" : "#e7f6ec",
    "red"      : "#dc2626",
    "pink"     : "#db2777",
    "pink_bg"  : "#fdeaf3",
    "slate"    : "#64748b",
    "slate_bg" : "#f1f5f9",
}

# Colour assigned to each port group of the DUT.
GROUP_COLOR = {
    "req"     : C["blue"],
    "source"  : C["purple"],
    "sys"     : C["slate"],
}

MONO_W = 7.0      # rough advance width of one mono character at 12px


def esc(s):
    return html.escape(str(s), quote=True)


# --------------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------------
def rect(x, y, w, h, fill, stroke="none", rx=0, sw=1, dash=None, opacity=None):
    s = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" '
         f'rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"')
    if dash:
        s += f' stroke-dasharray="{dash}"'
    if opacity is not None:
        s += f' opacity="{opacity}"'
    return s + "/>"


def line(x1, y1, x2, y2, stroke, sw=1, dash=None, cap="butt"):
    s = (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
         f'stroke="{stroke}" stroke-width="{sw}" stroke-linecap="{cap}"')
    if dash:
        s += f' stroke-dasharray="{dash}"'
    return s + "/>"


def text(x, y, s, size=12, fill=None, anchor="start", weight="400", mono=False,
         opacity=None):
    fill = fill or C["ink"]
    style = "font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;" if mono else ""
    extra = f' opacity="{opacity}"' if opacity is not None else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}" style="{style}"{extra}>'
            f'{esc(s)}</text>')


def polyline(points, stroke, sw=1.6, fill="none"):
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polyline points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'


def path(d, stroke, sw=1.6, fill="none"):
    return f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'


def svg(width, height, inner, title="", desc=""):
    t = f"<title>{esc(title)}</title>" if title else ""
    d = f"<desc>{esc(desc)}</desc>" if desc else ""
    return (f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" '
            f'preserveAspectRatio="xMidYMid meet" role="img" '
            f'xmlns="http://www.w3.org/2000/svg">{t}{d}{inner}</svg>')


def arrow_down(x, y1, y2, color, sw=1.6):
    """A short downward arrow from y1 to y2."""
    d = (f"M {x:.1f} {y1:.1f} L {x:.1f} {y2 - 7:.1f} "
         f"M {x - 4:.1f} {y2 - 8:.1f} L {x:.1f} {y2:.1f} L {x + 4:.1f} {y2 - 8:.1f}")
    return path(d, color, sw=sw)


# --------------------------------------------------------------------------------
# Section: the flow, from Python source to a passing test
# --------------------------------------------------------------------------------
def pipeline(stages, arrows, width=1100):
    """stages: list of {title, sub, color}; arrows: list of str (len = n-1)."""
    box_w, box_h, gap = 720, 58, 46
    x = (width - box_w) / 2
    top = 16
    body = []
    y = top
    for i, st in enumerate(stages):
        color = st["color"]
        body.append(rect(x, y, box_w, box_h, "#ffffff", stroke=C["line"], rx=10, sw=1.4))
        body.append(rect(x, y, 5, box_h, color, rx=2.5))
        body.append(text(x + 22, y + 25, st["title"], size=14, weight="600"))
        body.append(text(x + 22, y + 45, st["sub"], size=11.5, fill=C["muted"], mono=True))
        if i < len(arrows):
            body.append(arrow_down(x + box_w / 2, y + box_h + 6, y + box_h + gap - 6, color))
            body.append(text(x + box_w / 2 + 14, y + box_h + gap / 2 + 4,
                             arrows[i], size=12, fill=C["muted"], mono=True))
        y += box_h + gap
    height = y - gap + top
    return svg(width, height, "".join(body), title="Tool flow")


# --------------------------------------------------------------------------------
# Section: DUT port map, parsed from the generated Verilog
# --------------------------------------------------------------------------------
def port_map(ports, module_name, width=1100):
    """ports: list of {dir, width, name} in declaration order; width in bits."""
    ins = [p for p in ports if p["dir"] == "input"]
    outs = [p for p in ports if p["dir"] != "input"]
    rows = max(len(ins), len(outs))
    row_h = 21
    box_x, box_w = 430, 250
    box_y = 40
    box_h = rows * row_h + 30

    body = []

    def dim_of(port):
        # Verilog's own range notation, so it reads the same as the .v file.
        return "[%d:0]" % (port["width"] - 1) if port["width"] > 1 else ""

    def label(port, y, side):
        group = port["name"].split("_")[0]
        color = GROUP_COLOR.get(group, C["slate"])
        dim = dim_of(port)
        if side == "in":
            dim_w = len(dim) * 6.4
            body.append(text(box_x - 16, y + 4, dim, size=11, fill=C["faint"],
                             anchor="end", mono=True))
            body.append(text(box_x - 16 - dim_w - 8, y + 4, port["name"], size=12,
                             fill=color, anchor="end", mono=True))
        else:
            body.append(text(box_x + box_w + 16, y + 4, port["name"], size=12,
                             fill=color, mono=True))
            if dim:
                body.append(text(box_x + box_w + 16 + len(port["name"]) * MONO_W + 8,
                                 y + 4, dim, size=11, fill=C["faint"], mono=True))

    body.append(rect(box_x, box_y, box_w, box_h, C["panel"], stroke=C["line"], rx=10, sw=1.4))
    body.append(text(box_x + box_w / 2, box_y + box_h / 2 - 6, module_name,
                     size=12.5, weight="600", anchor="middle", mono=True))
    body.append(text(box_x + box_w / 2, box_y + box_h / 2 + 12,
                     f'{len(ins)} inputs / {len(outs)} outputs',
                     size=11, fill=C["muted"], anchor="middle"))
    body.append(text(box_x + box_w / 2, box_y - 14, "generated top level",
                     size=11, fill=C["faint"], anchor="middle"))

    for i, p in enumerate(ins):
        label(p, box_y + 24 + i * row_h, "in")
    for i, p in enumerate(outs):
        label(p, box_y + 24 + i * row_h, "out")

    legend_y = box_y + box_h + 36
    lx = 260
    for group, lbl in (("req", "req_sink"), ("source", "source"), ("sys", "clock / reset")):
        body.append(rect(lx, legend_y - 9, 10, 10, GROUP_COLOR[group], rx=2))
        body.append(text(lx + 16, legend_y, lbl, size=11, fill=C["muted"], mono=True))
        lx += 34 + len(lbl) * 6.6 + 26

    return svg(width, legend_y + 20, "".join(body),
               title="DUT port map",
               desc=f"{len(ins)} inputs and {len(outs)} outputs of {module_name}")


# --------------------------------------------------------------------------------
# Section: TLP request header, field by field
# --------------------------------------------------------------------------------
HEADER_FIELDS = [
    (0, "Fmt",            31, 29, "blue"),
    (0, "Type",           28, 24, "purple"),
    (0, "TC",             22, 20, "slate"),
    (0, "Length",          9,  0, "teal"),
    (1, "Requester ID",   31, 16, "purple"),
    (1, "Tag",            15,  8, "amber"),
    (1, "Last BE",         7,  4, "pink"),
    (1, "First BE",        3,  0, "pink"),
    (2, "Address[63:32]", 31,  0, "green"),
    (3, "Address[31:0]",  31,  0, "green"),
]


def tlp_header(dws, width=1100):
    left, right = 122, 1030
    span = right - left
    px = span / 32.0
    body = []
    y = 30

    # bit ruler
    for b in range(32):
        bx = left + (31 - b) * px
        if b % 8 == 0 or b == 31:
            body.append(line(bx, y, bx, y + 8, C["faint"], 1))
            body.append(text(bx, y - 4, str(b), size=10, fill=C["faint"], anchor="middle"))
    y += 14
    body.append(line(left, y, right, y, C["line"], 1))
    y += 20

    dw_labels = ["DW0", "DW1", "DW2", "DW3"]
    for dw in range(4):
        body.append(text(left - 20, y + 24, dw_labels[dw], size=13,
                         weight="600", anchor="end", fill=C["muted"]))
        body.append(text(left - 20, y + 40, "0x%08x" % dws[dw], size=9.5,
                         anchor="end", fill=C["faint"], mono=True))
        body.append(rect(left, y, span, 40, "#ffffff", stroke=C["line"], rx=6, sw=1.2))

        for (d, name, msb, lsb, ckey) in HEADER_FIELDS:
            if d != dw:
                continue
            fx = left + (31 - msb) * px
            fw = (msb - lsb + 1) * px
            val = (dws[dw] >> lsb) & ((1 << (msb - lsb + 1)) - 1)
            body.append(rect(fx + 1.5, y + 1.5, fw - 3, 37, C[ckey + "_bg"], rx=4))
            body.append(text(fx + fw / 2, y + 16, name, size=10.5,
                             fill=C[ckey], anchor="middle", weight="600"))
            body.append(text(fx + fw / 2, y + 32, "0x%x" % val, size=11.5,
                             fill=C["ink"], anchor="middle", mono=True, weight="600"))
        y += 40 + 28

    return svg(width, y + 4, "".join(body),
               title="Decoded 4DW TLP request header",
               desc="Field positions and values of the request header produced by the DUT")


# --------------------------------------------------------------------------------
# Section: cycle-accurate bus waveform
# --------------------------------------------------------------------------------
def waveform(view, width=1100, title="Bus waveform"):
    """view: list of per-cycle row dicts, already sliced to the window."""
    n = len(view)
    lbl_w, cw = 182, 34
    head_h = 24

    def xof(i):
        return lbl_w + i * cw

    body = []

    # Cycle index strip
    for i in range(n):
        body.append(rect(xof(i), 0, cw, head_h, C["panel"] if i % 2 == 0 else "#ffffff"))
        body.append(text(xof(i) + cw / 2, 15, str(i), size=10.5, fill=C["muted"],
                         anchor="middle", mono=True))

    # Which beat each bus lane is offering, cycle by cycle
    req_lab, src_lab = [], []
    rb = sb = 0
    for r in view:
        req_lab.append("#%d" % rb if r["v_req"] else None)
        src_lab.append("b%d" % sb if r["v_src"] else None)
        rb += r["x_req"]
        sb += r["x_src"]

    y = head_h

    def row_grid(y0, y1):
        for i in range(n + 1):
            body.append(line(xof(i), y0, xof(i), y1, C["grid"], 1))

    def clock_row(label):
        nonlocal y
        top, h = y, 26
        hi, lo = top + 7, top + 19
        body.append(text(lbl_w - 12, (hi + lo) / 2 + 4, label, size=11.5,
                         fill=C["faint"], anchor="end", mono=True))
        row_grid(top, top + h)
        d = f"M {xof(0):.1f} {hi:.1f}"
        for i in range(n):
            d += (f" L {xof(i) + cw / 2:.1f} {hi:.1f} L {xof(i) + cw / 2:.1f} {lo:.1f}"
                  f" L {xof(i + 1):.1f} {lo:.1f} L {xof(i + 1):.1f} {hi:.1f}")
        body.append(path(d, C["faint"], 1.8))
        y = top + h

    def bit_row(label, color, value_of):
        nonlocal y
        top, h = y, 26
        hi, lo = top + 7, top + 19
        body.append(text(lbl_w - 12, (hi + lo) / 2 + 4, label, size=11.5,
                         fill=color, anchor="end", mono=True))
        row_grid(top, top + h)
        for i, r in enumerate(view):
            if value_of(i, r):
                body.append(rect(xof(i), top + 2, cw, h - 6, color, opacity=0.09))
        body.append(line(xof(0), lo, xof(n), lo, C["grid"], 1))

        # A logic level changes instantaneously, so each transition needs TWO
        # points at the same x (hold the old level, then jump). Feeding a single
        # point per change draws a diagonal ramp instead of a square edge.
        pts, prev = [], None
        for i, r in enumerate(view):
            v = value_of(i, r)
            x = xof(i)
            if prev is None:
                pts.append((x, hi if v else lo))
            elif v != prev:
                pts.append((x, hi if prev else lo))
                pts.append((x, hi if v else lo))
            prev = v
        pts.append((xof(n), hi if prev else lo))
        body.append(polyline(pts, color, 1.8))
        y = top + h

    def bus_row(label, color, bg, lab_of, dat_of, val_of, xkey):
        nonlocal y
        top, h = y + 3, 24
        body.append(text(lbl_w - 12, top + 16, label, size=11.5,
                         fill=color, anchor="end", mono=True))
        row_grid(y, y + 32)
        i = 0
        while i < n:
            if not val_of(i):
                i += 1
                continue
            j = i
            while j + 1 < n and val_of(j + 1) and dat_of(j + 1) == dat_of(i):
                j += 1
            x1, x2 = xof(i), xof(j + 1)
            body.append(rect(x1 + 1, top, x2 - x1 - 2, h, "#ffffff",
                             stroke=color, rx=3, sw=1.2))
            # cycles where the beat is actually consumed get a filled block
            for k in range(i, j + 1):
                if view[k][xkey]:
                    body.append(rect(xof(k) + 2, top + 2, cw - 4, h - 4, bg, rx=2))
            lab = next((lab_of(k) for k in range(i, j + 1) if lab_of(k)), "")
            if lab:
                body.append(text((x1 + x2) / 2, top + 16, lab, size=10.5,
                                 fill=color, anchor="middle", mono=True, weight="600"))
            i = j + 1
        y += 32

    clock_row("sys_clk")
    bit_row("req_sink_valid", C["blue"],   lambda i, r: r["v_req"])
    bit_row("req_sink_ready", C["teal"],   lambda i, r: r["r_req"])
    bus_row("req_sink", C["blue"], C["blue_bg"], lambda i: req_lab[i],
            lambda i: view[i]["dat_req"], lambda i: view[i]["v_req"], "x_req")
    bit_row("source_valid",   C["purple"], lambda i, r: r["v_src"])
    bit_row("source_ready",   C["teal"],   lambda i, r: r["r_src"])
    bus_row("source", C["purple"], C["purple_bg"], lambda i: src_lab[i],
            lambda i: view[i]["dat_src"], lambda i: view[i]["v_src"], "x_src")

    # Transfer markers
    top = y + 4
    row_grid(y, top + 22)
    body.append(text(lbl_w - 12, top + 15, "handshake", size=11.5,
                     fill=C["muted"], anchor="end", mono=True))
    for i, r in enumerate(view):
        cx = xof(i) + cw / 2
        if r["x_req"]:
            body.append(path(f"M {cx - 5:.1f} {top + 6:.1f} L {cx + 5:.1f} {top + 6:.1f} "
                             f"L {cx:.1f} {top + 17:.1f} Z", "none", fill=C["blue"]))
        if r["x_src"]:
            body.append(path(f"M {cx - 5:.1f} {top + 6:.1f} L {cx + 5:.1f} {top + 6:.1f} "
                             f"L {cx:.1f} {top + 17:.1f} Z", "none", fill=C["purple"]))
    y = top + 26

    return svg(width, y, "".join(body), title=title,
               desc="Cycle-by-cycle view of the request and response buses")


# --------------------------------------------------------------------------------
# Section: backpressure comparison
# --------------------------------------------------------------------------------
def backpressure_chart(golden, stressed, bubbles, seed, width=1100):
    """golden / stressed: lists of beat dicts carrying a `wait_cycles` field."""
    unit, left, row_h, top = 46, 262, 62, 52
    body = []

    total = max(sum(b["wait_cycles"] + 1 for b in golden),
                sum(b["wait_cycles"] + 1 for b in stressed))
    for c in range(total + 1):
        x = left + c * unit
        body.append(line(x, top - 22, x, top + row_h * 2 - 10, C["grid"], 1))
        body.append(text(x, top - 30, str(c), size=10, fill=C["faint"],
                         anchor="middle", mono=True))
    body.append(text(left - 18, top - 30, "cycle", size=10.5, fill=C["faint"],
                     anchor="end", mono=True))

    def row(label, beats, y, color, bg):
        body.append(text(left - 18, y + 34, label, size=12,
                         anchor="end", fill=C["muted"], mono=True))
        x = left
        cycles = 0
        for i, b in enumerate(beats):
            occ = b["wait_cycles"] + 1
            w = occ * unit
            cycles += occ
            body.append(rect(x + 2, y + 10, w - 4, 44, bg, stroke=color, rx=6, sw=1.4))
            body.append(text(x + w / 2, y + 30, "b%d" % i, size=12,
                             fill=color, anchor="middle", weight="600", mono=True))
            body.append(text(x + w / 2, y + 46, "%d cyc" % occ, size=10,
                             fill=C["muted"], anchor="middle", mono=True))
            x += w
        # Note: `x - left` is a pixel span, not a cycle count. Beats are drawn
        # `unit` px per cycle, so the two agree numerically only by accident.
        body.append(text(x + 16, y + 36, "%d cycles total" % cycles, size=11,
                         fill=C["faint"], mono=True))

    row("ready = 1", golden, top, C["green"], C["green_bg"])
    row(f"ready = random (seed {seed})", stressed, top + row_h, C["amber"], C["amber_bg"])

    note_y = top + row_h * 2 + 24
    body.append(text(left, note_y,
                     f"{bubbles} wait states absorbed: cycles with source_valid=1 and "
                     f"source_ready=0. Not one beat was dropped.",
                     size=12, fill=C["muted"]))

    return svg(width, note_y + 22, "".join(body),
               title="Backpressure comparison",
               desc="Cycles each output beat occupied the bus, always-ready versus random backpressure")


# --------------------------------------------------------------------------------
# Section: golden relation  valid_dwords == 4 + length
# --------------------------------------------------------------------------------
def scaling_chart(scaling, width=1100):
    left, right = 130, 1010
    base, top = 300, 66
    n = len(scaling)
    slot = (right - left) / n
    bar_w = slot * 0.46
    vmax = max(s["valid_dwords"] for s in scaling)

    def yof(v):
        return base - (base - top) * (v / vmax)

    body = []
    for g in range(0, vmax + 1, 2):
        y = yof(g)
        body.append(line(left - 10, y, right, y, C["grid"], 1))
        body.append(text(left - 18, y + 4, str(g), size=10.5, fill=C["faint"],
                         anchor="end", mono=True))
    body.append(text(left - 18, top - 26, "valid", size=10.5, fill=C["faint"], anchor="end"))
    body.append(text(left - 18, top - 14, "dwords", size=10.5, fill=C["faint"], anchor="end"))

    pts = []
    for i, s in enumerate(scaling):
        cx = left + slot * (i + 0.5)
        y = yof(s["valid_dwords"])
        body.append(rect(cx - bar_w / 2, y, bar_w, base - y, C["blue_bg"],
                         stroke=C["blue"], rx=6, sw=1.5))
        body.append(text(cx, y - 10, str(s["valid_dwords"]), size=13,
                         fill=C["blue"], anchor="middle", weight="700", mono=True))
        body.append(text(cx, base + 22, "length = %d" % s["length"], size=12,
                         fill=C["ink"], anchor="middle", mono=True))
        body.append(text(cx, base + 40,
                         "%d beat%s" % (s["beats"], "" if s["beats"] == 1 else "s"),
                         size=10.5, fill=C["muted"], anchor="middle", mono=True))
        pts.append((cx, yof(s["length"] + 4)))

    body.append(polyline(pts, C["green"], 2.2))
    for x, y in pts:
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#ffffff" '
                    f'stroke="{C["green"]}" stroke-width="2"/>')
    body.append(line(left, base, right, base, C["line"], 1.4))
    body.append(text(right, base + 62,
                     "green markers = 4 header DW + requested length (upstream golden rule)",
                     size=11, fill=C["green"], anchor="end"))

    return svg(width, base + 76, "".join(body),
               title="Header plus payload scaling",
               desc="Valid dwords emitted versus requested length")
