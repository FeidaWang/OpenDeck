#!/usr/bin/env python3
"""Inspect existing local slide assets and record their declared provenance.

No images are generated or edited. Inspection verifies file bytes and bounded
format properties; visual quality and a declared source remain unverified.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import math
import re
import sys
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_ASSET_BYTES = 50_000_000
MAX_SVG_BYTES = 5_000_000
MAX_RASTER_PIXELS = 100_000_000
MAX_FRAMES = 500
SVG_NS = "http://www.w3.org/2000/svg"
SVG_TAGS = {"svg", "g", "defs", "path", "rect", "circle", "ellipse", "line",
            "polyline", "polygon", "linearGradient", "radialGradient", "stop",
            "clipPath", "mask", "use", "title", "desc"}


class AssetAuditError(ValueError):
    """An existing asset does not satisfy the supported inspection contract."""


def _fail(message: str) -> None:
    raise AssetAuditError(message)


def resolve_asset(path: str | Path, project_root: Path) -> Path:
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        _fail("project_root must be an existing directory")
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        _fail(f"Asset must be a file inside project_root: {path}")
    if resolved.stat().st_size >= MAX_ASSET_BYTES:
        _fail("Asset must be smaller than 50 MB")
    return resolved


def _svg_info(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_SVG_BYTES:
        _fail("SVG exceeds the 5 MB local inspection limit")
    if re.search(br"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", raw, flags=re.I):
        _fail("SVG document types, entities and stylesheets are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        _fail(f"Invalid SVG XML: {exc}")
    if root.tag not in {"svg", f"{{{SVG_NS}}}svg"}:
        _fail("Asset is not an SVG document")
    for element in root.iter():
        namespace, _, tag = element.tag.rpartition("}")
        if namespace and namespace != "{" + SVG_NS:
            _fail("Foreign namespaces are not accepted in SVG content")
        if tag not in SVG_TAGS:
            _fail(f"Only static vector SVG content is accepted; unsupported element: {tag}")
        for key, value in element.attrib.items():
            name = key.rpartition("}")[2]
            if name.lower().startswith("on"):
                _fail("SVG event handlers are not accepted")
            if name in {"href", "src"} and not value.startswith("#"):
                _fail("SVG external resources are not accepted")
            if re.search(r"(?:https?:|data:|javascript:|file:|@import|expression\s*\(|/\*|\\)", value, re.I):
                _fail("SVG contains an external resource or unsupported CSS")
            for target in re.findall(r"url\s*\((.*?)\)", value, flags=re.I):
                if not target.strip().strip("\"'").startswith("#"):
                    _fail("SVG URL references must be local fragments")

    def dimension(value: str | None) -> float | None:
        if value and re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:px)?", value):
            return float(value.removesuffix("px"))
        return None

    viewbox = None
    if root.get("viewBox") is not None:
        try:
            viewbox = [float(v) for v in re.split(r"[\s,]+", root.get("viewBox", "").strip())]
        except ValueError:
            _fail("SVG viewBox must contain four finite numbers")
        if len(viewbox) != 4 or not all(math.isfinite(v) for v in viewbox) or min(viewbox[2:]) <= 0:
            _fail("SVG viewBox must contain finite coordinates and positive dimensions")
    width, height = dimension(root.get("width")), dimension(root.get("height"))
    if width is None or height is None:
        if viewbox is None:
            _fail("SVG needs positive numeric dimensions or a valid viewBox")
        width, height = viewbox[2:]
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        _fail("SVG dimensions must be finite and positive")
    return {"format": "svg", "width": width, "height": height, "frames": 1,
            "has_transparency": None, "has_fully_transparent_pixels": None,
            "transparency_status": "requires_render", "static_vector_checked": True,
            "visual_review": "pending"}


def _raster_info(raw: bytes) -> dict[str, Any]:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        _fail("Pillow is required for raster inspection; install the declared project requirements explicitly")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    _fail("Only PNG, JPEG, WebP, GIF and static SVG assets are supported")
                fmt, frames = image.format.lower(), getattr(image, "n_frames", 1)
                if frames > MAX_FRAMES or image.width * image.height * frames > MAX_RASTER_PIXELS:
                    _fail("Raster asset exceeds the local inspection pixel budget")
                width, height = ImageOps.exif_transpose(image).size
                minimum_alpha = 255
                for frame in range(frames):
                    image.seek(frame)
                    image.load()
                    rgba = ImageOps.exif_transpose(image).convert("RGBA")
                    minimum_alpha = min(minimum_alpha, rgba.getchannel("A").getextrema()[0])
                return {"format": fmt, "width": width, "height": height,
                        "frames": frames, "has_transparency": minimum_alpha < 255,
                        "has_fully_transparent_pixels": minimum_alpha == 0,
                        "visual_review": "pending"}
    except AssetAuditError:
        raise
    except (OSError, ValueError, EOFError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        _fail(f"Invalid or oversized raster asset: {exc}")


def audit_asset(path: str | Path, project_root: Path) -> dict[str, Any]:
    file = resolve_asset(path, project_root)
    raw = file.read_bytes()
    if len(raw) >= MAX_ASSET_BYTES:
        _fail("Asset must be smaller than 50 MB")
    info = _svg_info(raw) if file.suffix.lower() == ".svg" else _raster_info(raw)
    return {"path": file.relative_to(Path(project_root).resolve()).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), **info}


def record_asset(path: str | Path, project_root: Path, source: str = "unknown",
                 notes: str = "") -> dict[str, Any]:
    if not isinstance(source, str) or not source.strip() or len(source) > 4096:
        _fail("source must be a nonempty declared origin of at most 4096 characters")
    if not isinstance(notes, str) or len(notes) > 8192:
        _fail("notes must be text of at most 8192 characters")
    return {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
            **audit_asset(path, project_root), "source": source.strip(),
            "source_verification": "unknown" if source.strip() == "unknown" else "operator_supplied_unverified",
            "notes": notes, "visual_review": "pending",
            "limitations": ["File properties and content hash were checked locally.",
                            "Declared origin, visual quality, licensing and application playback were not verified."]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit_parser = sub.add_parser("audit", help="Inspect an existing local image")
    record_parser = sub.add_parser("record", help="Inspect an image and record its declared source")
    record_parser.add_argument("--source", default="unknown")
    record_parser.add_argument("--notes", default="")
    for child in (audit_parser, record_parser):
        child.add_argument("--asset", type=Path, required=True)
        child.add_argument("--project-root", type=Path, required=True)
        child.add_argument("--output", type=Path, help="New JSON file; existing files are not overwritten")
    args = parser.parse_args(argv)
    try:
        result = audit_asset(args.asset, args.project_root) if args.command == "audit" else record_asset(
            args.asset, args.project_root, args.source, args.notes)
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as file:
                file.write(output)
        else:
            sys.stdout.write(output)
        return 0
    except (AssetAuditError, OSError, ValueError, TypeError) as exc:
        print(f"asset_audit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
