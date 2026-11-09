#!/usr/bin/env python3
"""Read a PPTD project from disk and check that every file it refers to stays inside it.

Local and read-only: no network, no browser, no installs.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_MEDIA_BYTES = 200 * 1024 * 1024


class ProjectError(RuntimeError):
    pass


def ensure_pyyaml() -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise ProjectError(
            "PyYAML is required to read PPTD. Install it in your chosen Python "
            "environment (for example: python3 -m pip install PyYAML), then retry. "
            "No packages were installed automatically."
        ) from exc
    return yaml


def find_manifest(source: Path) -> Path:
    source = source.expanduser().resolve()
    if source.is_file():
        if source.suffix.lower() != ".pptd":
            raise ProjectError(f"input must be a .pptd file or project directory: {source}")
        return source
    if not source.is_dir():
        raise ProjectError(f"input does not exist: {source}")
    manifests = sorted(source.rglob("*.pptd"))
    if not manifests:
        raise ProjectError(f"no .pptd manifest found under: {source}")
    if len(manifests) > 1:
        choices = "\n  ".join(str(path) for path in manifests[:20])
        raise ProjectError(
            "multiple .pptd manifests found; pass one manifest explicitly:\n  " + choices
        )
    return manifests[0]


def read_yaml_mapping(path: Path) -> Tuple[str, Dict[str, Any]]:
    yaml = ensure_pyyaml()
    text = path.read_text(encoding="utf-8")
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProjectError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectError(f"expected a YAML mapping in {path}")
    return text, value


def safe_project_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ProjectError("project path must be a non-empty string")
    root = root.resolve()
    relative = relative.replace("\\", "/")
    if (relative.startswith("/") or re.match(r"^[A-Za-z]:", relative)
            or "\x00" in relative or ".." in relative.split("/")):
        raise ProjectError(f"project path must stay relative to the PPTD directory: {relative}")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProjectError(f"project path escapes the PPTD directory: {relative}") from exc
    return candidate


def image_sources(document: Any) -> List[str]:
    """Find declared Image.src/ImageFill.src values, not every image on disk."""
    found: List[str] = []
    pending = [document]
    seen = set()
    while pending:
        value = pending.pop()
        if not isinstance(value, (dict, list)) or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, list):
            pending.extend(value)
            continue
        if value.get("elementType") == "image" or value.get("type") == "image":
            src = value.get("src")
            if not isinstance(src, str) or not src.strip():
                raise ProjectError("image and image-fill src must be a non-empty string")
            found.append(src)
        pending.extend(value.values())
    return found


def local_images(root: Path, references: Iterable[str] = ()) -> List[str]:
    """Check each local image reference and return the project-relative paths."""
    found: List[str] = []
    total = 0
    root = root.resolve()
    for requested in sorted(set(references)):
        if re.match(r"^(?:https?://|data:image/)", requested, flags=re.IGNORECASE):
            continue
        if urllib.parse.urlsplit(requested).scheme:
            raise ProjectError(f"unsupported image resource URL: {requested}")
        relative = urllib.parse.unquote(requested).replace("\\", "/")
        path = safe_project_path(root, relative)
        if not path.is_file():
            raise ProjectError(f"missing referenced image: {requested}")
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ProjectError(f"unsupported local image format: {requested}")
        size = path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise ProjectError(f"referenced image exceeds 20 MiB: {requested}")
        if total + size > MAX_MEDIA_BYTES:
            raise ProjectError("referenced local images exceed 200 MiB; reduce media size")
        total += size
        found.append(Path(relative).as_posix())
    return found


def load_project(manifest: Path) -> Dict[str, Any]:
    manifest_text, manifest_data = read_yaml_mapping(manifest)
    if manifest_data.get("version") != "v2":
        raise ProjectError("this project reads PPTD version: v2 only")
    page_paths = manifest_data.get("pages")
    if not isinstance(page_paths, list) or not page_paths:
        raise ProjectError("PPTD manifest must contain a non-empty pages list")

    root = manifest.parent.resolve()
    pages: List[Dict[str, str]] = []
    references = image_sources(manifest_data)
    for entry in page_paths:
        page_path = safe_project_path(root, entry)
        if not page_path.is_file():
            raise ProjectError(f"missing page file: {entry}")
        page_text, page_data = read_yaml_mapping(page_path)
        if not isinstance(page_data.get("elements"), list):
            raise ProjectError(f"page elements must be an array: {entry}")
        references.extend(image_sources(page_data))
        pages.append({"path": str(entry), "content": page_text})

    return {
        "title": str(manifest_data.get("title") or manifest.stem),
        "manifestPath": manifest.name,
        "manifestContent": manifest_text,
        "pages": pages,
        "images": local_images(root, references),
    }
