"""Generate the TELIPORT Round-1 submission (title slide + one content slide) from the official template.

    python scripts/make_r1_slide.py --template "Teliport S4_Submission Template.pptx" --out build/EdgeCar_R1.pptx

Every number on the slide is read from ``eval/results/*.json`` or stated in PROGRESS.md, so the slide can be rebuilt
after re-running the benchmarks. The template (TELIPORT / Tata Elxsi branding) is not part of this repository.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

BLUE = RGBColor(0x0B, 0x5F, 0xA5)
CYAN = RGBColor(0x14, 0xA0, 0xDC)
AMBER = RGBColor(0xF2, 0xA9, 0x00)
INK = RGBColor(0x17, 0x20, 0x2E)
GREY = RGBColor(0x5B, 0x65, 0x73)
LIGHT = RGBColor(0xEE, 0xF5, 0xFB)
LINE = RGBColor(0xC9, 0xD9, 0xE8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED = RGBColor(0xC6, 0x2E, 0x2E)
GREEN = RGBColor(0x1E, 0x8E, 0x5A)
FONT = "Segoe UI"


def text(
    slide,
    x,
    y,
    w,
    h,
    runs,
    *,
    size=10,
    color=INK,
    bold=False,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
    fill=None,
    line=None,
    shape=MSO_SHAPE.RECTANGLE,
    margin=0.05,
    spacing=1.0,
):
    """A box with one or more paragraphs; each paragraph is a list of (text, {overrides}) runs or a plain string."""
    shp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(0.75)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        shp.adjustments[0] = 0.08
    tf = shp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", Inches(margin))
    paragraphs = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        for chunk in [para] if isinstance(para, str) else para:
            content, opts = (chunk, {}) if isinstance(chunk, str) else chunk
            r = p.add_run()
            r.text = content
            f = r.font
            f.name = FONT
            f.size = Pt(opts.get("size", size))
            f.bold = opts.get("bold", bold)
            f.italic = opts.get("italic", False)
            f.color.rgb = opts.get("color", color)
    return shp


def arrow(slide, x1, y1, x2, y2, color=GREY, width=1.25):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    tail = ln.makeelement("{http://schemas.openxmlformats.org/drawingml/2006/main}tailEnd", {"type": "triangle"})
    ln.append(tail)
    return c


def heading(slide, x, y, w, label, color=BLUE):
    text(slide, x, y, w, 0.28, [[(label.upper(), {"bold": True, "color": color, "size": 10})]], margin=0.0)
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y + 0.28), Inches(0.55), Emu(36000))
    bar.fill.solid()
    bar.fill.fore_color.rgb = AMBER
    bar.line.fill.background()


def build(template: Path, out: Path, results: Path) -> None:
    prs = Presentation(str(template))
    e2e = json.loads((results / "e2e-qwen1.5b-edge-t2.json").read_text())
    heldout = json.loads((results / "test-hybrid-qwen2.5-1.5b-instruct-q4_k_m.json").read_text())

    # ---------------------------------------------------------------------------------------- title slide
    s1 = prs.slides[0]
    for shp in s1.shapes:
        if shp.has_text_frame and shp.text_frame.text.strip().startswith("Case Study No"):
            shp.text_frame.paragraphs[0].runs[0].text = "Case Study No. 5"
            shp.width = Inches(4.2)
            shp.left = Inches((13.333 - 4.2) / 2)
        elif shp.is_placeholder and shp.placeholder_format.idx == 1:  # subtitle
            tf = shp.text_frame
            tf.text = ""
            lines = [
                ("Team sidemen", 22, True, INK),
                ("Meet Kabra  ·  Aditya Mishra", 18, False, INK),
                ("JECRC University", 16, False, GREY),
            ]
            for i, (t, sz, b, col) in enumerate(lines):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.alignment = PP_ALIGN.CENTER
                r = p.add_run()
                r.text, r.font.size, r.font.bold, r.font.color.rgb, r.font.name = t, Pt(sz), b, col, FONT

    # -------------------------------------------------------------------------------------- content slide
    s = prs.slides[1]
    for shp in list(s.shapes):
        if shp.is_placeholder:
            shp._element.getparent().remove(shp._element)

    text(
        s,
        0.35,
        0.86,
        12.65,
        0.5,
        [
            [
                ("EdgeCar AI", {"bold": True, "color": BLUE, "size": 24}),
                ("   Ask your car what happened — offline, on-device, in under 2 seconds", {"size": 17, "color": INK}),
            ]
        ],
        anchor=MSO_ANCHOR.MIDDLE,
        margin=0.0,
    )
    text(
        s,
        0.35,
        1.35,
        12.65,
        0.3,
        "A small language model only interprets the question; deterministic analytics compute every number from real, "
        "multi-protocol vehicle data — on 2 GB RAM and 2 CPU cores.",
        size=10.5,
        color=GREY,
        margin=0.0,
    )

    # ---- column 1: architecture ------------------------------------------------------------------------
    cx, cw = 0.35, 4.75
    heading(s, cx, 1.78, cw, "Architecture — edge deployment")
    zone = text(s, cx, 2.15, cw, 4.55, "", fill=LIGHT, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    zone.adjustments[0] = 0.04
    text(
        s,
        cx + 0.12,
        2.2,
        cw - 0.24,
        0.26,
        [
            [
                (
                    "AI zone: isolated VM on the vehicle HPC · safety (ASIL) domains untouched",
                    {"size": 8.5, "color": GREY, "italic": True},
                )
            ]
        ],
        margin=0.0,
    )

    def box(x, y, w, h, title, sub, color=BLUE):
        text(
            s,
            x,
            y,
            w,
            h,
            [[(title, {"bold": True, "size": 9.5, "color": WHITE})], [(sub, {"size": 8, "color": WHITE})]],
            fill=color,
            shape=MSO_SHAPE.ROUNDED_RECTANGLE,
            anchor=MSO_ANCHOR.MIDDLE,
            margin=0.07,
            spacing=0.95,
        )

    bx = cx + 0.15
    bw = cw - 0.3
    box(
        bx,
        2.5,
        bw,
        0.6,
        "Vehicle buses — listen-only, no transmit path",
        "CAN · CAN FD · SOME/IP · Android VHAL · QNX slog2 · UDS diagnostics · ADAS",
        INK,
    )
    arrow(s, cx + cw / 2, 3.1, cx + cw / 2, 3.24)
    box(bx, 3.24, bw / 2 - 0.05, 0.62, "Ingest & decode", "real DBCs · ISO-TP · pcap · ~15 ms to queryable")
    box(
        bx + bw / 2 + 0.05, 3.24, bw / 2 - 0.05, 0.62, "Encrypted store", "DuckDB AES-256 · 1 Hz → 1 min → trips/events"
    )
    arrow(s, cx + cw / 2, 3.86, cx + cw / 2, 4.0)
    text(
        s,
        bx,
        4.0,
        bw,
        0.38,
        [
            [
                ("Question  ", {"bold": True, "color": INK, "size": 9.5}),
                ("“Why has my range dropped over the last two weeks?”", {"italic": True, "size": 9, "color": GREY}),
            ]
        ],
        fill=WHITE,
        line=LINE,
        shape=MSO_SHAPE.ROUNDED_RECTANGLE,
        anchor=MSO_ANCHOR.MIDDLE,
        margin=0.07,
    )
    arrow(s, cx + cw / 2, 4.38, cx + cw / 2, 4.52)
    fast = e2e["latency_ms_fast"]["n"] / e2e["questions"]
    box(
        bx,
        4.52,
        bw / 2 - 0.05,
        0.7,
        f"Rules fast path · {fast:.0%}",
        f"deterministic, p50 {e2e['latency_ms_fast']['p50']:.0f} ms",
        CYAN,
    )
    box(
        bx + bw / 2 + 0.05,
        4.52,
        bw / 2 - 0.05,
        0.7,
        "SLM for the long tail",
        "Qwen2.5-1.5B · llama.cpp · grammar-locked 2–6 token command",
        CYAN,
    )
    arrow(s, cx + cw / 2, 5.22, cx + cw / 2, 5.36)
    box(
        bx,
        5.36,
        bw,
        0.56,
        "Deterministic analytics — every number computed, never generated",
        "trends · comparisons · anomalies · factor attribution · root-cause timelines",
    )
    arrow(s, cx + cw / 2, 5.92, cx + cw / 2, 6.06)
    box(
        bx,
        6.06,
        bw,
        0.52,
        "Grounded answer + evidence",
        "driver · service engineer · fleet views with privacy roles",
        GREEN,
    )

    # ---- column 2: measured -----------------------------------------------------------------------------
    mx, mw = 5.3, 3.75
    heading(s, mx, 1.78, mw, "Measured, not claimed")
    lat = e2e["latency_ms_all"]
    tiles = [
        (f"{heldout['accuracy']:.1%}", "question → query accuracy on a held-out set (70 questions never tuned on)"),
        (
            f"p95 {lat['p95'] / 1000:.2f} s",
            f"end-to-end, model loaded, 2 threads; {lat['under_2s_pct']:.0f} % of "
            f"{e2e['questions']} questions under 2 s",
        ),
        (f"{e2e['peak_rss_mb']['total'] / 1000:.2f} GB", "peak RAM, assistant + model server (2 GB budget)"),
        ("9 / 9", "fault-scenario events recovered from 5 protocols (QNX, UDS, CAN FD, SOME/IP, VHAL)"),
        ("15 ms", "live ingest: each second of bus traffic queryable (67× real time)"),
    ]
    ty = 2.15
    for big, small in tiles:
        text(s, mx, ty, mw, 0.6, "", fill=WHITE, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
        text(
            s,
            mx + 0.08,
            ty + 0.04,
            1.25,
            0.52,
            [[(big, {"bold": True, "size": 15, "color": BLUE})]],
            anchor=MSO_ANCHOR.MIDDLE,
            margin=0.0,
        )
        text(
            s,
            mx + 1.35,
            ty + 0.04,
            mw - 1.43,
            0.52,
            small,
            size=8.5,
            color=INK,
            anchor=MSO_ANCHOR.MIDDLE,
            margin=0.0,
            spacing=0.95,
        )
        ty += 0.66
    heading(s, mx, 5.5, mw, "Real data, real encodings")
    text(
        s,
        mx,
        5.85,
        mw,
        0.85,
        [
            [
                ("VED", {"bold": True, "size": 8.5}),
                (": 1 year of Nissan Leaf driving, replayed as real Leaf CAN frames", {"size": 8.5}),
            ],
            [
                ("TU Munich BMW i3", {"bold": True, "size": 8.5}),
                (": summer + winter battery/thermal trips", {"size": 8.5}),
            ],
            [
                ("BATON", {"bold": True, "size": 8.5}),
                (": 3,593 real ADAS handovers time the ADAS model", {"size": 8.5}),
            ],
            [("TD2D (CARLA)", {"bold": True, "size": 8.5}), (": 500 measured takeover reactions", {"size": 8.5})],
        ],
        color=INK,
        margin=0.0,
        spacing=0.95,
    )

    # ---- column 3: sample answers ----------------------------------------------------------------------
    ax, aw = 9.25, 3.75
    heading(s, ax, 1.78, aw, "Real answers (BMW i3 · Nissan Leaf)")
    text(
        s,
        ax,
        2.15,
        aw,
        0.42,
        [
            [
                (
                    "“What happened before the battery overheating warning?”",
                    {"italic": True, "bold": True, "size": 9.5, "color": INK},
                )
            ]
        ],
        fill=LIGHT,
        line=LINE,
        shape=MSO_SHAPE.ROUNDED_RECTANGLE,
        anchor=MSO_ANCHOR.MIDDLE,
        margin=0.08,
    )
    text(
        s,
        ax,
        2.62,
        aw,
        0.62,
        [
            [
                ("Likely root cause: ", {"bold": True, "color": RED, "size": 9}),
                ("coolant pump ‘A’ fault (DTC P0C73), 14.5 min before the warning.", {"size": 9}),
            ],
        ],
        margin=0.04,
    )
    timeline = [
        ("13:11", "pump feedback lost", "QNX"),
        ("13:11", "DTC P0C73 stored", "UDS"),
        ("13:11", "fast temperature rise", "CAN FD"),
        ("13:21", "drive power limited", "SOME/IP"),
        ("13:25", "warning shown · 55 °C", "VHAL"),
    ]
    yy = 3.2
    for t, what, src in timeline:
        dot = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(ax + 0.08), Inches(yy + 0.08), Inches(0.1), Inches(0.1))
        dot.fill.solid()
        dot.fill.fore_color.rgb = AMBER if src != "VHAL" else RED
        dot.line.fill.background()
        text(
            s,
            ax + 0.25,
            yy,
            aw - 0.25,
            0.26,
            [
                [
                    (f"{t}  ", {"bold": True, "size": 8.5}),
                    (what, {"size": 8.5}),
                    (f"   {src}", {"size": 8, "color": CYAN, "bold": True}),
                ]
            ],
            margin=0.0,
        )
        yy += 0.27
    text(
        s,
        ax,
        4.68,
        aw,
        0.42,
        [
            [
                (
                    "“Why has my range dropped over the last two weeks?”",
                    {"italic": True, "bold": True, "size": 9.5, "color": INK},
                )
            ]
        ],
        fill=LIGHT,
        line=LINE,
        shape=MSO_SHAPE.ROUNDED_RECTANGLE,
        anchor=MSO_ANCHOR.MIDDLE,
        margin=0.08,
    )
    text(
        s,
        ax,
        5.15,
        aw,
        0.95,
        [
            [
                ("161 → 151 km", {"bold": True, "size": 11, "color": BLUE}),
                (" as consumption rose 13.2 → 14.1 kWh/100 km:", {"size": 9}),
            ],
            [("colder air, battery & tyres ≈ −7 km · cabin heating ≈ −5 km", {"size": 9})],
            [("(6.9 °C vs 10.8 °C; 10 trips vs 14 trips — real Michigan data)", {"size": 8, "color": GREY})],
        ],
        margin=0.04,
        spacing=0.95,
    )
    text(
        s,
        ax,
        6.1,
        aw,
        0.55,
        [
            [("Driver · service engineer · fleet", {"bold": True, "size": 9, "color": GREEN})],
            [("same numbers, role-based privacy (location, behaviour, raw logs)", {"size": 8.5, "color": INK})],
        ],
        margin=0.04,
        spacing=0.95,
    )

    # ---- footer -----------------------------------------------------------------------------------------
    band = text(s, 0.35, 6.86, 12.65, 0.42, "", fill=LIGHT, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    band.adjustments[0] = 0.2
    footer = [
        (0.5, 4.6, "Scales", "Pi / 2-core VM (0.5–1.5B) → vehicle HPC (3–8B, NPU)"),
        (5.1, 3.9, "Safety", "read-only buses · AI isolated · no cloud"),
        (9.0, 3.9, "Open source", "github.com/meet-png/edgecar-ai"),
    ]
    for fx, fw, label, body in footer:
        text(
            s,
            fx,
            6.86,
            fw,
            0.42,
            [[(f"{label}  ", {"bold": True, "color": BLUE, "size": 9.5}), (body, {"size": 9.5})]],
            anchor=MSO_ANCHOR.MIDDLE,
            margin=0.0,
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("build/EdgeCar_TELIPORT_R1.pptx"))
    ap.add_argument("--results", type=Path, default=Path("eval/results"))
    a = ap.parse_args()
    build(a.template, a.out, a.results)


if __name__ == "__main__":
    main()
