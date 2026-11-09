#!/usr/bin/env python3
"""Export a PPTD project as page images through Kimi's public editor for visual QA.

Reuses the same localhost SDK host and agent-browser flow as export_pptx.py, but
chooses 图片 in the export dialog, captures the images ZIP, unzips it, and stitches
all pages into a single overview image that a multimodal model can review.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from export_pptx import (
    HOST_TEMPLATE,
    BrowserSession,
    ExportError,
    build_payload,
    checked_archive_members,
    ensure_agent_browser,
    ensure_debug_chrome,
    ensure_websocket,
    find_download,
    find_manifest,
    log,
    ref_by_name,
    renderer_lock,
    require_remote_renderer_consent,
    run_command,
    serve,
    temporary_directory,
    wait_for_export_dialog,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
OVERVIEW_COLUMNS = 3
OVERVIEW_THUMB_WIDTH = 640
OVERVIEW_LABEL_HEIGHT = 32
OVERVIEW_GAP = 12
QA_MARKER = ".open-kimi-qa.json"
MAX_RENDERED_IMAGE_PIXELS = 40_000_000


def ensure_pillow() -> Tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont

        return Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ExportError(
            "Pillow is required for image QA. Install it in your chosen Python "
            "environment (for example: python3 -m pip install Pillow), then retry. "
            "No packages were installed automatically."
        ) from exc


def is_image_zip(path: Path) -> bool:
    if not path.is_file() or path.name.endswith(".crdownload"):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            checked_archive_members(archive)
            return any(
                Path(name).suffix.lower() in IMAGE_SUFFIXES
                for name in archive.namelist()
            )
    except (OSError, zipfile.BadZipFile, ExportError):
        return False


def page_sort_key(path: Path) -> Tuple[int, str]:
    match = re.search(r"(\d+)\D*$", path.stem)
    return (int(match.group(1)) if match else sys.maxsize, path.name)


def unzip_images(
    archive_path: Path, pages_dir: Path, expected_pages: Optional[int] = None,
) -> List[Path]:
    images: List[Path] = []
    with zipfile.ZipFile(archive_path) as archive:
        members = [
            info for info in checked_archive_members(archive)
            if not info.is_dir() and Path(info.filename).suffix.lower() in IMAGE_SUFFIXES
        ]
        names = [Path(info.filename.replace("\\", "/")).name for info in members]
        if len({name.casefold() for name in names}) != len(names):
            raise ExportError("rendered image ZIP contains duplicate flattened filenames")
        if expected_pages is not None and len(members) != expected_pages:
            raise ExportError(
                f"rendered image count {len(members)} does not match source count {expected_pages}"
            )
        if expected_pages is not None and expected_pages > 1:
            numbers = sorted(page_sort_key(Path(name))[0] for name in names)
            if numbers not in [list(range(expected_pages)), list(range(1, expected_pages + 1))]:
                raise ExportError("rendered image filenames do not establish an unambiguous page order")
        if not members:
            raise ExportError(f"no page images found in: {archive_path}")
        pages_dir.mkdir(parents=True, exist_ok=True)
        for info, name in zip(members, names):
            target = pages_dir / name
            with archive.open(info) as source, target.open("xb") as out:
                shutil.copyfileobj(source, out)
            images.append(target)
    images.sort(key=page_sort_key)
    if not images:
        raise ExportError(f"no page images found in: {archive_path}")
    return images


def owned_qa_files(output: Path, manifest: Path) -> List[Path]:
    """Validate a previous job directory before replacing any of its contents."""
    marker = output / QA_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ExportError(
            f"refusing to replace an unowned QA directory: {output}. Choose a new "
            "output path or review and move the old directory yourself."
        )
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportError(f"invalid QA ownership marker: {marker}") from exc
    if (not isinstance(data, dict) or data.get("owner") != "open-kimi-ppt" or data.get("schema") != 1
            or data.get("manifest") != str(manifest.resolve())):
        raise ExportError("QA ownership marker does not match this source project")
    files = data.get("files")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise ExportError("invalid file list in QA ownership marker")
    if len(files) != len(set(files)):
        raise ExportError("duplicate files in QA ownership marker")
    for item in files:
        if not re.fullmatch(r"(?:overview\.jpg|browser-raw\.zip|pages/[^/\\]+\.(?:png|jpe?g|webp))", item, re.I):
            raise ExportError(f"unrecognised managed QA filename: {item}")
    expected = set(files) | {QA_MARKER}
    actual = set()
    for path in output.rglob("*"):
        if path.is_symlink():
            raise ExportError(f"symlinks are not allowed in managed QA output: {path}")
        relative = path.relative_to(output).as_posix()
        if path.is_dir():
            if relative != "pages":
                raise ExportError(f"unmanaged directory exists in QA output: {relative}")
        elif path.is_file():
            actual.add(relative)
        else:
            raise ExportError(f"unexpected filesystem entry in QA output: {relative}")
    if actual != expected:
        raise ExportError("QA directory contains added or missing files; refusing to replace it")
    return [output / item for item in sorted(expected)]


def validate_qa_output(
    output: Path, manifest: Path, payload: Dict[str, Any], force: bool,
) -> Path:
    output = output.expanduser()
    if output.is_symlink():
        raise ExportError("QA output must not be a symlink")
    output = output.resolve()
    root = manifest.parent.resolve()
    protected = [manifest.resolve()]
    protected.extend((root / page["path"]).resolve() for page in payload["pages"])
    protected.extend((root / image).resolve() for image in payload["imageMap"])
    if output == root or output in root.parents or any(
        output == path or output in path.parents for path in protected
    ):
        raise ExportError("QA output would replace source files or a source ancestor")
    if output.exists():
        if not output.is_dir():
            raise ExportError("QA output exists and is not a directory")
        if any(output.iterdir()):
            if not force:
                raise ExportError(f"QA output already exists (use --force for owned output): {output}")
            owned_qa_files(output, manifest)
    return output


def publish_qa_output(staging: Path, output: Path, manifest: Path, payload: Dict[str, Any], force: bool) -> None:
    """Commit a complete staged QA run; retain the old run if commit fails."""
    validate_qa_output(output, manifest, payload, force)
    backup = output.with_name(f".{output.name}.previous-{uuid.uuid4().hex}")
    previous_files: List[Path] = []
    if output.exists():
        if any(output.iterdir()):
            previous_files = [path.relative_to(output) for path in owned_qa_files(output, manifest)]
        output.rename(backup)
    try:
        staging.rename(output)
    except Exception:
        if backup.exists() and not output.exists():
            backup.rename(output)
        raise
    if backup.exists():
        # Delete only the validated, enumerated output files. Never recursively
        # delete an arbitrary --output directory, even when --force was supplied.
        try:
            for relative in previous_files:
                (backup / relative).unlink()
            if (backup / "pages").is_dir():
                (backup / "pages").rmdir()
            backup.rmdir()
        except OSError:
            log(f"previous QA output retained because it changed during cleanup: {backup}")


def label_font(image_font: Any) -> Any:
    try:
        return image_font.load_default(size=18)
    except TypeError:  # older Pillow without the size argument
        return image_font.load_default()


def stitch_overview(
    images: Sequence[Path],
    output: Path,
    image_cls: Any,
    draw_cls: Any,
    image_font: Any,
) -> Path:
    thumbs: List[Tuple[str, Any]] = []
    for index, path in enumerate(images, start=1):
        with image_cls.open(path) as opened:
            if opened.width * opened.height > MAX_RENDERED_IMAGE_PIXELS:
                raise ExportError(f"rendered image exceeds 40 megapixels: {path.name}")
            frame = opened.convert("RGB")
            ratio = min(OVERVIEW_THUMB_WIDTH / frame.width, 640 / frame.height)
            thumb = frame.resize(
                (max(1, round(frame.width * ratio)), max(1, round(frame.height * ratio)))
            )
        thumbs.append((f"P{index}", thumb))

    columns = OVERVIEW_COLUMNS
    rows = math.ceil(len(thumbs) / columns)
    cell_height = OVERVIEW_LABEL_HEIGHT + max(thumb.height for _, thumb in thumbs)
    width = columns * OVERVIEW_THUMB_WIDTH + (columns + 1) * OVERVIEW_GAP
    height = rows * cell_height + (rows + 1) * OVERVIEW_GAP
    if width * height > 100_000_000:
        raise ExportError("overview exceeds 100 megapixels; split the deck for visual QA")

    overview = image_cls.new("RGB", (width, height), "#e5e7eb")
    draw = draw_cls.Draw(overview)
    font = label_font(image_font)
    for position, (label, thumb) in enumerate(thumbs):
        column = position % columns
        row = position // columns
        x = OVERVIEW_GAP + column * (OVERVIEW_THUMB_WIDTH + OVERVIEW_GAP)
        y = OVERVIEW_GAP + row * (cell_height + OVERVIEW_GAP)
        draw.rectangle(
            (x, y, x + OVERVIEW_THUMB_WIDTH, y + OVERVIEW_LABEL_HEIGHT - 4),
            fill="#111827",
        )
        draw.text((x + 8, y + 5), label, fill="#ffffff", font=font)
        overview.paste(thumb, (x, y + OVERVIEW_LABEL_HEIGHT))

    overview.save(output, "JPEG", quality=85)
    return output


OOPIF_URL_HINT = "kimi.com/neo-ppt"

# The export dialog's 图片 format option is a plain <div class="radio-group-item">
# without an ARIA role, so agent-browser's interactive snapshot never exposes it
# and cross-origin iframe rules block page-level eval. Clicking it requires CDP.
IMAGE_FORMAT_CLICK_JS = """
(() => {
  const items = [...document.querySelectorAll('.radio-group-item')];
  const pool = items.length
    ? items
    : [...document.querySelectorAll('div,span,label,button')].filter(
        (el) => el.children.length === 0
      );
  const target = pool.find((el) => el.textContent.trim() === '图片');
  if (!target) return null;
  target.click();
  return 'clicked';
})()
""".strip()

ACTIVE_FORMAT_JS = """
(() => {
  const active = document.querySelector('.radio-group-item.active');
  return active ? active.textContent.trim() : null;
})()
""".strip()


def browser_cdp_url(browser: BrowserSession) -> str:
    process = browser.run(["get", "cdp-url"], timeout=30)
    match = re.search(r"ws://\S+", process.stdout)
    if not match:
        raise ExportError(
            f"could not determine the browser CDP URL:\n{process.stdout[-500:]}"
        )
    return match.group(0)


def evaluate_in_iframe(cdp_url: str, url_hint: str, expression: str) -> Any:
    websocket = ensure_websocket()

    def call(socket: Any, request_id: int, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        socket.send(json.dumps({"id": request_id, "method": method, "params": params}))
        while True:
            message = json.loads(socket.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ExportError(f"CDP {method} failed: {message['error']}")
            return message.get("result", {})

    # websocket-client honors http_proxy env vars; the CDP endpoint is local,
    # so strip proxy settings instead of tunneling localhost through the proxy.
    proxy_env = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
    saved_proxy = {name: os.environ.pop(name) for name in proxy_env if name in os.environ}
    try:
        socket = websocket.create_connection(cdp_url, timeout=30, suppress_origin=True)
    finally:
        os.environ.update(saved_proxy)
    try:
        targets = call(socket, 1, "Target.getTargets", {}).get("targetInfos", [])
        target = next(
            (item for item in targets if url_hint in str(item.get("url", ""))), None
        )
        if target is None:
            visible = ", ".join(
                f"{item.get('type')}:{str(item.get('url', ''))[:80]}" for item in targets
            )
            raise ExportError(
                f"no browser target matches {url_hint!r}; observed: {visible}"
            )
        attached = call(
            socket,
            2,
            "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        session_id = attached["sessionId"]
        socket.send(
            json.dumps(
                {
                    "id": 3,
                    "sessionId": session_id,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }
            )
        )
        while True:
            message = json.loads(socket.recv())
            if message.get("id") != 3:
                continue
            if "error" in message:
                raise ExportError(f"CDP Runtime.evaluate failed: {message['error']}")
            result = message.get("result", {})
            if result.get("exceptionDetails"):
                details = result["exceptionDetails"]
                raise ExportError(f"iframe script failed: {details.get('text')}")
            return result.get("result", {}).get("value")
    finally:
        socket.close()


def select_image_format(browser: BrowserSession) -> None:
    cdp_url = browser_cdp_url(browser)
    value = evaluate_in_iframe(cdp_url, OOPIF_URL_HINT, IMAGE_FORMAT_CLICK_JS)
    if value != "clicked":
        raise ExportError("could not find the 图片 option in the export dialog")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        active = evaluate_in_iframe(cdp_url, OOPIF_URL_HINT, ACTIVE_FORMAT_JS)
        if active == "图片":
            return
        time.sleep(0.3)
    raise ExportError(f"image format was not activated; active option: {active!r}")


def export_images(
    source: Path,
    output: Path,
    keep_download: bool = False,
    force: bool = False,
    *,
    allow_remote_renderer: bool = False,
) -> Dict[str, Any]:
    require_remote_renderer_consent(allow_remote_renderer)
    manifest = find_manifest(source)
    payload = build_payload(manifest)
    output = validate_qa_output(output, manifest, payload, force)
    agent_browser = ensure_agent_browser()
    image_cls, draw_cls, image_font = ensure_pillow()
    ensure_websocket()

    log(f"manifest: {manifest}")
    with renderer_lock(), temporary_directory(prefix="open-kimi-ppt-images-") as temp_name:
        cdp_port = ensure_debug_chrome()
        temp_dir = Path(temp_name)
        download_dir = temp_dir / "downloads"
        download_dir.mkdir()
        shutil.copy2(HOST_TEMPLATE, temp_dir / HOST_TEMPLATE.name)
        (temp_dir / "payload.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        server, thread, url = serve(temp_dir)
        session = f"open-kimi-ppt-images-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        browser = BrowserSession(agent_browser, session, temp_dir, download_dir, cdp_port)
        try:
            log("opening the public Kimi slide editor")
            browser.open(url)
            browser.configure_downloads()
            browser.run(
                [
                    "wait",
                    "--fn",
                    'document.documentElement.dataset.deckStatus === "ready"',
                ],
                timeout=120,
            )
            browser.run(["set", "viewport", "1280", "720"])
            snapshot = browser.snapshot()
            export_ref = ref_by_name(snapshot, "导出", "button")
            browser.run(["click", f"@{export_ref}"])
            dialog = wait_for_export_dialog(browser)

            select_image_format(browser)
            dialog = wait_for_export_dialog(browser)

            started_at = time.time() - 1.0
            download_ref = ref_by_name(dialog, "下载", "button")
            log("rendering page images in the browser")
            browser.run(["click", f"@{download_ref}"], timeout=300)
            downloaded = find_download(
                (download_dir,),
                timeout=240,
                accept=is_image_zip,
                since=started_at,
            )
        finally:
            try:
                browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=str(output.parent)))
        try:
            images = unzip_images(downloaded, staging / "pages", expected_pages=len(payload["pages"]))
            if keep_download:
                shutil.copy2(downloaded, staging / "browser-raw.zip")
            stitch_overview(images, staging / "overview.jpg", image_cls, draw_cls, image_font)
            managed_files = sorted(
                path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file()
            )
            (staging / QA_MARKER).write_text(json.dumps({
                "owner": "open-kimi-ppt", "schema": 1,
                "manifest": str(manifest.resolve()), "files": managed_files,
            }, indent=2), encoding="utf-8")
            publish_qa_output(staging, output, manifest, payload, force)
        finally:
            # This is our fresh temporary staging directory, never --output.
            if staging.exists():
                shutil.rmtree(staging)

    page_paths = [entry["path"] for entry in payload["pages"]]
    mapping = [
        {
            "index": index,
            "image": f"pages/{path.name}",
            "page": page_paths[index - 1],
        }
        for index, path in enumerate(images, start=1)
    ]
    return {
        "pages": len(images),
        "overview": str(output / "overview.jpg"),
        "output": str(output),
        "images": mapping,
        "renderer": "kimi-public-iframe",
        "visualReview": "pending: inspect these rendered images separately",
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export a PPTD project as page images via Kimi's public editor, unzip "
            "them, and stitch an overview image for visual QA."
        )
    )
    parser.add_argument("input", type=Path, help=".pptd manifest or project directory")
    parser.add_argument(
        "--allow-remote-renderer", action="store_true",
        help="permit deck content/media to be exposed to Kimi's third-party iframe",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="output directory (default: <project>/.qa-images)",
    )
    parser.add_argument(
        "--keep-browser-raw",
        action="store_true",
        help="also keep the downloaded images ZIP beside the overview",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing QA directory owned by this exporter and source project",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        manifest = find_manifest(args.input)
        output = args.output or manifest.parent / ".qa-images"
        summary = export_images(
            args.input, output, args.keep_browser_raw, args.force,
            allow_remote_renderer=args.allow_remote_renderer,
        )
    except (ExportError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"open-kimi-ppt image export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
