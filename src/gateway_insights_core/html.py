# SPDX-License-Identifier: Apache-2.0
"""HTML rendering of a report spec with a theme.

The page is self-contained and inert: no script, a Content-Security-Policy that
forbids every external load, fonts and logo embedded or absent. The renderer
lays out and escapes; it computes nothing a reader could disagree with — a
chart bar is the spec's value, scaled.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Any, Dict, List, Optional

from .numbers import to_decimal
from .report import HIDDEN, format_delta, format_value, validate
from .theme import Theme

CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
       "base-uri 'none'; form-action 'none'")


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _css(theme: Theme) -> str:
    c = theme.colors
    upper = "text-transform:uppercase;letter-spacing:.02em;" if theme.heading_uppercase else ""
    return (
        theme.font_faces_css
        + f":root{{--bg:{c['background']};--surface:{c['surface']};--border:{c['border']};"
        f"--text:{c['text']};--heading:{c['heading']};--muted:{c['muted']};--accent:{c['accent']};"
        f"--good:{c['good']};--bad:{c['bad']};}}"
        f"html,body{{margin:0;background:var(--bg);color:var(--text);font-family:{theme.fonts['body']};"
        "font-size:14px;line-height:1.5;}"
        ".page{max-width:1080px;margin:0 auto;padding:28px 20px 40px;}"
        "header{display:flex;align-items:center;gap:14px;margin-bottom:6px;}"
        "header img{height:28px;width:auto;}"
        f".product{{font-family:{theme.fonts['heading']};color:var(--muted);font-size:12px;{upper}}}"
        f"h1,h2{{font-family:{theme.fonts['heading']};color:var(--heading);{upper}}}"
        "h1{font-size:26px;margin:4px 0 2px;}"
        "h1::after{content:'';display:block;width:48px;height:4px;background:var(--accent);margin-top:10px;}"
        "h2{font-size:15px;margin:28px 0 10px;}"
        ".period{color:var(--muted);margin-bottom:18px;}"
        ".summary p{margin:4px 0;color:var(--heading);font-weight:600;}"
        ".kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin:18px 0;}"
        ".kpi{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;}"
        ".kpi .label{color:var(--muted);font-size:12px;}"
        f".kpi .value{{font-family:{theme.fonts['heading']};color:var(--heading);font-size:20px;margin:2px 0;"
        "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}"
        ".kpi .base{color:var(--muted);font-size:12px;}"
        ".good{color:var(--good);} .bad{color:var(--bad);} .flat{color:var(--muted);}"
        "table{width:100%;border-collapse:separate;border-spacing:0;background:var(--surface);"
        "border:1px solid var(--border);border-radius:10px;overflow:hidden;font-size:13px;}"
        "th,td{padding:7px 10px;border-bottom:1px solid var(--border);text-align:left;}"
        "th{color:var(--muted);font-weight:600;font-size:12px;}"
        "td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;}"
        "tr:last-child td{border-bottom:0;}"
        ".hidden{color:var(--muted);font-style:italic;}"
        ".chart{background:var(--surface);border:1px solid var(--border);border-radius:10px;"
        "padding:10px;margin-bottom:10px;}"
        ".chart svg{width:100%;height:auto;display:block;}"
        ".legend{color:var(--muted);font-size:12px;margin:4px 6px;}"
        ".legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 12px;}"
        "ul.caveats{color:var(--muted);} details{color:var(--muted);margin-top:24px;}"
        f"code{{font-family:{theme.fonts['mono']};font-size:12px;}}"
        + theme.extra_css
    )


def _num(value: Any) -> Optional[Decimal]:
    if value in (None, HIDDEN):
        return None
    try:
        return to_decimal(value)
    except ValueError:
        return None


def _label(value: Decimal, chart: Dict[str, Any], currency: str) -> str:
    unit = chart.get("unit") or "number"
    return format_value(format(value, "f"), unit, currency if unit == "money" else "")


def _bar_chart(chart: Dict[str, Any], theme: Theme, currency: str = "") -> str:
    cats = chart.get("categories") or []
    series = chart.get("series") or []
    if not cats or not series:
        return ""
    values = [[_num(v) for v in s.get("values") or []] for s in series]
    peak = max((abs(v) for row in values for v in row if v is not None), default=Decimal(0)) or Decimal(1)
    label_w, width, bar_h, gap = 220, 760, 14, 6
    group_h = bar_h * len(series) + gap * 2
    height = group_h * len(cats) + 10
    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">']
    plot = width - label_w - 130   # room for the value label after the longest bar
    for i, cat in enumerate(cats):
        y0 = 5 + i * group_h
        parts.append(f'<text x="{label_w - 8}" y="{y0 + group_h / 2 + 4}" text-anchor="end" font-size="12" '
                     f'fill="{theme.colors["text"]}">{_e(str(cat)[:40])}</text>')
        for j, row in enumerate(values):
            v = row[i] if i < len(row) else None
            y = y0 + gap + j * bar_h
            if v is None:
                continue
            w = float(abs(v) / peak) * plot
            color = theme.series[j % len(theme.series)]
            parts.append(f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h - 3}" rx="2" fill="{color}"/>')
            parts.append(f'<text x="{label_w + w + 6:.1f}" y="{y + bar_h - 5}" font-size="11" '
                         f'fill="{theme.colors["muted"]}">{_e(_label(v, chart, currency))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _line_chart(chart: Dict[str, Any], theme: Theme, currency: str = "") -> str:
    cats = chart.get("categories") or []
    series = chart.get("series") or []
    if len(cats) < 2 or not series:
        return ""
    values = [[_num(v) for v in s.get("values") or []] for s in series]
    flat = [v for row in values for v in row if v is not None]
    if not flat:
        return ""
    lo, hi = min(flat), max(flat)
    if hi == lo:
        hi = lo + 1
    width, height, left, right, top, bottom = 760, 240, 84, 28, 10, 26
    plot_w, plot_h = width - left - right, height - top - bottom

    def xy(i: int, v: Decimal) -> str:
        x = left + plot_w * i / (len(cats) - 1)
        y = top + plot_h * float((hi - v) / (hi - lo))
        return f"{x:.1f},{y:.1f}"

    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for k in range(4):   # horizontal grid with the value it stands for
        level = lo + (hi - lo) * Decimal(k) / Decimal(3)
        y = top + plot_h * float((hi - level) / (hi - lo))
        grid = "" if k == 0 else ' stroke-dasharray="2 4"'
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
                     f'stroke="{theme.colors["border"]}"{grid}/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
                     f'fill="{theme.colors["muted"]}">{_e(_label(level, chart, currency))}</text>')
    step = max(1, len(cats) // 8)
    for i in range(0, len(cats), step):
        x = left + plot_w * i / (len(cats) - 1)
        parts.append(f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" font-size="11" '
                     f'fill="{theme.colors["muted"]}">{_e(cats[i])}</text>')
    for j, row in enumerate(values):
        color = theme.series[j % len(theme.series)]
        dash = ' stroke-dasharray="5 4"' if (series[j].get("style") == "dashed") else ""
        run: List[str] = []
        for i, v in enumerate(row):
            if v is None:   # a gap stays a gap: no line is drawn through missing data
                if len(run) > 1:
                    parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2"{dash} '
                                 f'points="{" ".join(run)}"/>')
                run = []
                continue
            run.append(xy(i, v))
        if len(run) > 1:
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2"{dash} points="{" ".join(run)}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _legend(chart: Dict[str, Any], theme: Theme) -> str:
    items = [f'<i style="background:{theme.series[j % len(theme.series)]}"></i>{_e(s.get("label", ""))}'
             for j, s in enumerate(chart.get("series") or [])]
    return f'<div class="legend">{"".join(items)}</div>' if items else ""


def _value_cell(value: Any, unit: str, currency: str) -> str:
    text = format_value(value, unit, currency)
    cls = "num" if unit != "text" else ""
    if value == HIDDEN:
        cls += " hidden"
    return f'<td class="{cls.strip()}">{_e(text)}</td>'


def render_html(spec: Dict[str, Any], theme: Theme) -> str:
    validate(spec)
    currency = spec.get("currency") or ""
    out: List[str] = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(spec['title'])}</title><style>{_css(theme)}</style></head><body><div class=\"page\">",
        "<header>",
    ]
    if theme.logo_data_uri:
        out.append(f'<img alt="" src="{_e(theme.logo_data_uri)}">')
    if theme.product_name:
        out.append(f'<span class="product">{_e(theme.product_name)}</span>')
    out.append("</header>")
    out.append(f"<h1>{_e(spec['title'])}</h1>")
    period = spec.get("period") or {}
    if period:
        text = f"Period: {period.get('current', '')}" + (f" · base: {period['base']}" if period.get("base") else "")
        if currency:
            text += f" · {currency}"
        out.append(f'<div class="period">{_e(text)}</div>')
    if spec["summary"]:
        out.append('<div class="summary">' + "".join(f"<p>{_e(s)}</p>" for s in spec["summary"]) + "</div>")
    if spec["kpis"]:
        out.append('<div class="kpis">')
        for k in spec["kpis"]:
            cls = {1: "good", -1: "bad", 0: "flat"}.get(k.get("direction"), "flat")
            value_cls = "value hidden" if k["value"] == HIDDEN else "value"
            out.append(
                f'<div class="kpi"><div class="label">{_e(k["label"])}</div>'
                f'<div class="{value_cls}">{_e(format_value(k["value"], k["unit"], currency))}</div>'
                + (f'<div class="base">base {_e(format_value(k["base"], k["unit"], currency))} · '
                   f'<span class="{cls}">{_e(format_delta(k["delta"], k["unit"], currency))}</span></div>'
                   if k.get("base") is not None or k.get("delta") is not None else "")
                + (f'<div class="base">{_e(k["note"])}</div>' if k.get("note") else "")
                + "</div>")
        out.append("</div>")
    for section in spec["sections"]:
        out.append(f"<h2>{_e(section.get('title', ''))}</h2>")
        if section.get("text"):
            out.append(f"<p>{_e(section['text'])}</p>")
        chart = section.get("chart")
        if chart:
            svg = (_bar_chart(chart, theme, currency) if chart["type"] == "bar"
                   else _line_chart(chart, theme, currency))
            if svg:
                out.append(f'<div class="chart">{svg}{_legend(chart, theme)}</div>')
        tbl = section.get("table")
        if tbl:
            cols = tbl["columns"]
            head = "".join(f'<th class="{"" if c["unit"] == "text" else "num"}">{_e(c["label"])}</th>' for c in cols)
            body = "".join("<tr>" + "".join(_value_cell(r.get(c["key"]), c["unit"], currency) for c in cols) + "</tr>"
                           for r in tbl["rows"])
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
    if spec["caveats"]:
        out.append("<h2>Caveats</h2><ul class=\"caveats\">" + "".join(f"<li>{_e(c)}</li>" for c in spec["caveats"])
                   + "</ul>")
    if spec["provenance"]:
        out.append("<details><summary>How obtained</summary><ul>" + "".join(
            f"<li><code>{_e(p['tool'])}</code> → <code>POST {_e(p['path'])}</code> {_e(p.get('params', ''))}</li>"
            for p in spec["provenance"]) + "</ul></details>")
    out.append("</div></body></html>")
    return "".join(out)
