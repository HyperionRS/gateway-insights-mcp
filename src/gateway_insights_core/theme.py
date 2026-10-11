# SPDX-License-Identifier: Apache-2.0
"""Theme packs: how a report looks, kept outside the code.

A theme pack is a directory:

    theme.json      colours, font stacks, product name (docs/themes.md)
    logo.svg|png    optional, embedded as an image
    extra.css       optional; refused if it imports, fetches or scripts anything
    fonts/*.woff2   optional, listed in theme.json "font_files", embedded

The path comes from the profile configuration only, never from a tool argument.
The repository ships the neutral theme; a brand is always an external pack.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .errors import ThemeError

THEME_SCHEMA = "gateway-insights-theme/1"
NEUTRAL_DIR = Path(__file__).resolve().parent / "themes" / "neutral"

COLOR_KEYS = ("background", "surface", "border", "text", "heading", "muted", "accent", "good", "bad")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FONT_STACK = re.compile(r'^[A-Za-z0-9 ,"\'-]{1,200}$')
_PRODUCT = re.compile(r"^[^<>&\"']{0,60}$")
_WEIGHT = re.compile(r"^[1-9]00( [1-9]00)?$")
_RANGE = re.compile(r"^U\+[0-9A-Fa-f?]{1,6}(-[0-9A-Fa-f]{1,6})?(, ?U\+[0-9A-Fa-f?]{1,6}(-[0-9A-Fa-f]{1,6})?)*$")
_MAX_LOGO = 200 * 1024
_MAX_FONT = 1024 * 1024
_MAX_FONT_FILES = 16
_MAX_CSS = 32 * 1024
_LOGO_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}


@dataclass(frozen=True)
class Theme:
    name: str
    product_name: str
    colors: Dict[str, str]
    series: List[str]
    fonts: Dict[str, str]
    heading_uppercase: bool = False
    logo_data_uri: Optional[str] = None
    font_faces_css: str = ""
    extra_css: str = ""


def _inside(root: Path, rel: str, what: str) -> Path:
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ThemeError(f"{what} {rel!r} points outside the theme directory")
    if not path.is_file():
        raise ThemeError(f"{what} {rel!r} not found in the theme directory")
    return path


def _check_css(css: str) -> str:
    if len(css) > _MAX_CSS:
        raise ThemeError("extra.css is larger than 32 KiB")
    lowered = css.lower()
    for bad in ("@import", "javascript:", "expression(", "</style", "behavior:", "-moz-binding"):
        if bad in lowered:
            raise ThemeError(f"extra.css contains {bad!r}: a theme may not load or run anything")
    for match in re.finditer(r"url\(\s*(['\"]?)([^)'\"]*)", css, re.IGNORECASE):
        if not match.group(2).strip().lower().startswith("data:"):
            raise ThemeError("extra.css references an external url(); only data: URLs are allowed")
    return css


def load_theme(path: Optional[str]) -> Theme:
    root = Path(path).expanduser().resolve() if path else NEUTRAL_DIR
    spec_file = root / "theme.json"
    if not spec_file.is_file():
        raise ThemeError(f"no theme.json in {root}")
    try:
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ThemeError(f"{spec_file}: not valid JSON ({exc})") from None
    if spec.get("schema") != THEME_SCHEMA:
        raise ThemeError(f"{spec_file}: schema must be {THEME_SCHEMA!r}")

    colors_in = spec.get("colors") or {}
    colors: Dict[str, str] = {}
    for key in COLOR_KEYS:
        value = str(colors_in.get(key, ""))
        if not _HEX.match(value):
            raise ThemeError(f"colors.{key} must be a hex colour, got {value!r}")
        colors[key] = value
    series = [str(c) for c in (colors_in.get("series") or [colors["accent"], colors["muted"]])]
    for value in series:
        if not _HEX.match(value):
            raise ThemeError(f"colors.series entry must be a hex colour, got {value!r}")

    fonts_in = spec.get("fonts") or {}
    fonts: Dict[str, str] = {}
    for key, default in (("heading", "system-ui, sans-serif"), ("body", "system-ui, sans-serif"),
                         ("mono", "ui-monospace, monospace")):
        value = str(fonts_in.get(key) or default)
        if not _FONT_STACK.match(value):
            raise ThemeError(f"fonts.{key} contains characters a font stack does not need")
        fonts[key] = value

    product = str(spec.get("product_name") or "")
    if not _PRODUCT.match(product):
        raise ThemeError("product_name must be plain text up to 60 characters")

    logo_uri = None
    if spec.get("logo"):
        logo = _inside(root, str(spec["logo"]), "logo")
        mime = _LOGO_TYPES.get(logo.suffix.lower())
        if not mime:
            raise ThemeError("logo must be .svg or .png")
        blob = logo.read_bytes()
        if len(blob) > _MAX_LOGO:
            raise ThemeError("logo is larger than 200 KiB")
        # Embedded as an <img> source: an SVG shown this way cannot run script.
        logo_uri = f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"

    faces: List[str] = []
    font_items = spec.get("font_files") or []
    if len(font_items) > _MAX_FONT_FILES:
        raise ThemeError(f"font_files: at most {_MAX_FONT_FILES} files")
    for item in font_items:
        family = str(item.get("family", ""))
        if not re.match(r"^[A-Za-z0-9 -]{1,60}$", family):
            raise ThemeError(f"font_files: bad family {family!r}")
        font = _inside(root, str(item.get("file", "")), "font file")
        if font.suffix.lower() != ".woff2":
            raise ThemeError("font_files: only .woff2 is embedded")
        blob = font.read_bytes()
        if len(blob) > _MAX_FONT:
            raise ThemeError(f"font file {font.name} is larger than 1 MiB")
        weight = str(item.get("weight", "400")).strip()
        if not _WEIGHT.match(weight):
            raise ThemeError(f"font_files: weight {weight!r} must be like 400 or a range like 500 600")
        style = "italic" if item.get("style") == "italic" else "normal"
        rng = str(item.get("unicode_range") or "").strip()
        if rng and not _RANGE.match(rng):
            raise ThemeError(f"font_files: bad unicode_range {rng!r}")
        faces.append(f'@font-face{{font-family:"{family}";font-weight:{weight};font-style:{style};'
                     + (f"unicode-range:{rng};" if rng else "")
                     + f'src:url(data:font/woff2;base64,{base64.b64encode(blob).decode("ascii")}) format("woff2");}}')

    extra = ""
    if (root / "extra.css").is_file():
        extra = _check_css((root / "extra.css").read_text(encoding="utf-8"))

    return Theme(name=root.name, product_name=product, colors=colors, series=series, fonts=fonts,
                 heading_uppercase=bool(spec.get("heading_uppercase")), logo_data_uri=logo_uri,
                 font_faces_css="".join(faces), extra_css=extra)
