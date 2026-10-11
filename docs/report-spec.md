# Report spec, version `gateway-insights-report/1`

What a business tool answered, independent of how it looks. A business tool returns it as MCP structured
content; the built-in renderer draws it as HTML, and any other agent may draw it in its own style. The spec
is a public contract: a change that breaks a reader bumps the version.

```json
{
  "spec": "gateway-insights-report/1",
  "tool": "ceo_earnings",
  "title": "Earnings by merchant",
  "period": {"current": "2026-10-01 .. 2026-10-07", "base": "2026-09-24 .. 2026-09-30"},
  "currency": "EUR",
  "summary": ["Manager margin 12,400.00 EUR, +8.1% to base; three merchants give 61%."],
  "kpis": [
    {"label": "Manager margin", "unit": "money", "value": "12400.00", "base": "11470.00",
     "delta": "930.00", "direction": 1, "note": ""}
  ],
  "sections": [
    {"title": "Top merchants",
     "chart": {"type": "bar", "categories": ["101 · Shop A", "102 · Shop B"],
               "series": [{"label": "now", "values": ["5200.00", "2300.00"]}]},
     "table": {"columns": [{"key": "name", "label": "Merchant", "unit": "text"},
                           {"key": "margin", "label": "Margin", "unit": "money"}],
               "rows": [{"name": "101 · Shop A", "margin": "5200.00"},
                        {"name": "102 · Shop B", "margin": {"no_access": true}}]}}
  ],
  "caveats": ["Earnings are summed over all statuses."],
  "provenance": [{"tool": "exec_top", "path": "/exec-dashboard/top",
                  "params": "metric=MANAGER_MARGIN entity=MERCHANT top_n=10"}]
}
```

## Values

| form | meaning |
|---|---|
| decimal string `"1234.50"` | a number, read by the unit of its column or KPI |
| plain string | text (unit `text`) |
| `null` | no data |
| `{"no_access": true}` | the console hid it from the token owner — render «no access», never zero |

## Units

| unit | value | rendered as |
|---|---|---|
| `money` | amount in `currency` | `1,234.50 EUR` |
| `count` | integer | `1,234` |
| `rate` | fraction | `97.25%`; its delta as percentage points |
| `pp` | difference of fractions | `+1.23 pp` |
| `number` | any decimal | `1,234.50` |
| `text` | string | as is, escaped |

## Fields

- `direction`: `1` good, `-1` bad, `0` flat, `null` unknown — as the console decided it, polarity included
  (more declines is bad).
- `chart.type`: `bar` (one bar per category per series) or `line` (series over ordered categories; a
  series may carry `"style": "dashed"`). Every series has one value per category.
- `provenance`: the raw calls that produced the numbers, so anyone can reproduce them.

Renderers lay out and escape; they never compute a value.
