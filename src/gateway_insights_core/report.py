# SPDX-License-Identifier: Apache-2.0
"""The report spec: what a business tool answered, independent of how it looks.

It is a public contract (docs/report-spec.md). A business tool returns it as
structured content; the markdown here and the HTML renderer only lay it out —
neither computes a number. A change that breaks a reader bumps SPEC_VERSION.

A cell or KPI value is one of:
  - a decimal string ("1234.50", "0.9725"), interpreted by the column's unit;
  - plain text (unit "text");
  - None: no data;
  - {"no_access": true}: the console hid it from the token owner.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from .errors import InsightsError
from .markdown import NO_ACCESS, UNTRUSTED_NOTE, md_escape, md_table
from .numbers import fmt_count, fmt_money, fmt_number, fmt_pp, fmt_rate, fmt_signed, to_decimal

SPEC_VERSION = "gateway-insights-report/1"
UNITS = ("money", "count", "rate", "pp", "number", "text")
HIDDEN = {"no_access": True}


class SpecError(InsightsError):
    """A business tool built a spec that breaks the contract: a bug, not a user error."""


def cell(value: Any) -> Any:
    """Normalise a value for the spec: Decimal -> string, hidden marker kept."""
    if value is None or value == HIDDEN:
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        raise SpecError("booleans are not report values")
    if isinstance(value, (int, float)):
        return format(to_decimal(value), "f")
    return str(value)


def format_value(value: Any, unit: str, currency: str = "") -> str:
    if value == HIDDEN:
        return NO_ACCESS
    if value is None:
        return "—"
    if unit == "text":
        return str(value)
    number = to_decimal(value)
    if unit == "money":
        return fmt_money(number, currency)
    if unit == "count":
        return fmt_count(number)
    if unit == "rate":
        return fmt_rate(number)
    if unit == "pp":
        return fmt_pp(number)
    return fmt_number(number)


def format_delta(value: Any, unit: str, currency: str = "") -> str:
    if value in (None, HIDDEN):
        return format_value(value, unit, currency)
    number = to_decimal(value)
    if unit == "rate":
        return fmt_pp(number)
    if unit == "money":
        return (fmt_signed(number) + f" {currency}").rstrip()
    if unit == "count":
        return fmt_signed(number, 0)
    return fmt_signed(number)


def new_spec(title: str, *, tool: str, period: Dict[str, str], currency: str = "") -> Dict[str, Any]:
    return {
        "spec": SPEC_VERSION, "tool": tool, "title": title, "period": period, "currency": currency,
        "summary": [], "kpis": [], "sections": [], "caveats": [], "provenance": [],
    }


def kpi(label: str, value: Any, unit: str, *, base: Any = None, delta: Any = None,
        direction: Optional[int] = None, note: str = "") -> Dict[str, Any]:
    """direction: +1 good, -1 bad, 0 flat, None unknown — as the console decided it."""
    return {"label": label, "unit": unit, "value": cell(value), "base": cell(base),
            "delta": cell(delta), "direction": direction, "note": note}


def table(columns: List[Dict[str, str]], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """columns: [{"key", "label", "unit"}]; rows: dicts keyed by column key."""
    return {"columns": columns, "rows": [{k: cell(v) for k, v in row.items()} for row in rows]}


def validate(spec: Dict[str, Any]) -> Dict[str, Any]:
    if spec.get("spec") != SPEC_VERSION:
        raise SpecError(f"unknown spec version {spec.get('spec')!r}")
    for key in ("title", "tool"):
        if not isinstance(spec.get(key), str) or not spec[key]:
            raise SpecError(f"spec.{key} must be a non-empty string")
    for key in ("summary", "kpis", "sections", "caveats", "provenance"):
        if not isinstance(spec.get(key), list):
            raise SpecError(f"spec.{key} must be a list")
    for item in spec["kpis"]:
        if item.get("unit") not in UNITS:
            raise SpecError(f"kpi {item.get('label')!r}: unknown unit {item.get('unit')!r}")
        if item.get("direction") not in (None, -1, 0, 1):
            raise SpecError(f"kpi {item.get('label')!r}: direction must be -1, 0, 1 or null")
    for section in spec["sections"]:
        tbl = section.get("table")
        if tbl:
            keys = set()
            for col in tbl.get("columns") or []:
                if col.get("unit") not in UNITS:
                    raise SpecError(f"column {col.get('key')!r}: unknown unit {col.get('unit')!r}")
                keys.add(col.get("key"))
            for row in tbl.get("rows") or []:
                extra = set(row) - keys
                if extra:
                    raise SpecError(f"row has keys without a column: {sorted(extra)}")
        chart = section.get("chart")
        if chart:
            if chart.get("type") not in ("bar", "line"):
                raise SpecError(f"chart type {chart.get('type')!r} is not bar or line")
            if chart.get("unit", "number") not in UNITS or chart.get("unit") == "text":
                raise SpecError(f"chart unit {chart.get('unit')!r} is not a numeric unit")
            n = len(chart.get("categories") or [])
            for series in chart.get("series") or []:
                if len(series.get("values") or []) != n:
                    raise SpecError(f"series {series.get('label')!r} length differs from categories")
    return spec


def to_markdown(spec: Dict[str, Any]) -> str:
    validate(spec)
    currency = spec.get("currency") or ""
    out: List[str] = [f"## {md_escape(spec['title'])}"]
    period = spec.get("period") or {}
    if period:
        line = f"Period: {md_escape(period.get('current', ''))}"
        if period.get("base"):
            line += f" · base: {md_escape(period['base'])}"
        out.append(line)
    if spec["summary"]:
        out.append("\n".join(f"**{md_escape(s)}**" for s in spec["summary"]))
    if spec["kpis"]:
        compared = any(k.get("base") is not None or k.get("delta") is not None for k in spec["kpis"])
        rows = []
        for k in spec["kpis"]:
            arrow = {1: "▲ good", -1: "▼ bad", 0: "flat"}.get(k.get("direction"), "")
            row = [k["label"], format_value(k["value"], k["unit"], currency)]
            if compared:
                row += [format_value(k["base"], k["unit"], currency), format_delta(k["delta"], k["unit"], currency),
                        arrow]
            rows.append(row)
        out.append(md_table(["Metric", "Now", "Base", "Change", ""] if compared else ["Metric", "Value"], rows))
    for section in spec["sections"]:
        out.append(f"### {md_escape(section.get('title', ''))}")
        if section.get("text"):
            out.append(md_escape(section["text"]))
        tbl = section.get("table")
        if tbl:
            cols = tbl["columns"]
            out.append(md_table([md_escape(c["label"]) for c in cols],
                                [[format_value(r.get(c["key"]), c["unit"], currency) for c in cols]
                                 for r in tbl["rows"]]))
    if spec["caveats"]:
        out.append("**Caveats**\n" + "\n".join(f"- {md_escape(c)}" for c in spec["caveats"]))
    if spec["provenance"]:
        lines = []
        for p in spec["provenance"]:
            params = str(p.get("params", "")).replace("`", "'")   # a code span cannot hold a backtick
            lines.append(f"- `{p['tool']}` → `POST {p['path']}` `{params}`")
        out.append("**How obtained**\n" + "\n".join(lines))
    out.append(UNTRUSTED_NOTE)
    return "\n\n".join(out)
