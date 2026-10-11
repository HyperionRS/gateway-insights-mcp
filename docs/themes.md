# Themes

A report's look is a theme pack: a directory chosen in the profile (`"theme": "/path"`) or by `GI_THEME`.
The repository ships one neutral theme. Your brand is your own pack, kept wherever you keep brand assets;
nothing in the code needs to change.

A tool argument can never choose a theme or a path.

```
my-theme/
  theme.json        required
  logo.svg          optional (.svg or .png, ≤ 200 KiB), embedded as an image
  extra.css         optional (≤ 32 KiB)
  fonts/*.woff2     optional (≤ 1 MiB each), listed in theme.json
```

## theme.json

```json
{
  "schema": "gateway-insights-theme/1",
  "product_name": "Acme Payments",
  "colors": {
    "background": "#10141b", "surface": "#161b23", "border": "#2e3336",
    "text": "#ffffffcc", "heading": "#ffffff", "muted": "#9d9ea1",
    "accent": "#ff6a00", "good": "#2fbf71", "bad": "#ff5a52",
    "series": ["#ff6a00", "#9d9ea1", "#ffffff"]
  },
  "fonts": {
    "heading": "\"Acme Display\", Arial, sans-serif",
    "body": "\"Acme Text\", Arial, sans-serif",
    "mono": "Menlo, monospace"
  },
  "heading_uppercase": true,
  "logo": "logo.svg",
  "font_files": [
    {"family": "Acme Display", "weight": "600", "file": "fonts/acme-display-600-latin.woff2",
     "unicode_range": "U+0000-00FF, U+2000-206F"},
    {"family": "Acme Display", "weight": "500 700", "file": "fonts/acme-display-variable-cyrillic.woff2",
     "unicode_range": "U+0400-045F, U+0490-0491"}
  ]
}
```

- Colours are hex (`#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`).
- Font stacks are family names; a family is used if it is embedded through `font_files` or installed on
  the reader's machine. Nothing is fetched from the network.
- `product_name` is shown in the report header; leave it empty for none.
- `font_files` (≤ 16): `weight` is one weight (`"400"`) or a range for a variable font (`"500 700"`);
  `unicode_range` is optional and lets one family be split into script subsets, as font services ship them.
  Remember the fonts' own licence when you embed them.

## What a theme cannot do

The rendered page carries a Content-Security-Policy that forbids every external load and contains no
script. A theme is refused if:

- `extra.css` contains `@import`, a `url()` that is not `data:`, `expression(`, `javascript:` or `</style`;
- a logo or font file lies outside the theme directory, or has another type or size than above;
- a colour, a font stack or the product name carries characters it does not need.

A logo is embedded as an image (`<img src="data:…">`), so an SVG cannot run script even if it contains one.
