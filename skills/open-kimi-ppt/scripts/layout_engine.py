#!/usr/bin/env python3
"""Build editable PPTD from bounded, measured templates; never shrink to fit."""
from __future__ import annotations

import argparse
import html
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

from layout_quality import FontResolver, LayoutError, audit_deck, local_path, missing_glyphs, wrap_text


PALETTES = {
    "warm": {"background": "#F5F2EC", "card": "#FFFFFF", "text": "#21252A", "muted": "#50565C", "accent": "#95432F", "rule": "#DED8CF"},
    "neutral": {"background": "#F3F5F8", "card": "#FFFFFF", "text": "#172636", "muted": "#4A5968", "accent": "#205C82", "rule": "#D6DEE6"},
    "dark": {"background": "#17222D", "card": "#223342", "text": "#F5F7FA", "muted": "#CCD5DE", "accent": "#F3B49B", "rule": "#465565"},
}


def _keys(value, allowed, location):
    if not isinstance(value, dict):
        raise LayoutError(f"{location} must be an object")
    unknown = set(value) - set(allowed)
    if unknown:
        raise LayoutError(f"{location} has unsupported fields: {', '.join(sorted(unknown))}")


def _text(value, location, *, limit=6000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise LayoutError(f"{location} must be a nonempty string of at most {limit} characters")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise LayoutError(f"{location} contains control characters")
    return value.replace("\t", "    ").strip()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for part in value:
            yield from _strings(part)
    elif isinstance(value, dict):
        for part in value.values():
            yield from _strings(part)


def select_font(text="", requested=None, root=None):
    """Return (actual family, policy) for exact local faces covering supplied text."""
    configured = {} if requested is None else requested
    root = Path(root or Path.cwd()).resolve()
    _keys(configured, ("family", "regular", "bold"), "font")
    if configured and not configured.get("family"):
        raise LayoutError("font.family is required when font files are supplied")
    cjk = any(0x2E80 <= ord(c) <= 0x9FFF or 0xAC00 <= ord(c) <= 0xD7AF for c in text)
    candidates = [configured["family"]] if configured else (["Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "DejaVu Sans", "Arial"] if cjk else ["DejaVu Sans", "Arial", "Noto Sans"])
    failures = []
    for family in candidates:
        policy = {"fonts": {family: {face: configured[face] for face in ("regular", "bold") if face in configured}}}
        resolver = FontResolver(root, policy)
        try:
            for bold in (False, True):
                font, _ = resolver.font(family, 24, bold)
                absent = missing_glyphs(text, font)
                if absent:
                    raise LayoutError("missing " + " ".join(f"U+{ord(c):04X}" for c in sorted(absent)[:8]))
            faces = {face: str(resolver.resolve(family, face == "bold")) for face in ("regular", "bold")}
            return family, {"fonts": {family: faces}}
        except (LayoutError, OSError, ValueError) as exc:
            failures.append(f"{family}: {exc}")
    raise LayoutError("No exact usable regular/bold font pair covers this deck. Supply font.family, font.regular and font.bold with actual local files. " + "; ".join(failures))


class Builder:
    def __init__(self, spec, stage, asset_root):
        self.spec, self.stage, self.asset_root = spec, stage, asset_root
        self.palette = PALETTES[spec.get("style", "warm")]
        self.family, font_policy = select_font("\n".join(_strings(spec["slides"])), spec.get("font"), asset_root)
        self.policy = {"schema_version": 1, "safe_margin": 24, **font_policy,
                       "element_roles": {}, "source_files": ["run/layout-spec.json"],
                       "builder": "bounded-templates-v1", "font_selection": "explicit" if spec.get("font") else "available_family_with_glyph_coverage"}
        self.resolver = FontResolver(stage, self.policy)
        self.page_no = 0
        self.elements = []

    def measure(self, value, width, size, bold=False, *, location="text", max_lines=None):
        value = _text(value, location)
        font, scale = self.resolver.font(self.family, size, bold)
        absent = missing_glyphs(value, font)
        if absent:
            raise LayoutError(f"{location}: selected font lacks " + " ".join(f"U+{ord(c):04X}" for c in sorted(absent)[:12]))
        lines = wrap_text(value, font, width - 12, scale)
        if max_lines and len(lines) > max_lines:
            raise LayoutError(f"{location}: needs {len(lines)} lines; template allows {max_lines}. Shorten or move content to an explicitly authorized additional slide.")
        ascent, descent = font.getmetrics()
        line_height = max(size * 1.2, (ascent + descent) / scale)
        return lines, math.ceil(line_height * len(lines) + 10)

    def text(self, eid, value, x, y, width, height, size=24, bold=False, colour=None, max_lines=None, role=None):
        lines, needed = self.measure(value, width, size, bold, location=f"slide {self.page_no} / {eid}", max_lines=max_lines)
        if needed > height + .01:
            raise LayoutError(f"slide {self.page_no} / {eid}: needs {needed}px height at {size}px, but only {height:.1f}px is available. Split or rewrite the content; text was not shrunk or truncated.")
        content = {"fontFamily": self.family, "fontSize": size, "bold": bold,
                   "color": colour or self.palette["text"], "lineHeight": 1.2,
                   "align": ["left", "top"], "wrap": False,
                   "text": "".join("<p>" + (html.escape(line) if line else "&#160;") + "</p>" for line in lines)}
        self.elements.append({"elementId": eid, "elementType": "text",
                              "bounds": [round(x, 2), round(y, 2), round(width, 2), round(height, 2)], "content": content})
        if role:
            self.policy["element_roles"][f"{self.page_no}/{eid}"] = role
        return needed

    def shape(self, eid, x, y, width, height, colour, shape="rect"):
        self.elements.append({"elementId": eid, "elementType": "shape", "shapeName": shape,
                              "bounds": [round(x, 2), round(y, 2), round(width, 2), round(height, 2)],
                              "fill": {"type": "solid", "color": colour}})

    def header(self, slide):
        _, height = self.measure(slide["title"], 864, 36, True, location=f"slide {self.page_no} title", max_lines=2)
        self.text("title", slide["title"], 48, 40, 864, height, 36, True)
        return 40 + height + 28

    def footer(self, slide):
        if slide.get("source"):
            self.text("source", slide["source"], 48, 489, 784, 27, 14, colour=self.palette["muted"], max_lines=1, role="caption")
        self.text("page_number", str(self.page_no), 866, 489, 46, 27, 14, colour=self.palette["muted"], max_lines=1, role="caption")

    def bullet_list(self, bullets, x, y, width, bottom, *, size=24, gap=16, prefix="bullet"):
        if not isinstance(bullets, list) or not 1 <= len(bullets) <= 8:
            raise LayoutError(f"slide {self.page_no} / {prefix}: provide between 1 and 8 bullets")
        measured = [(value, self.measure(value, width - 30, size, location=f"slide {self.page_no} / {prefix} {i+1}")[1]) for i, value in enumerate(bullets)]
        needed = sum(h for _, h in measured) + gap * (len(measured) - 1)
        if y + needed > bottom:
            raise LayoutError(f"slide {self.page_no} / {prefix}: bullets need {needed:.1f}px, available {bottom-y:.1f}px. Split or rewrite; page count and readable font size are preserved.")
        for i, (value, height) in enumerate(measured, 1):
            self.shape(f"{prefix}_{i}_dot", x + 1, y + size * .48, 6, 6, self.palette["accent"], "ellipse")
            self.text(f"{prefix}_{i}", value, x + 30, y, width - 30, height, size)
            y += height + gap
        return y

    def cover(self, slide):
        self.shape("accent", 64, 120, 76, 6, self.palette["accent"])
        if slide.get("eyebrow"):
            self.text("eyebrow", slide["eyebrow"], 64, 66, 832, 38, 20, True, self.palette["accent"], max_lines=1)
        self.text("title", slide["title"], 64, 154, 832, 148, 48, True, max_lines=2)
        if slide.get("subtitle"):
            self.text("subtitle", slide["subtitle"], 64, 330, 780, 110, 24, colour=self.palette["muted"], max_lines=3)

    def bullets(self, slide):
        top = self.header(slide)
        self.bullet_list(slide["bullets"], 48, top, 864, 478)

    def two_column(self, slide):
        columns = slide["columns"]
        if not isinstance(columns, list) or len(columns) != 2:
            raise LayoutError(f"slide {self.page_no}: two_column requires exactly 2 columns")
        top = self.header(slide)
        for i, column in enumerate(columns, 1):
            _keys(column, ("heading", "bullets"), f"slide {self.page_no} column {i}")
            if not all(k in column for k in ("heading", "bullets")):
                raise LayoutError("Each column needs heading and bullets")
            x = 48 + (i - 1) * 444
            self.shape(f"column_{i}_card", x, top, 420, 480-top, self.palette["card"])
            _, heading_h = self.measure(column["heading"], 372, 24, True, max_lines=2)
            self.text(f"column_{i}_heading", column["heading"], x+24, top+22, 372, heading_h, 24, True)
            self.bullet_list(column["bullets"], x+24, top+22+heading_h+18, 372, 460, size=22, gap=12, prefix=f"column_{i}_bullet")

    def metrics(self, slide):
        values = slide["metrics"]
        if not isinstance(values, list) or len(values) not in (2, 3):
            raise LayoutError(f"slide {self.page_no}: metrics requires 2 or 3 metric cards")
        top = self.header(slide)
        width = (864 - 24*(len(values)-1)) / len(values)
        for i, metric in enumerate(values, 1):
            _keys(metric, ("value", "label", "detail"), f"slide {self.page_no} metric {i}")
            if not all(k in metric for k in ("value", "label")):
                raise LayoutError("Each metric needs value and label as supplied strings")
            if "detail" in metric:
                _text(metric["detail"], f"slide {self.page_no} metric {i} detail")
            x = 48+(i-1)*(width+24)
            self.shape(f"metric_{i}_card", x, top, width, 480-top, self.palette["card"])
            self.text(f"metric_{i}_value", metric["value"], x+24, top+28, width-48, 66, 44, True, self.palette["accent"], max_lines=1)
            _, label_h = self.measure(metric["label"], width-48, 22, True, max_lines=2)
            self.text(f"metric_{i}_label", metric["label"], x+24, top+116, width-48, label_h, 22, True)
            if metric.get("detail"):
                y = top+116+label_h+18
                self.text(f"metric_{i}_detail", metric["detail"], x+24, y, width-48, 458-y, 20, colour=self.palette["muted"], max_lines=4)

    def table(self, slide):
        headers, rows = slide["headers"], slide["rows"]
        if not isinstance(headers, list) or not 1 <= len(headers) <= 5:
            raise LayoutError(f"slide {self.page_no}: table requires 1–5 headers")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 8 or any(not isinstance(row, list) or len(row) != len(headers) for row in rows):
            raise LayoutError(f"slide {self.page_no}: table requires 1–8 complete rows matching header count")
        weights = slide.get("column_weights", [1]*len(headers))
        if not isinstance(weights, list) or len(weights) != len(headers) or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in weights):
            raise LayoutError("column_weights must be finite positive numbers matching headers")
        widths = [864*v/sum(weights) for v in weights]
        if min(widths) < 104:
            raise LayoutError("Table columns must be at least 104px wide; use fewer columns or rebalance column_weights")
        top = self.header(slide)
        measured = []
        for row_index, row in enumerate([headers]+rows):
            size, bold = (20, True) if row_index == 0 else (18, False)
            height = max(self.measure(cell, w-24, size, bold, location=f"slide {self.page_no} table row {row_index+1}")[1] for cell, w in zip(row, widths)) + 16
            measured.append((row, height, size, bold))
        if top+sum(item[1] for item in measured) > 480:
            raise LayoutError(f"slide {self.page_no}: table exceeds available height at readable font sizes. Split rows across authorized slides or shorten cells; no cells were discarded.")
        y = top
        for row_index, (row, height, size, bold) in enumerate(measured):
            x = 48
            for col_index, (cell, width) in enumerate(zip(row, widths)):
                prefix = f"cell_{row_index}_{col_index}"
                colour = self.palette["accent"] if row_index == 0 else self.palette["card"] if row_index % 2 else self.palette["background"]
                self.shape(prefix+"_background", x, y, width, height, colour)
                foreground = self.palette["background"] if row_index == 0 else self.palette["text"]
                self.text(prefix, cell, x+12, y+8, width-24, height-16, size, bold, foreground)
                x += width
            y += height

    def image(self, slide):
        from PIL import Image, UnidentifiedImageError
        src = local_path(self.asset_root, slide["image"])
        if src.stat().st_size > 20 * 1024 * 1024:
            raise LayoutError("Image exceeds the exporter's 20 MiB limit; supply an appropriately sized source asset")
        try:
            with Image.open(src) as asset:
                if asset.format not in ("PNG", "JPEG"):
                    raise LayoutError("Image template accepts PNG or JPEG source assets")
                iw, ih = asset.size
                ext = {"PNG": ".png", "JPEG": ".jpg"}[asset.format]
                asset.verify()
        except (OSError, UnidentifiedImageError) as exc:
            raise LayoutError(f"Image source cannot be decoded: {src.name}") from exc
        rel = f"media/slide-{self.page_no:03d}{ext}"
        (self.stage / "media").mkdir(exist_ok=True)
        shutil.copyfile(src, self.stage / rel)
        top = self.header(slide)
        box_x, box_w = 48, 864
        if slide.get("body"):
            self.text("body", slide["body"], 48, top, 318, 478-top, 22)
            box_x, box_w = 398, 514
        box_h = 480-top
        ratio = min(box_w/iw, box_h/ih)
        width, height = iw*ratio, ih*ratio
        self.elements.append({"elementId": "image", "elementType": "image", "src": rel,
                              "bounds": [round(box_x+(box_w-width)/2, 2), round(top+(box_h-height)/2, 2), round(width, 2), round(height, 2)],
                              "fit": {"mode": "contain"}})

    def build(self):
        import yaml
        allowed = {
            "cover": ("subtitle", "eyebrow"), "bullets": ("bullets",),
            "two_column": ("columns",), "metrics": ("metrics",),
            "table": ("headers", "rows", "column_weights"), "image": ("image", "body"),
        }
        required = {"cover": (), "bullets": ("bullets",), "two_column": ("columns",), "metrics": ("metrics",), "table": ("headers", "rows"), "image": ("image",)}
        page_paths = []
        (self.stage / "pages").mkdir()
        (self.stage / "run").mkdir()
        for self.page_no, slide in enumerate(self.spec["slides"], 1):
            if not isinstance(slide, dict) or slide.get("layout") not in allowed:
                raise LayoutError(f"slide {self.page_no}: layout must be one of {', '.join(allowed)}")
            kind = slide["layout"]
            _keys(slide, ("layout", "title", "source", *allowed[kind]), f"slide {self.page_no}")
            if any(key not in slide for key in ("title", *required[kind])):
                raise LayoutError(f"slide {self.page_no}: {kind} is missing required content")
            _text(slide["title"], f"slide {self.page_no} title", limit=500)
            for optional in ("source", "subtitle", "eyebrow", "body"):
                if optional in slide:
                    _text(slide[optional], f"slide {self.page_no} {optional}")
            self.elements = []
            getattr(self, kind)(slide)
            self.footer(slide)
            rel = f"pages/{self.page_no:02d}.page"
            page = {"pageType": "cover" if kind == "cover" else "content",
                    "background": {"type": "solid", "color": self.palette["background"]}, "elements": self.elements}
            (self.stage / rel).write_text(yaml.safe_dump(page, allow_unicode=True, sort_keys=False), encoding="utf-8")
            page_paths.append(rel)
        manifest = {"version": "v2", "title": self.spec["title"], "size": [960, 540], "pages": page_paths}
        (self.stage / "deck.pptd").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
        for name, document in (("layout-spec.json", self.spec), ("layout-policy.json", self.policy)):
            (self.stage / "run" / name).write_text(json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")


def _publish(stage: Path, output: Path):
    """No-overwrite publication, including into an initialized workflow project."""
    if output.is_symlink() or output.exists() and not output.is_dir():
        raise LayoutError("Output must be a new directory or an initialized project directory")
    if not output.exists():
        os.rename(stage, output)
        return
    if list(output.glob("*.pptd")) or (output / "pages").is_dir() and any(p.is_file() for p in (output / "pages").rglob("*")):
        raise LayoutError("Refusing to add a deck to a project with existing manifests or page sources; choose a new project")
    planned = sorted(p.relative_to(stage) for p in stage.rglob("*") if p.is_file())
    for relative in planned:
        target = output / relative
        if target.exists() or target.is_symlink():
            raise LayoutError(f"Refusing to overwrite existing output: {relative}")
        for parent in target.parents:
            if parent == output:
                break
            if parent.is_symlink() or parent.exists() and not parent.is_dir():
                raise LayoutError(f"Unsafe output directory: {parent}")
    created, directories = [], []
    try:
        for relative in planned:
            target = output / relative
            missing = []
            parent = target.parent
            while parent != output and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                directories.append(directory)
            with target.open("xb") as stream:
                created.append(target)
                stream.write((stage / relative).read_bytes())
    except BaseException:
        for file in reversed(created):
            file.unlink(missing_ok=True)
        for directory in reversed(directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def build_deck(spec: dict, output: Path, asset_root: Path | None = None) -> dict:
    _keys(spec, ("version", "title", "style", "font", "slide_count", "slides"), "spec")
    if type(spec.get("version", 1)) is not int or spec.get("version", 1) != 1:
        raise LayoutError("Only layout spec version 1 is supported")
    _text(spec.get("title"), "spec.title", limit=500)
    if spec.get("style", "warm") not in PALETTES:
        raise LayoutError("style must be warm, neutral or dark")
    slides = spec.get("slides")
    if not isinstance(slides, list) or not 1 <= len(slides) <= 100:
        raise LayoutError("slides must contain between 1 and 100 slides")
    if "slide_count" in spec and (type(spec["slide_count"]) is not int or spec["slide_count"] != len(slides)):
        raise LayoutError("slide_count must equal the exact number of supplied slides; the builder never adds or removes pages")
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    root = Path(asset_root or Path.cwd()).resolve()
    if not root.is_dir():
        raise LayoutError("asset_root must be an existing directory")
    with tempfile.TemporaryDirectory(prefix=".pptd-layout-", dir=output.parent) as temporary:
        stage = Path(temporary) / "project"
        stage.mkdir()
        builder = Builder(spec, stage, root)
        builder.build()
        report = audit_deck(stage / "deck.pptd")
        if report["status"] != "passed":
            messages = "; ".join(f"{i['id']}: {i['message']}" for i in report["issues"][:8])
            raise LayoutError("Generated layout did not pass deterministic checks: " + messages)
        (stage / "run/layout-qa.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
        _publish(stage, output)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Create bounded editable PPTD and a source-layout report")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--output", "--output-dir", dest="output", type=Path, required=True)
    build.add_argument("--asset-root", type=Path, help="Contained image/font paths resolve here; defaults to spec directory")
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        report = build_deck(spec, args.output, args.asset_root or args.spec.parent)
        print(json.dumps({"status": report["status"], "slide_count": report["slide_count"], "manifest": str(args.output / "deck.pptd"), "report": str(args.output / "run/layout-qa.json"), "visual_review": "not_performed"}, indent=2))
        return 0
    except (LayoutError, OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        print(f"layout_engine: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
