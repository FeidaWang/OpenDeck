#!/usr/bin/env python3
"""Deterministic PPTD layout checks. A source check is not visual approval."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata
from typing import Any


class LayoutError(ValueError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_path(root: Path, value: str, *, required=True) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or ".." in Path(value).parts:
        raise LayoutError(f"Expected a contained project-relative path: {value!r}")
    p = (root / value).resolve()
    if not p.is_relative_to(root.resolve()) or p == root.resolve():
        raise LayoutError(f"Resource escapes project: {value}")
    if required and not p.is_file():
        raise LayoutError(f"Resource does not exist: {value}")
    return p


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _family(value: str) -> str:
    return re.sub(r"[\s_-]", "", value).lower()


class FontResolver:
    """Resolve a real face, never silently substitute a requested family."""
    def __init__(self, root: Path, policy: dict | None = None):
        self.root = root.resolve()
        self.policy = policy or {}
        if not isinstance(self.policy.get("fonts", {}), dict):
            raise LayoutError("policy.fonts must be a mapping of exact families to face files")
        self.used = {}
        self.cache = {}

    def resolve(self, family: str, bold=False, italic=False) -> Path:
        if not isinstance(family, str) or not family.strip() or family.startswith("-") or len(family) > 200:
            raise LayoutError("A nonempty specific font family is required")
        family = family.strip().strip("\"'")
        if "," in family:
            raise LayoutError(f"Font fallback lists cannot establish an exact face: {family}")
        face = "bold_italic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
        key = (family, face)
        if key in self.cache:
            return self.cache[key]
        configured = self.policy.get("fonts", {}).get(family, {})
        path = configured.get(face) if isinstance(configured, dict) else None
        if path:
            if not isinstance(path, str):
                raise LayoutError("Configured font faces must be file paths")
            path = Path(path).expanduser()
            path = path if path.is_absolute() else local_path(self.root, path.as_posix())
            path = path.resolve()
            if not path.is_file():
                raise LayoutError(f"Configured font face is missing: {family} / {face}")
        else:
            path = self._discover(family, face)
        if not path:
            raise LayoutError(f"Exact font unavailable: {family} / {face}; provide the actual font file or choose an installed family")
        try:
            from PIL import ImageFont
            probe = ImageFont.truetype(str(path), 20)
            actual_family, actual_style = probe.getname()
        except (ImportError, OSError) as exc:
            raise LayoutError(f"Cannot measure font {family} / {face}: {exc}") from exc
        if _family(actual_family) != _family(family):
            raise LayoutError(f"Font substitution rejected: requested {family}, file reports {actual_family}")
        actual_bold = any(w in actual_style.lower() for w in ("bold", "demi", "black", "heavy"))
        actual_italic = any(w in actual_style.lower() for w in ("italic", "oblique"))
        if bool(bold) != actual_bold or bool(italic) != actual_italic:
            raise LayoutError(f"Font style substitution rejected: {family} / {face} resolved to {actual_style}")
        self.cache[key] = path
        self.used[key] = {"family": family, "face": face, "path": str(path),
                          "sha256": sha256(path), "verified": True,
                          "verification": "local_family_style_and_font_bytes"}
        return path

    def _discover(self, family: str, face: str) -> Path | None:
        known = {
            "DejaVu Sans": {
                "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
                "bold_italic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"},
            "Arial": {"regular": "/System/Library/Fonts/Supplemental/Arial.ttf",
                      "bold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                      "italic": "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
                      "bold_italic": "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"},
        }
        if family in known and face in known[family] and Path(known[family][face]).is_file():
            return Path(known[family][face])
        if shutil.which("fc-match"):
            style = {"regular": "Regular", "bold": "Bold", "italic": "Italic", "bold_italic": "Bold Italic"}[face]
            try:
                result = subprocess.run(["fc-match", "-f", "%{family}\n%{file}\n", f"{family}:style={style}"],
                                        capture_output=True, text=True, timeout=5, check=False)
                lines = result.stdout.strip().splitlines()
                if result.returncode == 0 and len(lines) == 2 and _family(family) in [_family(f) for f in lines[0].split(",")]:
                    candidate = Path(lines[1])
                    if candidate.is_file():
                        return candidate
            except (OSError, subprocess.TimeoutExpired):
                pass
        win = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        if family == "Arial":
            filename = {"regular": "arial.ttf", "bold": "arialbd.ttf", "italic": "ariali.ttf", "bold_italic": "arialbi.ttf"}[face]
            if (win / filename).is_file():
                return win / filename
        if family == "Microsoft YaHei" and face in ("regular", "bold"):
            path = win / ("msyhbd.ttc" if face == "bold" else "msyh.ttc")
            if path.is_file():
                return path
        return None

    def font(self, family: str, size: float, bold=False, italic=False):
        if not _finite(size) or not 1 <= size <= 300:
            raise LayoutError("Font size must be finite and between 1 and 300")
        from PIL import ImageFont
        path = self.resolve(family, bold, italic)
        # Higher-resolution metrics avoid integer-size rounding for fractional px.
        return ImageFont.truetype(str(path), round(size * 4)), 4


def glyphs(text: str) -> list[str]:
    """Keep combining marks and common emoji joins attached during wrapping."""
    result = []
    for ch in text:
        continuation = unicodedata.combining(ch) or ch in ("\ufe0f", "\ufe0e", "\u200d") or 0x1F3FB <= ord(ch) <= 0x1F3FF
        if result and (continuation or result[-1].endswith("\u200d")):
            result[-1] += ch
        else:
            result.append(ch)
    return result


def text_width(text: str, font, scale: int = 4) -> float:
    if not text:
        return 0.0
    box = font.getbbox(text)
    return max(float(font.getlength(text)), float(box[2] - min(box[0], 0))) / scale


def missing_glyphs(text: str, font) -> set[str]:
    # FreeType's .notdef mask is stable within a face. Never replace missing glyphs.
    sentinel = font.getmask(chr(0x10FFFF))
    signature = (sentinel.size, bytes(sentinel))
    missing = set()
    for ch in set(text):
        if ch.isspace() or unicodedata.combining(ch) or ch in ("\ufe0f", "\ufe0e", "\u200d"):
            continue
        mask = font.getmask(ch)
        if (mask.size, bytes(mask)) == signature:
            missing.add(ch)
    return missing


def wrap_text(text: str, font, width: float, scale: int = 4) -> list[str]:
    if not isinstance(text, str) or not _finite(width) or width <= 0:
        raise LayoutError("Text wrapping requires text and a positive width")
    lines = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if paragraph == "":
            lines.append("")
            continue
        remaining = glyphs(paragraph)
        while remaining:
            count = 0
            while count < len(remaining) and text_width("".join(remaining[:count + 1]), font, scale) <= width:
                count += 1
            if count == 0:
                raise LayoutError("A single glyph is wider than its text box")
            if count == len(remaining):
                lines.append("".join(remaining).rstrip())
                break
            # Prefer a word break; CJK and long unbroken words can break by glyph.
            spaces = [i for i in range(1, count + 1) if remaining[i - 1].isspace()]
            cut = spaces[-1] if spaces else count
            # Avoid starting a CJK line with closing punctuation where feasible.
            if cut == count and cut > 1 and remaining[cut] in "，。！？、；：）》】」』,.!?;:":
                cut -= 1
            lines.append("".join(remaining[:cut]).rstrip())
            remaining = remaining[cut:]
            while remaining and remaining[0].isspace():
                remaining.pop(0)
    return lines


class RichText(HTMLParser):
    """Conservative HTML run parser. Unsupported styling is explicitly reviewed."""
    def __init__(self, base: dict):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.stack = [("root", dict(base))]
        self.paragraphs: list[list[tuple[str, dict]]] = [[]]
        self.unsupported = set()

    def style(self, raw: str, inherited: dict) -> dict:
        result = dict(inherited)
        for declaration in raw.split(";"):
            if not declaration.strip():
                continue
            if ":" not in declaration:
                self.unsupported.add("malformed_css")
                continue
            name, value = [x.strip() for x in declaration.split(":", 1)]
            if name == "font-size":
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)px", value)
                if match:
                    result["fontSize"] = float(match.group(1))
                else:
                    self.unsupported.add(name)
            elif name == "font-family":
                result["fontFamily"] = value.strip("\"'")
            elif name == "font-weight":
                result["bold"] = value in ("bold", "600", "700", "800", "900")
            elif name == "font-style":
                result["italic"] = value in ("italic", "oblique")
            elif name == "color":
                result["color"] = value
            elif name == "background-color":
                result["backgroundColor"] = value
            elif name == "line-height":
                try:
                    result["lineHeightPx" if value.endswith("px") else "lineHeight"] = float(value.removesuffix("px"))
                except ValueError:
                    self.unsupported.add(name)
            elif name in ("text-align", "text-decoration"):
                continue
            elif name in ("margin-top", "letter-spacing") and value in ("0", "0px"):
                continue
            else:
                self.unsupported.add(name)
        return result

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.paragraphs.append([])
            return
        if tag not in {"p", "span", "strong", "b", "em", "i", "u"}:
            self.unsupported.add(tag)
        if tag in ("p", "li", "div") and self.paragraphs[-1]:
            self.paragraphs.append([])
        attrs = dict(attrs)
        style = self.style(attrs.get("style", ""), self.stack[-1][1])
        if tag in ("strong", "b"):
            style["bold"] = True
        if tag in ("em", "i"):
            style["italic"] = True
        if any(k != "style" for k in attrs):
            self.unsupported.add("html_attributes")
        self.stack.append((tag, style))

    def handle_endtag(self, tag):
        if tag in ("p", "li", "div") and self.paragraphs[-1]:
            self.paragraphs.append([])
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, text):
        # HTML indentation whitespace between paragraphs is not content.
        if not text.strip() and len(self.stack) == 1:
            return
        pieces = text.split("\n")
        for i, piece in enumerate(pieces):
            if i:
                self.paragraphs.append([])
            if piece:
                self.paragraphs[-1].append((piece, dict(self.stack[-1][1])))

    def result(self):
        while len(self.paragraphs) > 1 and not self.paragraphs[-1]:
            self.paragraphs.pop()
        return self.paragraphs


def _colour(value, theme):
    if isinstance(value, str) and value.startswith("$"):
        value = theme.get("colors", {}).get(value[1:])
    if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return None


def contrast(first, second):
    def luminance(rgb):
        values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(values, (.2126, .7152, .0722)))
    a, b = sorted((luminance(first), luminance(second)))
    return (b + .05) / (a + .05)


def intersection(a, b):
    return max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))


def contains(a, b):
    return a[0] <= b[0] + .01 and a[1] <= b[1] + .01 and a[0] + a[2] >= b[0] + b[2] - .01 and a[1] + a[3] >= b[1] + b[3] - .01


def shape_covers(shape, bounds):
    """A bounding rectangle is not proof of a curved shape's painted coverage."""
    outer = shape["bounds"]
    if not contains(outer, bounds):
        return False
    kind = shape.get("shapeName")
    if kind == "rect":
        return True
    x, y, w, h = outer
    points = [(bounds[0]+dx*bounds[2], bounds[1]+dy*bounds[3]) for dx in (0, 1) for dy in (0, 1)]
    if kind == "ellipse" and w and h:
        return all(((px-x-w/2)/(w/2))**2 + ((py-y-h/2)/(h/2))**2 <= 1 for px, py in points)
    if kind == "roundRect" and w and h:
        # The core of even the maximally rounded rectangle is certainly painted.
        # Outside this conservative region, the renderer must establish coverage.
        radius = min(w, h)/2
        return all(max(x+radius-px, 0, px-(x+w-radius))**2 + max(y+radius-py, 0, py-(y+h-radius))**2 <= radius**2 for px, py in points)
    return False


def audit_deck(manifest: Path, policy: dict | Path | None = None) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise LayoutError("PyYAML is required for layout checks") from exc
    from export_pptx import find_manifest, read_yaml_mapping, image_sources, safe_project_path
    manifest = find_manifest(Path(manifest).resolve())
    root = manifest.parent
    _, deck = read_yaml_mapping(manifest)
    policy_path = None
    if policy is None and (root / "run/layout-policy.json").is_file():
        policy = root / "run/layout-policy.json"
    if isinstance(policy, (str, Path)):
        policy_path = Path(policy).resolve()
        policy = json.loads(policy_path.read_text())
    policy = policy or {}
    if not isinstance(policy, dict):
        raise LayoutError("Layout policy must be an object")
    if not isinstance(policy.get("element_roles", {}), dict) or not isinstance(policy.get("intentional_overlaps", []), list) or not isinstance(policy.get("edge_elements", []), list):
        raise LayoutError("Layout policy roles, intentional_overlaps or edge_elements have invalid types")
    resolver = FontResolver(root, policy)
    issues, metrics, evidence = [], [], {manifest.name: sha256(manifest)}
    if policy_path and policy_path.is_relative_to(root):
        evidence[policy_path.relative_to(root).as_posix()] = sha256(policy_path)
    sources = policy.get("source_files", [])
    if not isinstance(sources, list):
        raise LayoutError("policy.source_files must be an array of project-relative file paths")
    for source in sources:
        path = local_path(root, source)
        evidence[path.relative_to(root).as_posix()] = sha256(path)
    counts = defaultdict(int)

    def issue(severity, code, page, element, message):
        key = f"p{page:03d}:{element}:{code}"
        counts[key] += 1
        issues.append({"id": key + (f":{counts[key]}" if counts[key] > 1 else ""),
                       "severity": severity, "code": code, "page": page,
                       "element": element, "message": message})

    size = deck.get("size")
    if not isinstance(size, list) or len(size) != 2 or not all(_finite(v) and v > 0 for v in size):
        raise LayoutError("Canvas size must be two finite positive numbers")
    pages = deck.get("pages")
    if not isinstance(pages, list) or not pages or any(not isinstance(p, str) for p in pages) or len(set(pages)) != len(pages):
        raise LayoutError("Deck must list unique page paths")
    theme = deck.get("theme", {})
    if not isinstance(theme, dict) or not isinstance(theme.get("textStyles", {}), dict) or not isinstance(theme.get("colors", {}), dict):
        raise LayoutError("Theme and its textStyles/colors must be mappings")
    margin = policy.get("safe_margin", 24)
    if not _finite(margin) or margin < 0 or margin * 2 >= min(size):
        raise LayoutError("safe_margin is invalid")
    for page_no, rel in enumerate(pages, 1):
        file = local_path(root, rel)
        _, page = read_yaml_mapping(file)
        evidence[rel] = sha256(file)
        for ref in image_sources(page) + image_sources(deck):
            if ref.startswith(("http:", "https:", "data:")):
                issue("review", "remote_asset", page_no, "page", "Remote/inline asset bytes require inspection in the actual renderer")
            else:
                asset = safe_project_path(root, ref)
                if not asset.is_file():
                    issue("error", "missing_asset", page_no, "page", f"Referenced image missing: {ref}")
                else:
                    evidence[asset.relative_to(root).as_posix()] = sha256(asset)
        elements = page.get("elements", [])
        if not isinstance(elements, list):
            issue("error", "elements_type", page_no, "page", "elements must be an array")
            continue
        valid, seen = [], set()
        for index, element in enumerate(elements):
            if not isinstance(element, dict):
                issue("error", "element_type", page_no, str(index), "Element must be a mapping")
                continue
            eid = element.get("elementId")
            if not isinstance(eid, str) or not eid:
                issue("error", "missing_id", page_no, str(index), "Element needs a nonempty ID")
                continue
            if eid in seen:
                issue("error", "duplicate_id", page_no, eid, "Duplicate element ID")
            seen.add(eid)
            bounds = element.get("bounds")
            if not isinstance(bounds, list) or len(bounds) != 4 or not all(_finite(v) for v in bounds):
                issue("error", "invalid_bounds", page_no, eid, "Bounds must be four finite numbers")
                continue
            x, y, w, h = bounds
            kind = element.get("elementType")
            if w < 0 or h < 0 or (kind != "line" and (w == 0 or h == 0)):
                issue("error", "invalid_dimensions", page_no, eid, "Element dimensions must be positive")
                continue
            if x < -.01 or y < -.01 or x + w > size[0] + .01 or y + h > size[1] + .01:
                issue("error", "out_of_bounds", page_no, eid, "Element extends beyond the slide")
            if kind in ("text", "image") and (x < margin or y < margin or x + w > size[0] - margin or y + h > size[1] - margin):
                if f"{page_no}/{eid}" not in policy.get("edge_elements", []):
                    issue("error", "safe_margin", page_no, eid, f"Content crosses the {margin}px safe margin")
            if element.get("rotation", 0) or element.get("flip") or element.get("shadow") or element.get("opacity", 1) != 1:
                issue("review", "transform_effect", page_no, eid, "Transform/effect needs rendered verification")
            if kind not in ("text", "shape", "image", "line"):
                issue("review", "unsupported_element", page_no, eid, f"{kind!r} needs rendered inspection; not measured as plain editable objects")
            if kind == "shape" and element.get("shapeName") not in ("rect", "roundRect", "ellipse"):
                issue("review", "complex_shape", page_no, eid, "Complex shape geometry requires render review")
            if kind == "shape" and ("text" in element or isinstance(element.get("content"), dict) and "text" in element["content"]):
                issue("error", "shape_embedded_text", page_no, eid, "PPTD shapes cannot contain text; use a separate editable text element so content is not discarded")
            if kind == "line":
                issue("review", "line_geometry", page_no, eid, "Line strokes, endpoints and intersections need rendered verification")
            if kind == "image" and isinstance(element.get("fit", {}), dict) and element.get("fit", {}).get("mode") == "fill":
                issue("error", "image_stretch", page_no, eid, "Use contain/cover; fill can distort image geometry")
            valid.append((index, element))
            if kind != "text":
                continue
            content = element.get("content", {})
            if not isinstance(content, dict) or not isinstance(content.get("text"), str):
                issue("error", "invalid_text", page_no, eid, "Text content must contain a string")
                continue
            text = content["text"]
            if len(text) > 50000:
                issue("error", "excessive_text", page_no, eid, "Text exceeds the bounded 50,000-character measurement limit; split the content")
                continue
            if not text.strip():
                issue("error", "empty_text", page_no, eid, "Remove empty text boxes")
                continue
            base = {"fontFamily": "MiSans", "fontSize": 18, "lineHeight": 1, "color": "#000000", "bold": False, "italic": False}
            style_name = content.get("style")
            if style_name:
                style_key = style_name.removeprefix("$") if isinstance(style_name, str) else None
                style = theme.get("textStyles", {}).get(style_key, {}) if style_key else None
                if not isinstance(style, dict):
                    issue("error", "invalid_text_style", page_no, eid, "Named text style must resolve to a mapping")
                    continue
                if style_key not in theme.get("textStyles", {}):
                    issue("review", "unresolved_text_style", page_no, eid, "Named text style is not defined in the source theme")
                base.update(style)
            base.update({k: v for k, v in content.items() if k != "text"})
            parser = RichText(base)
            if "<" in text:
                parser.feed(text)
                paragraphs = parser.result()
            else:
                paragraphs = [[(line, dict(base))] for line in text.split("\n")]
            if parser.unsupported or "\\(" in text or "\\[" in text:
                issue("review", "rich_text_features", page_no, eid, "Unsupported rich styling/formulas need renderer verification: " + ", ".join(sorted(parser.unsupported)))
            if any(base.get(k) for k in ("letterSpacing", "marginTop", "padding", "indent")):
                issue("review", "text_spacing", page_no, eid, "Nondefault spacing/padding must be checked in the renderer")
            vertical = base.get("textDirection", "horizontal") != "horizontal"
            if vertical or base.get("gradient") or base.get("shadow"):
                issue("review", "advanced_text", page_no, eid, "Vertical text, gradients and text shadows need renderer verification")
            role = policy.get("element_roles", {}).get(f"{page_no}/{eid}", "body")
            minimum = 14 if role == "caption" else 18
            max_width, total_height, line_count = 0.0, 0.0, 0
            fg_styles = []
            try:
                for paragraph in paragraphs:
                    if not paragraph:
                        total_height += float(base["fontSize"]) * float(base.get("lineHeight", 1.2))
                        line_count += 1
                        continue
                    # Conservative mixed runs: reserve the widest face per glyph and tallest line.
                    if len({(s.get("fontFamily"), s.get("fontSize"), s.get("bold"), s.get("italic")) for _, s in paragraph}) > 1:
                        issue("review", "mixed_run_metrics", page_no, eid, "Mixed type runs have conservative metrics; inspect actual line breaks")
                    ptext = "".join(t for t, _ in paragraph)
                    variants = []
                    line_height = 0.0
                    for fragment, style in paragraph:
                        size_px = style.get("fontSize", 18)
                        if not _finite(size_px) or size_px < minimum:
                            issue("error", "small_text", page_no, eid, f"Text below the {minimum}px minimum for role {role}")
                        font, scale = resolver.font(style.get("fontFamily", "MiSans"), size_px, style.get("bold", False), style.get("italic", False))
                        absent = missing_glyphs(fragment, font)
                        if absent:
                            issue("error", "missing_glyph", page_no, eid, "Selected face lacks glyphs: " + " ".join(f"U+{ord(c):04X}" for c in sorted(absent)[:12]))
                        height = style.get("lineHeightPx", size_px * style.get("lineHeight", 1.2))
                        if not _finite(height) or height <= 0:
                            raise LayoutError("Line height must be finite and positive")
                        ascent, descent = font.getmetrics()
                        line_height = max(line_height, height, (ascent + descent) / scale)
                        variants.append((font, scale))
                        fg_styles.append((style, size_px))
                    measured = []
                    for font, scale in variants:
                        lines = [ptext] if base.get("wrap") is False else wrap_text(ptext, font, max(1, w - 8), scale)
                        measured.append((len(lines), max((text_width(t, font, scale) for t in lines), default=0)))
                    count = max(m[0] for m in measured)
                    max_width = max(max_width, max(m[1] for m in measured))
                    total_height += count * line_height
                    line_count += count
                if not vertical and (max_width > w - 4 + .01 or total_height > h - 4 + .01):
                    issue("error", "text_overflow", page_no, eid, f"Measured {max_width:.1f}×{total_height:.1f}px exceeds safe text area {w-4:.1f}×{h-4:.1f}px; split or rewrite content")
                metrics.append({"page": page_no, "element": eid, "lines": line_count,
                                "measured_width": round(max_width, 2), "measured_height": round(total_height, 2),
                                "box_width": w, "box_height": h})
            except (LayoutError, ImportError, OSError, TypeError, ValueError) as exc:
                issue("error", "font_or_text_measurement", page_no, eid, str(exc))
            background = page.get("background", {"type": "solid", "color": "#FFFFFF"})
            background = background if isinstance(background, dict) else {}
            bg = _colour(background.get("color"), theme)
            if background.get("type") != "solid":
                bg = None
            for prior in elements[:index]:
                if isinstance(prior, dict) and prior.get("elementType") == "shape" and isinstance(prior.get("bounds"), list) and len(prior["bounds"]) == 4 and all(_finite(v) for v in prior["bounds"]) and contains(prior["bounds"], bounds):
                    fill = prior.get("fill", {})
                    if fill:
                        bg = _colour(fill.get("color"), theme) if shape_covers(prior, bounds) and isinstance(fill, dict) and fill.get("type") == "solid" and prior.get("opacity", 1) == 1 else None
                elif isinstance(prior, dict) and prior.get("elementType") == "image" and isinstance(prior.get("bounds"), list) and len(prior["bounds"]) == 4 and all(_finite(v) for v in prior["bounds"]) and intersection(prior["bounds"], bounds) > 1:
                    bg = None
            for style, font_size in fg_styles:
                fg = _colour(style.get("color"), theme)
                text_bg = _colour(style["backgroundColor"], theme) if "backgroundColor" in style else bg
                if fg is None or text_bg is None:
                    issue("review", "contrast_unknown", page_no, eid, "Text contrast depends on unresolved colour/background; inspect rendered pixels")
                    break
                threshold = 3.0 if font_size >= 24 or font_size >= 18 and style.get("bold") else 4.5
                ratio = contrast(fg, text_bg)
                if ratio < threshold:
                    issue("error", "low_contrast", page_no, eid, f"Contrast {ratio:.2f}:1 is below {threshold}:1")
                    break
        for pos, (index, a) in enumerate(valid):
            for next_index, b in valid[pos + 1:]:
                if intersection(a["bounds"], b["bounds"]) <= 1:
                    continue
                ak, bk = a.get("elementType"), b.get("elementType")
                if ak == bk == "shape" or "line" in (ak, bk):
                    continue
                pair = f"{a['elementId']}+{b['elementId']}"
                if ak == "shape" and bk in ("text", "image") and contains(a["bounds"], b["bounds"]):
                    if not shape_covers(a, b["bounds"]):
                        issue("review", "shape_content_boundary", page_no, pair, "Bounding-box containment does not establish painted coverage at curved/complex shape edges")
                    continue  # Earlier containers are intentional; curved boundaries still need review.
                if ak == "text" and bk == "shape" and not b.get("fill"):
                    continue  # Border-only outline does not cover the text.
                allowed = next((r for r in policy.get("intentional_overlaps", []) if isinstance(r, dict) and r.get("page") == page_no and set(r.get("elements", [])) == {a["elementId"], b["elementId"]} and r.get("reason")), None)
                if allowed:
                    issue("review", "intentional_overlap", page_no, pair, "Declared overlap still needs visual inspection: " + allowed["reason"])
                elif ak == "text" or bk == "text":
                    issue("error", "content_overlap", page_no, pair, "Text intersects another foreground object")
                elif ak == bk == "image":
                    issue("review", "image_overlap", page_no, pair, "Overlapping images require visual confirmation")
        if page.get("animations"):
            issue("review", "animation", page_no, "page", "Staged animation and final playback require application-level review")
    status = "failed" if any(i["severity"] == "error" for i in issues) else "needs_review" if issues else "passed"
    return {"schema_version": 1, "status": status, "kind": "deterministic_source_layout", "slide_count": len(pages),
            "policy_sha256": hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
            "files": [{"path": p, "sha256": h} for p, h in sorted(evidence.items())],
            "fonts": [resolver.used[k] for k in sorted(resolver.used)], "issues": issues, "metrics": metrics,
            "visual_review": "not_performed", "target_application_review": "not_performed",
            "limitations": ["Local font metrics and conservative source geometry do not certify another renderer",
                            "Unsupported rich content and declared overlap require page-specific visual resolutions"]}


check_layout = audit_deck


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        argv = argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_deck(args.manifest, args.policy)
        raw = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(raw, encoding="utf-8")
        else:
            print(raw, end="")
        return 0 if report["status"] == "passed" else 1
    except (LayoutError, RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
        print(f"layout_quality: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
