"""Generate the TELIPORT Round-1 submission (title slide + one content slide) from the official template.

    python scripts/make_r1_slide.py --template "Teliport S4_Submission Template.pptx"

Every number on the slide is read from ``eval/results`` (the ARM run, held-out and refusal evaluations, the root-cause
and storage benchmarks, the brief's questions), so the slide can be rebuilt after re-running the benchmarks. Text uses
the template's own font (Calibri) and every text/background pair is checked for WCAG AA contrast (4.5:1) before the
file is written. The template (TELIPORT / Tata Elxsi branding) is not part of this repository.
"""

from __future__ import annotations

import argparse
import io
import json
from datetime import datetime
from pathlib import Path

import segno
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

FONT = "Calibri"  # the template's body font: ships with Office, metric-compatible substitutes elsewhere
REPO = "https://github.com/meet-png/edgecar-ai"

NAVY, BLUE, INK, MUTED = "0B3A60", "0E5A9C", "1C2430", "4B5563"
SLATE, GREEN, WHITE = "2E3A46", "1D6B40", "FFFFFF"
PALE, PALE2, ZONE, LINE = "E8F1FA", "F4F7FA", "F3F7FB", "B8C7D6"

CONTRAST: list[tuple[str, str, str]] = []  # (text, foreground, background) for every run written


def _lum(hex_: str) -> float:
    c = [int(hex_[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def box(
    slide,
    x,
    y,
    w,
    h,
    paragraphs,
    *,
    fill=None,
    line=None,
    dash=False,
    bg=WHITE,
    shape=MSO_SHAPE.ROUNDED_RECTANGLE,
    anchor=MSO_ANCHOR.MIDDLE,
    align=PP_ALIGN.LEFT,
    margin=0.07,
    radius=0.08,
    name=None,
):
    """A shape with paragraphs of runs: each paragraph is a list of (text, {size, bold, italic, color, link})."""
    shp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    if name:
        shp.name = name
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = RGBColor.from_string(fill)
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = RGBColor.from_string(line)
        shp.line.width = Pt(1.25 if dash else 0.75)
        if dash:
            shp.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        shp.adjustments[0] = radius
    tf = shp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", Inches(margin))
    for i, para in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        for content, opts in para:
            r = p.add_run()
            r.text = content
            f = r.font
            f.name = FONT
            f.size = Pt(opts.get("size", 11))
            f.bold = opts.get("bold", False)
            f.italic = opts.get("italic", False)
            color = opts.get("color", INK)
            f.color.rgb = RGBColor.from_string(color)
            if opts.get("link"):
                r.hyperlink.address = opts["link"]
            CONTRAST.append((content, color, fill or bg))
    return shp


def arrow(slide, x1, y1, x2, y2, color=MUTED, width=1.5, dash=False):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = RGBColor.from_string(color)
    c.line.width = Pt(width)
    if dash:
        c.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    ln = c.line._get_or_add_ln()
    ln.append(ln.makeelement("{http://schemas.openxmlformats.org/drawingml/2006/main}tailEnd", {"type": "triangle"}))
    return c


def t(text, **opts):
    return (text, opts)


# ------------------------------------------------------------------------------------------------- numbers


def load(results: Path) -> dict:
    j = lambda name: json.loads((results / name).read_text(encoding="utf-8"))  # noqa: E731
    # latency and memory from the real 2 GiB ARM machine (AWS t4g.small, Graviton2), the brief's questions too
    edge, held = j("graviton/r1-2026-10-28/summary.json"), j("test-hybrid-qwen2.5-1.5b-instruct-q4_k_m.json")
    rc, store = j("rootcause.json")["summary"], j("storage.json")
    refusal, refusal_before = j("refusal-test-hybrid.json"), j("refusal-test-baseline-no-guard.json")
    brief = {r["question"]: r for r in j("graviton/r1-2026-10-28/brief.json")}
    return {
        "edge": edge,
        "heldout": held["accuracy"],
        "rc": rc,
        "store_mb": store["whole_year_all_tiers_mb"],
        "refused": f"{round(refusal['accuracy'] * refusal['n'])}/{refusal['n']}",
        "refused_before": f"{round(refusal_before['accuracy'] * refusal_before['n'])}/{refusal_before['n']}",
        "brief": brief,
    }


def brief_rows(b: dict) -> list[tuple[str, str, str]]:
    """(question, route · time, short answer) — each answer composed from the facts the engine returned."""

    def route(r):
        if r["route"] == "declined":
            return "declined"
        ms = r["ms"]
        return f"{r['route']} · " + (f"{ms / 1000:.1f} s" if ms >= 300 else f"{ms:.0f} ms")

    short = {"Colder air, battery and tyres": "colder", "Cabin heating and A/C": "heating"}
    rows = []
    q = "Why has my driving range dropped over the last two weeks?"
    f = b[q]["facts"]
    steps = " → ".join(
        f"{round(x['delta_range_km']):+d}\u00a0{short.get(x['factor'], x['factor'])}" for x in f["top_factors"]
    )
    a = f"{f['range_before_km']:.0f} → {steps} → {f['other_shown']:+d}\u00a0other"
    a += f" →\u00a0{f['range_after_km']:.0f}\u00a0km"
    rows.append((q, route(b[q]), a.replace("-", "−")))

    q = "When did fuel efficiency show the largest drop?"
    f = b[q]["facts"]
    day = _day(f["day"])
    a = f"{day}: {f['value']:.2f} vs {f['baseline']:.2f}\u00a0km/kWh ({f['deviation_pct']:.0f}%)"
    a += f" at {f['day_ambient_c']:.0f}\u00a0°C"
    rows.append((q, route(b[q]), a.replace("-", "−")))

    q = "Show all ADAS disengagement events from yesterday."
    codes = [x["code"] for x in b[q]["table"]]
    label = {
        "ADAS_DISENGAGE_DRIVER_STEER": "steering",
        "ADAS_DISENGAGE_DRIVER_BRAKE": "braking",
        "ADAS_DISENGAGE_STANDSTILL": "standstill",
        "ADAS_DISENGAGE_TAKEOVER": "takeover",
        "ADAS_DISENGAGE_TAKEOVER_LATE": "late takeover",
        "ADAS_DISENGAGE_LANE_LOST": "lane lost",
        "ADAS_DISENGAGE_SENSOR_BLOCKED": "sensor blocked",
    }
    parts = []
    for code in dict.fromkeys(codes):
        n = codes.count(code)
        parts.append(label.get(code, code) + (f" ×{n}" if n > 1 else ""))
    rows.append((q, route(b[q]), f"{len(codes)} events: " + ", ".join(parts)))

    q = "What happened before the battery overheating warning?"
    f = b[q]["facts"]
    a = f"Refrigerant loss ({f['root_cause_dtc']}) {f['root_cause_lead_min']:.1f} min before"
    a += " → P0C32 → power limit → warning"
    rows.append((q, route(b[q]), a))

    q = "Compare energy consumption between city and highway driving."
    seg = {s["segment"].split(" ")[0]: s["value"] for s in b[q]["facts"]["segments"]}
    rows.append((q, route(b[q]), f"City {seg['City']:.1f} vs highway {seg['Highway']:.1f} kWh/100 km"))

    q = "Is the car using more power than it used to?"
    f = b[q]["facts"]
    tops = ", ".join(
        f"{short.get(x['factor'], x['factor'])}\u00a0{x['delta_kwh_100km']:+.1f}" for x in f["top_factors"]
    )
    rows.append((q, route(b[q]), f"{f['consumption_before']:.1f} → {f['consumption_after']:.1f} kWh/100 km: {tops}"))

    q = "Turn off lane keeping assist"
    rows.append((q, route(b[q]), "“I can't control the car — I only read its data.”"))
    return rows


def _day(value) -> str:
    d = datetime.fromisoformat(str(value))
    return f"{d:%a} {d.day} {d:%b %Y}"


# ------------------------------------------------------------------------------------------------- slides


def title_slide(s1) -> None:
    for shp in s1.shapes:
        if shp.has_text_frame and shp.text_frame.text.strip().startswith("Case Study No"):
            shp.text_frame.paragraphs[0].runs[0].text = "Case Study No. 5"
            shp.width, shp.left, shp.top = Inches(4.2), Inches((13.333 - 4.2) / 2), Inches(1.98)
        elif shp.is_placeholder and shp.placeholder_format.idx == 0:  # "Team Details"
            shp.top, shp.height = Inches(4.02), Inches(0.62)
            for r in shp.text_frame.paragraphs[0].runs:
                r.font.size = Pt(30)
        elif shp.is_placeholder and shp.placeholder_format.idx == 1:  # subtitle → team
            shp.top, shp.height = Inches(4.62), Inches(1.25)
            tf = shp.text_frame
            tf.text = ""
            for i, (text, size, bold, color) in enumerate(
                [
                    ("Team Sidemen", 22, True, INK),
                    ("Meet Kabra  ·  Aditya Mishra", 18, False, INK),
                    ("JECRC University", 16, False, MUTED),
                ]
            ):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.alignment = PP_ALIGN.CENTER
                r = p.add_run()
                r.text, r.font.size, r.font.bold, r.font.name = text, Pt(size), bold, FONT
                r.font.color.rgb = RGBColor.from_string(color)
    box(
        s1,
        1.67,
        2.62,
        10.0,
        1.25,
        [
            [t("Natural Language Vehicle Information using On-Device SLM/LLM (VehicleGPT)", size=16, color=INK)],
            [t("EdgeCar AI", size=28, bold=True, color=BLUE)],
            [t("Ask your car what happened — offline, on-device, in under 2 seconds", size=16, italic=True, color=INK)],
        ],
        shape=MSO_SHAPE.RECTANGLE,
        align=PP_ALIGN.CENTER,
        bg="E3F2FB",  # the template's pale-blue artwork behind the centre of the slide
        name="Solution",
    )


def content_slide(s, n: dict, qr_png: bytes) -> None:
    edge, rc, b = n["edge"], n["rc"], n["brief"]
    for shp in list(s.shapes):  # keep the logos and the title placeholder; drop the empty body placeholder
        if shp.is_placeholder and shp.placeholder_format.idx != 0:
            shp._element.getparent().remove(shp._element)
    title = next(shp for shp in s.shapes if shp.is_placeholder)
    title.left, title.top, title.width, title.height = Inches(0.4), Inches(0.86), Inches(12.53), Inches(0.56)
    tf = title.text_frame
    tf.word_wrap, tf.vertical_anchor = True, MSO_ANCHOR.MIDDLE
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", 0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    for text, size, bold, color in [
        ("EdgeCar AI", 28, True, NAVY),
        ("   Ask your car what happened — offline, on-device, under 2 s on two ARM cores", 18, False, INK),
    ]:
        r = p.add_run()
        r.text, r.font.size, r.font.bold, r.font.name = text, Pt(size), bold, FONT
        r.font.color.rgb = RGBColor.from_string(color)
        CONTRAST.append((text, color, WHITE))

    slm, fast = edge["latency_ms"]["slm"], edge["latency_ms"]["fast"]

    # ---- left: where it runs and how data and questions flow -------------------------------------------------------
    box(
        s,
        0.4,
        1.48,
        6.05,
        0.3,
        [
            [
                t(
                    "Vehicle HPC — hypervisor-separated domains (ISO 26262 freedom from interference)",
                    size=10.5,
                    italic=True,
                    color=MUTED,
                )
            ]
        ],
        shape=MSO_SHAPE.RECTANGLE,
        margin=0,
    )
    box(
        s,
        0.4,
        1.82,
        2.05,
        0.68,
        [
            [t("Safety domain", size=11.5, bold=True, color=WHITE)],
            [t("QNX · AUTOSAR · ASIL — owns every warning", size=10, color=WHITE)],
        ],
        fill=SLATE,
        name="Safety domain",
    )
    box(
        s,
        2.57,
        1.82,
        1.8,
        0.68,
        [
            [t("Lab simulation", size=11.5, bold=True, color=NAVY)],
            [t("real trips replayed, faults injected", size=10, color=INK)],
        ],
        fill=PALE2,
        line=BLUE,
        dash=True,
        name="Simulation",
    )
    box(
        s,
        4.49,
        1.82,
        1.96,
        0.68,
        [
            [t("Android Automotive", size=11.5, bold=True, color=NAVY)],
            [t("VHAL · screens · voice UI", size=10, color=INK)],
        ],
        fill=PALE,
        line=LINE,
        name="Android Automotive VM",
    )
    zone = box(s, 0.4, 2.98, 6.05, 2.78, [], fill=ZONE, line=NAVY, radius=0.04, name="AI zone")
    zone.line.width = Pt(1.5)
    arrow(s, 1.42, 2.5, 1.42, 3.3)
    box(
        s,
        1.5,
        2.56,
        1.5,
        0.24,
        [[t("one way, listen-only", size=10, color=MUTED)]],
        shape=MSO_SHAPE.RECTANGLE,
        margin=0,
    )
    arrow(s, 3.3, 2.5, 2.75, 3.3, color=BLUE, dash=True)
    arrow(s, 5.47, 2.5, 5.47, 3.3)
    box(s, 5.55, 2.56, 0.9, 0.24, [[t("question", size=10, color=MUTED)]], shape=MSO_SHAPE.RECTANGLE, margin=0)

    left, right, w = 0.53, 3.6, 2.72
    rows_y, h = (3.3, 4.06, 4.82), 0.62
    box(
        s,
        left,
        rows_y[0],
        w,
        h,
        [
            [t("Ingest & decode", size=11, bold=True, color=WHITE)],
            [t("CAN · CAN FD · SOME/IP · VHAL · QNX · UDS\u00a0(passive) · ADAS", size=10, color=WHITE)],
        ],
        fill=NAVY,
    )
    box(
        s,
        left,
        rows_y[1],
        w,
        h,
        [
            [t("Encrypted DuckDB", size=11, bold=True, color=WHITE)],
            [t(f"1 Hz · 1 min · events — {n['store_mb']:.1f} MB per car-year", size=10, color=WHITE)],
        ],
        fill=NAVY,
    )
    box(
        s,
        left,
        rows_y[2],
        w,
        h,
        [
            [t("Analytics → answer + evidence", size=11, bold=True, color=WHITE)],
            [t(f"trends · anomalies · factors · root cause {rc['root_cause_correct']}", size=10, color=WHITE)],
        ],
        fill=GREEN,
    )
    box(
        s,
        right,
        rows_y[0],
        w,
        h,
        [
            [t("Decline checks", size=11, bold=True, color=NAVY)],
            [t(f"asked to act? data not recorded? {n['refused']} declined", size=10, color=INK)],
        ],
        fill=WHITE,
        line=LINE,
        bg=WHITE,
    )
    box(
        s,
        right,
        rows_y[1],
        w,
        h,
        [
            [t(f"Rules · {fast['n'] / 192:.0%} of questions", size=11, bold=True, color=WHITE)],
            [t(f"deterministic · p50 {fast['p50']:.0f} ms on Graviton2", size=10, color=WHITE)],
        ],
        fill=BLUE,
    )
    box(
        s,
        right,
        rows_y[2],
        w,
        h,
        [
            [t("Qwen2.5-1.5B for the rest", size=11, bold=True, color=WHITE)],
            [t(f"grammar-locked read-only query · p95 {slm['p95'] / 1000:.2f} s", size=10, color=WHITE)],
        ],
        fill=BLUE,
    )
    cx_l, cx_r = left + w / 2, right + w / 2
    arrow(s, cx_l, rows_y[0] + h, cx_l, rows_y[1])
    arrow(s, cx_l, rows_y[1] + h, cx_l, rows_y[2])
    arrow(s, cx_r, rows_y[0] + h, cx_r, rows_y[1])
    arrow(s, cx_r, rows_y[1] + h, cx_r, rows_y[2])
    arrow(s, right, rows_y[1] + h / 2, left + w, rows_y[2] + 0.2)
    arrow(s, right, rows_y[2] + h / 2, left + w, rows_y[2] + h / 2)
    box(
        s,
        3.35,
        5.47,
        3.0,
        0.26,
        [[t("AI zone · Linux VM · QM · 2 cores · 2 GB", size=10.5, bold=True, color=NAVY)]],
        shape=MSO_SHAPE.RECTANGLE,
        align=PP_ALIGN.RIGHT,
        margin=0,
        bg=ZONE,
    )
    arrow(s, cx_l, rows_y[2] + h, cx_l, 5.98)
    box(
        s,
        cx_l + 0.08,
        5.74,
        1.6,
        0.24,
        [[t("answer + evidence", size=10, color=MUTED)]],
        shape=MSO_SHAPE.RECTANGLE,
        margin=0,
    )

    users = [
        ("Driver", "voice · short answer + evidence"),
        ("Service engineer", "cert login · timelines · export"),
        ("Fleet", "opt-in · anonymised totals"),
        ("Validation team", "search test-drive & HIL logs"),
    ]
    uw = (6.05 - 3 * 0.08) / 4
    for i, (who, what) in enumerate(users):
        box(
            s,
            0.4 + i * (uw + 0.08),
            5.98,
            uw,
            0.66,
            [[t(who, size=11, bold=True, color=NAVY)], [t(what, size=10, color=INK)]],
            fill=PALE,
            line=LINE,
            name=f"User: {who}",
        )

    # ---- right: measured results ----------------------------------------------------------------------------------
    rx, rw = 6.65, 6.28
    tiles = [
        (
            f"{slm['p95'] / 1000:.2f} s",
            f"model-path p95 on 2 Graviton2 cores · {edge['latency_ms']['all']['under_2s_pct']:.0f}% of answers < 2 s",
        ),
        ("2 GB", f"AWS t4g.small, OS included — no swap, {edge['oom_kills']} OOM"),
        (f"{n['heldout']:.1%}", f"question → query, 70 held-out ({edge['heldout_accuracy']:.1%} on ARM)"),
        (
            rc["root_cause_correct"],
            f"root causes found in random fault tests (old rule {rc['earliest_fault_rule_correct'].split('/')[0]})",
        ),
    ]
    tw = (rw - 3 * 0.1) / 4
    for i, (big, small) in enumerate(tiles):
        box(
            s,
            rx + i * (tw + 0.1),
            1.48,
            tw,
            1.12,
            [[t(big, size=26, bold=True, color=BLUE)], [t(small, size=10, color=INK)]],
            fill=WHITE,
            line=LINE,
            anchor=MSO_ANCHOR.TOP,
            margin=0.08,
            name=f"Measured: {big}",
        )
    box(
        s,
        rx,
        2.72,
        rw,
        0.33,
        [
            [
                t("Typical  ", size=11, bold=True, color=INK),
                t("an LLM writes the numbers → can invent figures · several GB · seconds", size=11, color=INK),
            ]
        ],
        fill="EDEFF2",
        shape=MSO_SHAPE.RECTANGLE,
        margin=0.1,
    )
    box(
        s,
        rx,
        3.07,
        rw,
        0.33,
        [
            [
                t("EdgeCar AI  ", size=11, bold=True, color=WHITE),
                t("the model writes a read-only query; code computes every number", size=11, color=WHITE),
            ]
        ],
        fill=NAVY,
        shape=MSO_SHAPE.RECTANGLE,
        margin=0.1,
    )

    rows = brief_rows(b)
    tbl_y = 3.54
    gf = s.shapes.add_table(len(rows) + 1, 3, Inches(rx), Inches(tbl_y), Inches(rw), Inches(6.64 - tbl_y))
    gf.name = "The brief's questions"
    table = gf.table
    table.horz_banding = False
    for i, width in enumerate((2.55, 0.95, rw - 2.55 - 0.95)):
        table.columns[i].width = Inches(width)
    head = [
        "The brief's five questions · a paraphrase · a request",
        "Route · Graviton2",
        "Answer — computed, not generated",
    ]
    for r_i, row in enumerate([head, *rows]):
        table.rows[r_i].height = Inches(0.27 if r_i == 0 else 0.38)
        for c_i, value in enumerate(row):
            cell = table.cell(r_i, c_i)
            fill = NAVY if r_i == 0 else (WHITE if r_i % 2 else PALE2)
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(fill)
            cell.margin_left = cell.margin_right = Inches(0.06)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            para = cell.text_frame.paragraphs[0]
            run = para.add_run()
            run.text = value
            run.font.name, run.font.size = FONT, Pt(10 if r_i else 10)
            color = WHITE if r_i == 0 else INK
            run.font.bold = r_i == 0 or c_i == 1
            run.font.italic = r_i >= 6 and c_i == 0
            run.font.color.rgb = RGBColor.from_string(color)
            CONTRAST.append((value, color, fill))

    # ---- footer ----------------------------------------------------------------------------------------------------
    box(s, 0.4, 6.78, 12.53, 0.62, [], fill=PALE2, radius=0.12, name="Footer")
    box(
        s,
        0.5,
        6.8,
        5.0,
        0.58,
        [
            [
                t("Security & privacy  ", size=10, bold=True, color=NAVY),
                t(
                    "keys in HSM/TEE · signed OTA for code and model · read-only by construction · no cloud · "
                    "erase-my-data (DPDP Act, GDPR)",
                    size=10,
                    color=INK,
                ),
            ]
        ],
        shape=MSO_SHAPE.RECTANGLE,
        margin=0.02,
        bg=PALE2,
    )
    box(
        s,
        5.65,
        6.8,
        4.3,
        0.58,
        [
            [
                t("Scales  ", size=10, bold=True, color=NAVY),
                t(
                    "2 cores, 2 GB → Pi 5 + Hailo NPU → vehicle HPC with 3–8B models · a new car is "
                    "configuration (DBC/ARXML + fault map), not retraining",
                    size=10,
                    color=INK,
                ),
            ]
        ],
        shape=MSO_SHAPE.RECTANGLE,
        margin=0.02,
        bg=PALE2,
    )
    pic = s.shapes.add_picture(io.BytesIO(qr_png), Inches(10.12), Inches(6.82), Inches(0.54), Inches(0.54))
    pic.name = "QR code: repository"
    pic.click_action.hyperlink.address = REPO
    box(
        s,
        10.74,
        6.8,
        2.17,
        0.58,
        [
            [t("github.com/meet-png/edgecar-ai", size=10.5, bold=True, color=BLUE, link=REPO)],
            [t("code · ARM CI · every benchmark", size=9.5, color=MUTED)],
        ],
        shape=MSO_SHAPE.RECTANGLE,
        margin=0.02,
        bg=PALE2,
    )


def build(template: Path, out: Path, results: Path) -> None:
    prs = Presentation(str(template))
    numbers = load(results)
    buf = io.BytesIO()
    segno.make(REPO, error="m").save(buf, kind="png", scale=12, border=1, dark="#0B3A60")
    title_slide(prs.slides[0])
    content_slide(prs.slides[1], numbers, buf.getvalue())

    low = [(text, fg, bg, round(contrast(fg, bg), 2)) for text, fg, bg in CONTRAST if contrast(fg, bg) < 4.5]
    if low:
        raise SystemExit(f"contrast below 4.5:1: {low}")
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    worst = min(CONTRAST, key=lambda c: contrast(c[1], c[2]))
    print(
        f"wrote {out} — {len(CONTRAST)} text runs, "
        f"lowest contrast {contrast(worst[1], worst[2]):.1f}:1 ({worst[0][:30]!r})"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("build/EdgeCar_TELIPORT_R1.pptx"))
    ap.add_argument("--results", type=Path, default=Path("eval/results"))
    a = ap.parse_args()
    build(a.template, a.out, a.results)


if __name__ == "__main__":
    main()
