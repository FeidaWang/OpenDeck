#!/usr/bin/env python3
"""Render an existing final PPTX locally through LibreOffice and PyMuPDF.

The resulting report attests to conversion and file hashes only. Visual review
and application playback remain separate tasks. No dependencies are installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

MAX_PPTX_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_SLIDES = 200
MAX_PAGE_PIXELS = 20_000_000
MAX_TOTAL_PIXELS = 500_000_000
MAX_RENDER_BYTES = 512 * 1024 * 1024
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"


class RenderError(ValueError):
    """Local rendering could not establish the requested evidence."""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        if archive.getinfo(name).file_size > MAX_XML_BYTES:
            raise RenderError(f"PPTX XML part is too large: {name}")
        text = archive.read(name).decode("utf-8-sig")
        if re.search(r"<!DOCTYPE|<!ENTITY", text, flags=re.I):
            raise RenderError(f"PPTX XML declarations are unsupported: {name}")
        return ET.fromstring(text)
    except (KeyError, UnicodeError, ET.ParseError) as exc:
        raise RenderError(f"Invalid or missing PPTX XML part: {name}") from exc


def inspect_pptx(path: Path) -> dict:
    """Check the actual ZIP, presentation relationships and ordered slide count."""
    if path.suffix.lower() != ".pptx" or not path.is_file():
        raise RenderError("Input must be an existing .pptx file")
    if path.stat().st_size > MAX_PPTX_BYTES:
        raise RenderError("PPTX exceeds the 256 MiB input limit")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise RenderError("PPTX contains too many ZIP members")
            names, unpacked = set(), 0
            for info in members:
                name = info.filename
                if (name in names or name.startswith("/") or "\\" in name
                        or ".." in PurePosixPath(name).parts or re.match(r"^[A-Za-z]:", name)):
                    raise RenderError("PPTX contains a duplicate or unsafe ZIP member")
                if info.flag_bits & 1 or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise RenderError("Encrypted or symlink ZIP members are unsupported")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise RenderError("PPTX ZIP member exceeds the size limit")
                if name.lower().endswith("vbaproject.bin"):
                    raise RenderError("Macro content is unsupported by this static renderer")
                names.add(name)
                unpacked += info.file_size
            if unpacked > MAX_UNPACKED_BYTES:
                raise RenderError("PPTX exceeds the unpacked size limit")
            if archive.testzip() is not None:
                raise RenderError("PPTX ZIP checksum validation failed")
            content_types = _xml(archive, "[Content_Types].xml")
            if content_types.tag != f"{{{CONTENT_NS}}}Types" or not any(
                    node.get("PartName") == "/ppt/presentation.xml"
                    and node.get("ContentType") == PPTX_CONTENT_TYPE for node in content_types):
                raise RenderError("ZIP is not a standard PPTX presentation")
            presentation = _xml(archive, "ppt/presentation.xml")
            if presentation.tag != f"{{{PRESENTATION_NS}}}presentation":
                raise RenderError("PPTX has an unsupported presentation namespace")
            slides = {name for name in names if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", name)}
            slide_ids = presentation.findall(f"{{{PRESENTATION_NS}}}sldIdLst/{{{PRESENTATION_NS}}}sldId")
            if not 1 <= len(slide_ids) <= MAX_SLIDES or len(slides) != len(slide_ids):
                raise RenderError("PPTX slide count is invalid or inconsistent")
            relationships = {}
            for name in names:
                if not name.endswith(".rels"):
                    continue
                rels = _xml(archive, name)
                if rels.tag != f"{{{PACKAGE_REL_NS}}}Relationships":
                    raise RenderError(f"Invalid PPTX relationships: {name}")
                for rel in rels:
                    if rel.get("TargetMode") == "External" and not rel.get("Type", "").endswith("/hyperlink"):
                        raise RenderError("External linked media or document resources are unsupported")
                    if name == "ppt/_rels/presentation.xml.rels":
                        key = rel.get("Id")
                        if not key or key in relationships:
                            raise RenderError("PPTX contains duplicate or missing relationship IDs")
                        relationships[key] = rel
            ordered = []
            for slide in slide_ids:
                rel = relationships.get(slide.get(f"{{{REL_NS}}}id"))
                if rel is None or rel.get("Type") != f"{REL_NS}/slide" or rel.get("TargetMode") == "External":
                    raise RenderError("PPTX slide relationship is missing or invalid")
                target = rel.get("Target", "")
                part = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("ppt", target))
                if part not in slides or part in ordered:
                    raise RenderError("PPTX slide relationship count is inconsistent")
                if _xml(archive, part).tag != f"{{{PRESENTATION_NS}}}sld":
                    raise RenderError("PPTX slide XML has an invalid root")
                ordered.append(part)
            size = presentation.find(f"{{{PRESENTATION_NS}}}sldSz")
            try:
                width, height = int(size.get("cx")), int(size.get("cy"))
            except (AttributeError, ValueError, TypeError) as exc:
                raise RenderError("PPTX has no valid slide dimensions") from exc
            if width <= 0 or height <= 0:
                raise RenderError("PPTX slide dimensions must be positive")
            return {"slide_count": len(ordered), "aspect_ratio": width / height}
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise RenderError(f"PPTX ZIP could not be validated: {exc}") from exc


def _contained(root: Path, value: str | Path, *, existing: bool = False, output: bool = False) -> Path:
    candidate = Path(value)
    candidate = candidate if candidate.is_absolute() else root / candidate
    if output:
        for component in (candidate, *candidate.parents):
            if component == root:
                break
            if component.is_symlink():
                raise RenderError("Output paths must not contain symlinks")
    resolved = candidate.resolve(strict=existing)
    if resolved == root or not resolved.is_relative_to(root):
        raise RenderError("Input and outputs must be contained inside project_root")
    return resolved


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise RenderError("Local PPTX rendering exceeded its timeout")
    return seconds


def _run(argv: list[str], cwd: Path, timeout: float, *, capture: bool = False) -> str:
    """Use argv, an isolated process group and a bounded wait; never a shell."""
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                   stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
                                   start_new_session=os.name == "posix")
    except OSError as exc:
        raise RenderError(f"Could not start the local renderer: {exc}") from exc
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate()
        raise RenderError("Local PPTX rendering exceeded its timeout") from exc
    if process.returncode:
        raise RenderError(f"LibreOffice exited with status {process.returncode}")
    return (stdout or b"").decode("utf-8", errors="replace").strip()[:1000]


def find_soffice(explicit: str | Path | None = None) -> Path:
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_absolute():
            found = shutil.which(str(candidate))
            candidate = Path(found) if found else candidate
    else:
        found = shutil.which("soffice") or shutil.which("libreoffice")
        runtime = os.environ.get("CODEX_PRIMARY_RUNTIME_ROOT")
        candidate = Path(found) if found else (Path(runtime) / "dependencies/bin/override/soffice" if runtime else Path(""))
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise RenderError("LibreOffice is unavailable; supply an existing executable with --soffice. Nothing was installed.")
    return candidate.resolve()


def _render_pdf(pdf: Path, images: Path, count: int, ratio: float, dpi: int, deadline: float) -> list[dict]:
    try:
        import fitz
    except ImportError as exc:
        raise RenderError("PyMuPDF is required for local PDF rasterization. Nothing was installed.") from exc
    if not pdf.is_file() or pdf.is_symlink() or pdf.stat().st_size > MAX_RENDER_BYTES:
        raise RenderError("LibreOffice did not produce a bounded PDF file")
    pages = []
    try:
        with fitz.open(pdf) as document:
            if not document.is_pdf or document.needs_pass or document.page_count != count:
                raise RenderError("Rendered PDF page count does not match the final PPTX slide count")
            total_pixels = 0
            for page in document:
                rectangle = page.rect
                if not all(math.isfinite(v) and v > 0 for v in (rectangle.width, rectangle.height)):
                    raise RenderError("Rendered PDF has invalid page dimensions")
                if abs(rectangle.width / rectangle.height / ratio - 1) > 0.005:
                    raise RenderError("Rendered PDF aspect ratio differs from the final PPTX")
                pixels = math.ceil(rectangle.width * dpi / 72) * math.ceil(rectangle.height * dpi / 72)
                total_pixels += pixels
                if pixels > MAX_PAGE_PIXELS or total_pixels > MAX_TOTAL_PIXELS:
                    raise RenderError("Rendered PDF exceeds the page or total pixel budget")
            total_bytes = 0
            for number, page in enumerate(document, 1):
                _remaining(deadline)
                image = images / f"page-{number:03d}.png"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), colorspace=fitz.csRGB, alpha=False)
                pixmap.save(str(image))
                total_bytes += image.stat().st_size
                if total_bytes > MAX_RENDER_BYTES:
                    raise RenderError("Rendered PNG files exceed the output byte budget")
                pages.append({"page": number, "name": image.name, "sha256": digest(image),
                              "width": pixmap.width, "height": pixmap.height, "status": "rendered"})
                _remaining(deadline)
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"Rendered PDF could not be rasterized: {exc}") from exc
    return pages


def render_pptx(source: str | Path, project_root: str | Path, *, output: str | Path = "run/final-renders",
                report: str | Path = "run/final-render-qa.json", soffice: str | Path | None = None,
                dpi: int = 144, timeout: float = 180) -> dict:
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 300:
        raise RenderError("dpi must be an integer between 72 and 300")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 900:
        raise RenderError("timeout must be positive and at most 900 seconds")
    deadline = time.monotonic() + timeout
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise RenderError("project_root must be an existing directory")
    source_path = _contained(root, source, existing=True)
    destination = _contained(root, output, output=True)
    report_path = _contained(root, report, output=True)
    if report_path.exists() or report_path.is_symlink():
        raise RenderError("Report already exists; choose a new report path")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise RenderError("Render output must be a new or empty directory")
    if not source_path.is_file() or source_path.stat().st_size > MAX_PPTX_BYTES:
        raise RenderError("PPTX must be a regular file within the input size limit")
    source_hash = digest(source_path)
    metadata = inspect_pptx(source_path)
    executable = find_soffice(soffice)
    _remaining(deadline)
    destination.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    marker = destination / ".render-owner"
    token = uuid.uuid4().hex
    try:
        with marker.open("x", encoding="utf-8") as handle:
            handle.write(token)
    except OSError as exc:
        raise RenderError("Render output is already owned by another operation") from exc
    identity = destination.stat().st_dev, destination.stat().st_ino
    published = []
    try:
        if set(destination.iterdir()) != {marker}:
            raise RenderError("Render output changed before rendering began")
        with tempfile.TemporaryDirectory(prefix=".final-pptx-stage-", dir=destination.parent) as temporary:
            stage = Path(temporary)
            pinned = stage / "source.pptx"
            shutil.copyfile(source_path, pinned)
            if digest(pinned) != source_hash:
                raise RenderError("Source PPTX changed while it was being staged")
            profile, pdf_dir, images = stage / "profile", stage / "pdf", stage / "images"
            profile.mkdir()
            pdf_dir.mkdir()
            images.mkdir()
            installation = f"-env:UserInstallation={profile.as_uri()}"
            version = _run([str(executable), installation, "--version"], stage,
                           min(10, _remaining(deadline)), capture=True)
            if not version or "LibreOffice" not in version:
                raise RenderError("The selected executable did not identify as LibreOffice")
            argv = [str(executable), installation, "--headless", "--nologo", "--nodefault", "--norestore",
                    "--convert-to", "pdf:impress_pdf_Export", "--outdir", str(pdf_dir), str(pinned)]
            _run(argv, stage, _remaining(deadline))
            if digest(source_path) != source_hash:
                raise RenderError("Source PPTX changed during rendering")
            page_data = _render_pdf(pdf_dir / "source.pdf", images, metadata["slide_count"],
                                    metadata["aspect_ratio"], dpi, deadline)
            pages = [{**{key: value for key, value in page.items() if key != "name"},
                      "path": (destination / page["name"]).relative_to(root).as_posix()} for page in page_data]
            source_info = {"path": source_path.relative_to(root).as_posix(), "sha256": source_hash}
            result = {"schema_version": 1, "status": "passed", "kind": "final_pptx_render",
                      "render_source": "final_pptx", "slide_count": metadata["slide_count"],
                      "source_pptx": source_info, "renderer": {"name": "LibreOffice", "version": version},
                      "dpi": dpi, "files": [source_info, *[{"path": page["path"], "sha256": page["sha256"]} for page in pages]],
                      "pages": pages, "visual_review": "not_performed", "playback": "not_performed"}
            staged_report = stage / "report.json"
            staged_report.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            _remaining(deadline)
            if (destination.is_symlink() or (destination.stat().st_dev, destination.stat().st_ino) != identity
                    or marker.read_text(encoding="utf-8") != token or set(destination.iterdir()) != {marker}):
                raise RenderError("Render output ownership changed before publication")
            if _contained(root, report_path, output=True) != report_path or digest(source_path) != source_hash:
                raise RenderError("Source or report path changed before publication")
            # Hard links make each completed file visible atomically and fail if
            # another operation created its destination. No existing file is replaced.
            for page in page_data:
                target = destination / page["name"]
                os.link(images / page["name"], target)
                published.append((target, target.stat().st_ino))
            os.link(staged_report, report_path)
            published.append((report_path, report_path.stat().st_ino))
            return result
    except BaseException:
        # Roll back only files created and still owned by this call.
        for path, inode in reversed(published):
            try:
                if not path.is_symlink() and path.stat().st_ino == inode:
                    path.unlink()
            except OSError:
                pass
        raise
    finally:
        try:
            if not marker.is_symlink() and marker.read_text(encoding="utf-8") == token:
                marker.unlink()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--project-root", type=Path, help="Defaults to the input PPTX's parent directory")
    parser.add_argument("--output", type=Path, help="New or empty PNG directory; defaults to PROJECT/run/final-renders")
    parser.add_argument("--report", type=Path, help="New JSON file; defaults to PROJECT/run/final-render-qa.json")
    parser.add_argument("--soffice", help="Existing LibreOffice executable; otherwise discovered locally")
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        # Explicit CLI paths follow normal shell conventions. Defaults and the
        # Python API's relative paths are relative to the project root.
        source = args.pptx.resolve(strict=True)
        root = args.project_root.resolve(strict=True) if args.project_root else source.parent
        output = args.output.absolute() if args.output else Path("run/final-renders")
        report = args.report.absolute() if args.report else Path("run/final-render-qa.json")
        result = render_pptx(source, root, output=output, report=report,
                             soffice=args.soffice, dpi=args.dpi, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (RenderError, OSError, ValueError) as exc:
        print(f"render_pptx: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
