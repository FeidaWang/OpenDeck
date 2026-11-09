#!/usr/bin/env python3
"""Export a PPTD project through Kimi's public browser-side PPTX writer.

The script uses a temporary localhost SDK host and agent-browser. The document
and referenced images are exposed to third-party JavaScript in Kimi's iframe.
This is not an offline renderer or a guarantee that data remains on the device.
Live export requires explicit --allow-remote-renderer consent. Imports and local
payload validation never install dependencies or start a browser.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

SKILL_DIR = Path(__file__).resolve().parent.parent
HOST_TEMPLATE = Path(__file__).with_name("export_host.html")
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_EMBEDDED_MEDIA_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10000
MAX_ARCHIVE_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
)
FADE_TRANSITION_XML = (
    '<p:transition spd="fast" advClick="1"><p:fade/></p:transition>'
)
MIN_AGENT_BROWSER_VERSION = (0, 33, 2)
MIN_NODE_MAJOR = 18
NODE_INSTALL_HINT = "Install Node.js 18+ from https://nodejs.org, then retry."


class ExportError(RuntimeError):
    pass


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        return


def log(message: str) -> None:
    print(f"[open-kimi-ppt] {message}", file=sys.stderr, flush=True)


def run_command(
    command: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    """Capture merged stdout/stderr via a temp file.

    On Windows, agent-browser's detached daemon can inherit a PIPE handle and
    prevent EOF, deadlocking ``subprocess.run(stdout=PIPE)``. Decoding with the
    system locale (GBK on zh-CN Windows) can also raise UnicodeDecodeError.
    Writing to a UTF-8 file avoids both failures.
    """
    handle, sink_path = tempfile.mkstemp(prefix="open-kimi-ppt-", suffix=".log")
    os.close(handle)
    sink = Path(sink_path)
    output = ""
    try:
        with sink.open("w", encoding="utf-8", errors="replace") as out:
            returncode = subprocess.call(
                list(command),
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        output = sink.read_text(encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        try:
            output = sink.read_text(encoding="utf-8", errors="replace")
        except OSError:
            output = ""
        raise subprocess.TimeoutExpired(
            cmd=list(command),
            timeout=timeout,
            output=output,
        ) from exc
    finally:
        try:
            sink.unlink(missing_ok=True)
        except OSError:
            # WinError 32: daemon may still hold the log file handle.
            pass
    return subprocess.CompletedProcess(list(command), returncode, output, None)


def temporary_directory(prefix: str) -> Any:
    # ignore_cleanup_errors avoids masking the real export error when a Windows
    # browser daemon still holds files under the temp tree (Python 3.10+).
    try:
        return tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)
    except TypeError:
        return tempfile.TemporaryDirectory(prefix=prefix)


def default_downloads_dir() -> Path:
    home = Path.home()
    candidates: List[Path] = []
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / "Downloads")
    candidates.extend((home / "Downloads", home / "下载"))
    for path in candidates:
        if path.is_dir():
            return path
    return home / "Downloads"


def ensure_pyyaml() -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise ExportError(
            "PyYAML is required to read PPTD. Install it in your chosen Python "
            "environment (for example: python3 -m pip install PyYAML), then retry. "
            "No packages were installed automatically."
        ) from exc
    return yaml


def ensure_websocket() -> Any:
    try:
        import websocket
    except ImportError as exc:
        raise ExportError(
            "websocket-client is required for isolated browser downloads. Install "
            "it in your chosen Python environment (for example: python3 -m pip "
            "install websocket-client), then retry. No packages were installed "
            "automatically."
        ) from exc
    return websocket


def require_remote_renderer_consent(allowed: bool) -> None:
    if not allowed:
        raise ExportError(
            "Kimi rendering exposes deck content and referenced media to a "
            "third-party-origin iframe. Review this backend for your content, then "
            "pass --allow-remote-renderer to permit it. Local PPTD validation and "
            "payload construction do not require this flag."
        )


@contextlib.contextmanager
def renderer_lock() -> Iterable[None]:
    """Avoid conflicting CDP download settings in a reused debug browser."""
    identity = hashlib.sha256(str(Path.home()).encode("utf-8")).hexdigest()[:16]
    lock = Path(tempfile.gettempdir()) / f"open-kimi-ppt-render-{identity}.lock"
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(token)
    except FileExistsError as exc:
        raise ExportError(
            "another Open Kimi render owns the browser lock. Wait for it to "
            f"finish. If that process stopped, inspect and remove its stale lock: {lock}"
        ) from exc
    try:
        yield
    finally:
        try:
            if lock.read_text(encoding="utf-8") == token:
                lock.unlink()
        except OSError:
            pass


def checked_archive_members(archive: zipfile.ZipFile) -> List[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ExportError("export ZIP contains too many members")
    names = set()
    total = 0
    for info in members:
        if info.filename in names:
            raise ExportError(f"duplicate ZIP member: {info.filename}")
        names.add(info.filename)
        normalised = info.filename.replace("\\", "/")
        if (normalised.startswith("/") or ".." in normalised.split("/")
                or re.match(r"^[A-Za-z]:", normalised)):
            raise ExportError(f"unsafe ZIP member path: {info.filename}")
        if info.flag_bits & 1:
            raise ExportError("encrypted export ZIP entries are not supported")
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise ExportError(f"symlink ZIP member is not supported: {info.filename}")
        if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ExportError(f"export ZIP member exceeds size limit: {info.filename}")
        total += info.file_size
    if total > MAX_ARCHIVE_TOTAL_BYTES:
        raise ExportError("export ZIP exceeds the 512 MiB uncompressed limit")
    return members


def parse_version(output: str) -> Tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)\b", output)
    if not match:
        raise ExportError(f"could not parse agent-browser version from: {output.strip()}")
    return tuple(int(part) for part in match.groups())


def parse_node_version(output: str) -> Tuple[int, int, int]:
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)\b", output)
    if not match:
        raise ExportError(f"could not parse Node.js version from: {output.strip()}")
    return tuple(int(part) for part in match.groups())


def read_agent_browser_version(executable: str) -> Tuple[int, int, int]:
    process = run_command([executable, "--version"], timeout=30)
    if process.returncode != 0:
        raise ExportError(f"agent-browser --version failed:\n{process.stdout[-2000:]}")
    return parse_version(process.stdout)


def ensure_nodejs() -> str:
    executable = shutil.which("node")
    if not executable:
        raise ExportError(f"Node.js is not installed or not on PATH. {NODE_INSTALL_HINT}")

    process = run_command([executable, "--version"], timeout=30)
    if process.returncode != 0:
        raise ExportError(f"node --version failed:\n{process.stdout[-2000:]}")

    version = parse_node_version(process.stdout)
    if version[0] < MIN_NODE_MAJOR:
        raise ExportError(
            f"Node.js {MIN_NODE_MAJOR}+ is required; found "
            f"{'.'.join(map(str, version))} ({process.stdout.strip()}). {NODE_INSTALL_HINT}"
        )

    npm = shutil.which("npm")
    if not npm:
        raise ExportError(
            "npm is not installed or not on PATH. "
            f"npm ships with Node.js. {NODE_INSTALL_HINT}"
        )

    log(f"Node.js version: {'.'.join(map(str, version))}")
    return executable


def ensure_agent_browser() -> str:
    ensure_nodejs()

    executable = shutil.which("agent-browser")
    version = read_agent_browser_version(executable) if executable else None
    if version is not None and version >= MIN_AGENT_BROWSER_VERSION:
        log(f"agent-browser version: {'.'.join(map(str, version))}")
        return executable

    current = "not installed" if version is None else ".".join(map(str, version))
    minimum = ".".join(map(str, MIN_AGENT_BROWSER_VERSION))
    raise ExportError(
        f"agent-browser {current}; version {minimum}+ is required. Install a "
        "reviewed version and expose its executable on PATH before retrying. "
        "No global npm installation or automatic upgrade was performed."
    )


DEBUG_CHROME_PORT = 9337
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def cdp_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2):
            return True
    except OSError:
        return False


def ensure_debug_chrome() -> Optional[int]:
    """Return a CDP port for agent-browser to connect to (Windows only).

    On Windows agent-browser cannot launch Chrome itself: the Chrome launcher
    process hands off to a child and exits, which agent-browser mistakes for a
    crash ("Chrome exited early without writing DevToolsActivePort"). The
    export therefore always drives an externally started browser. An
    already-working AGENT_BROWSER_CDP wins; otherwise a dedicated debug
    instance is started (or reused) on port 9337. The instance is left running
    on purpose: relaunching with the same profile joins the existing browser,
    so repeated exports reuse one instance instead of piling up processes.
    """
    if sys.platform != "win32":
        return None

    explicit = os.environ.get("AGENT_BROWSER_CDP")
    if explicit:
        try:
            if cdp_alive(int(explicit)):
                return int(explicit)
        except ValueError:
            pass
        log(f"AGENT_BROWSER_CDP={explicit} is not answering; starting a debug browser instead")

    port = DEBUG_CHROME_PORT
    if cdp_alive(port):
        return port
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            # Port taken by something that is not a CDP endpoint.
            with socket.socket() as spare:
                spare.bind(("127.0.0.1", 0))
                port = spare.getsockname()[1]

    executable = next((c for c in CHROME_CANDIDATES if Path(c).is_file()), None)
    if executable is None:
        raise ExportError(
            "no Chrome or Edge found to drive the export; install Google Chrome, "
            "or start a browser with --remote-debugging-port yourself and set "
            "AGENT_BROWSER_CDP to that port"
        )
    profile = Path(tempfile.gettempdir()) / "okp-cdp-profile"
    log(f"starting debug browser on port {port}: {executable}")
    subprocess.Popen(
        [
            executable,
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-position=-2400,0",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if cdp_alive(port):
            return port
        time.sleep(0.5)
    raise ExportError(f"debug browser did not open CDP port {port} within 20s")


def find_manifest(source: Path) -> Path:
    source = source.expanduser().resolve()
    if source.is_file():
        if source.suffix.lower() != ".pptd":
            raise ExportError(f"input must be a .pptd file or project directory: {source}")
        return source
    if not source.is_dir():
        raise ExportError(f"input does not exist: {source}")
    manifests = sorted(source.rglob("*.pptd"))
    if not manifests:
        raise ExportError(f"no .pptd manifest found under: {source}")
    if len(manifests) > 1:
        choices = "\n  ".join(str(path) for path in manifests[:20])
        raise ExportError(
            "multiple .pptd manifests found; pass one manifest explicitly:\n  " + choices
        )
    return manifests[0]


def read_yaml_mapping(path: Path) -> Tuple[str, Dict[str, Any]]:
    yaml = ensure_pyyaml()
    text = path.read_text(encoding="utf-8")
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ExportError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"expected a YAML mapping in {path}")
    return text, value


def safe_project_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ExportError("project path must be a non-empty string")
    root = root.resolve()
    relative = relative.replace("\\", "/")
    if (relative.startswith("/") or re.match(r"^[A-Za-z]:", relative)
            or "\x00" in relative or ".." in relative.split("/")):
        raise ExportError(f"project path must stay relative to the PPTD directory: {relative}")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ExportError(f"project path escapes the PPTD directory: {relative}") from exc
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
                raise ExportError("image and image-fill src must be a non-empty string")
            found.append(src)
        pending.extend(value.values())
    return found


def build_image_map(root: Path, references: Iterable[str] = ()) -> Dict[str, str]:
    image_map: Dict[str, str] = {}
    total = 0
    root = root.resolve()
    for requested in sorted(set(references)):
        if re.match(r"^(?:https?://|data:image/)", requested, flags=re.IGNORECASE):
            continue
        if urllib.parse.urlsplit(requested).scheme:
            raise ExportError(f"unsupported image resource URL: {requested}")
        relative = urllib.parse.unquote(requested).replace("\\", "/")
        path = safe_project_path(root, relative)
        if not path.is_file():
            raise ExportError(f"missing referenced image: {requested}")
        if path.suffix.lower() not in IMAGE_MIME:
            raise ExportError(f"unsupported local image format: {requested}")
        size = path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise ExportError(f"referenced image exceeds 20 MiB: {requested}")
        if total + size > MAX_EMBEDDED_MEDIA_BYTES:
            raise ExportError(
                "referenced local image payload exceeds 200 MiB; reduce media size"
            )
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        # Retain the requested relative name so valid in-project symlinks still
        # resolve from PPTD. The resolved target above has passed containment.
        rel = Path(relative).as_posix()
        image_map[rel] = f"data:{IMAGE_MIME[path.suffix.lower()]};base64,{data}"
        total += size
    if image_map:
        log(f"prepared {len(image_map)} local image resource(s), {total} bytes")
    return image_map


def build_payload(manifest: Path) -> Dict[str, Any]:
    manifest_text, manifest_data = read_yaml_mapping(manifest)
    if manifest_data.get("version") != "v2":
        raise ExportError("local PPTX export currently requires PPTD version: v2")
    page_paths = manifest_data.get("pages")
    if not isinstance(page_paths, list) or not page_paths:
        raise ExportError("PPTD manifest must contain a non-empty pages list")

    root = manifest.parent.resolve()
    pages: List[Dict[str, str]] = []
    references = image_sources(manifest_data)
    for entry in page_paths:
        page_path = safe_project_path(root, entry)
        if not page_path.is_file():
            raise ExportError(f"missing page file: {entry}")
        page_text, page_data = read_yaml_mapping(page_path)
        if not isinstance(page_data.get("elements"), list):
            raise ExportError(f"page elements must be an array: {entry}")
        references.extend(image_sources(page_data))
        pages.append({"path": str(entry), "content": page_text})

    title = str(manifest_data.get("title") or manifest.stem)
    return {
        "id": f"local-export-{uuid.uuid4().hex}",
        "title": title,
        "manifestPath": manifest.name,
        "manifestContent": manifest_text,
        "pages": pages,
        "imageMap": build_image_map(root, references),
    }


def json_result(output: str) -> Dict[str, Any]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ExportError(f"agent-browser returned no JSON object:\n{output[-2000:]}")


class BrowserSession:
    def __init__(
        self,
        executable: str,
        session: str,
        cwd: Path,
        download_dir: Path,
        cdp_port: Optional[int] = None,
    ):
        self.executable = executable
        self.session = session
        self.cwd = cwd
        self.opened_url: Optional[str] = None
        # CDP sets this per-job destination after browser startup. Never fall back
        # to a shared Downloads folder or delete files outside this job directory.
        self.download_dir = download_dir
        self.env = os.environ.copy()
        self.env.setdefault("AGENT_BROWSER_DEFAULT_TIMEOUT", "60000")
        self.env.setdefault("AGENT_BROWSER_IDLE_TIMEOUT_MS", "180000")
        if cdp_port is not None:
            self.env["AGENT_BROWSER_CDP"] = str(cdp_port)

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: int = 90,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.executable, "--session", self.session, *args]
        process = run_command(command, cwd=self.cwd, env=self.env, timeout=timeout)
        if check and process.returncode != 0:
            raise ExportError(
                f"agent-browser command failed ({process.returncode}): "
                f"{' '.join(args)}\n{process.stdout[-4000:]}"
            )
        return process

    def open(self, url: str) -> None:
        # Avoid --download-path: agent-browser ≤0.33.2 + Chrome may cancel downloads
        # when given a verbatim Windows path. Files land in the default Downloads folder.
        self.run(["open", url], timeout=90)
        self.opened_url = url

    def configure_downloads(self) -> None:
        """Require Chromium's isolated download destination before exporting."""
        if not self.opened_url:
            raise ExportError("open the local export host before configuring downloads")
        websocket = ensure_websocket()
        process = self.run(["get", "cdp-url"], timeout=30)
        match = re.search(r"ws://\S+", process.stdout)
        if not match:
            raise ExportError(
                "browser exposed no local CDP URL; isolated downloads cannot be "
                "configured. Shared Downloads fallback is disabled."
            )
        url = match.group(0)
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ExportError("download isolation requires a loopback Chromium CDP endpoint")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        connection = None
        try:
            connection = websocket.create_connection(
                url, timeout=30, suppress_origin=True,
                http_no_proxy=[parsed.hostname],
            )
            def call(message_id: int, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
                connection.send(json.dumps({"id": message_id, "method": method, "params": params}))
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    connection.settimeout(max(0.1, deadline - time.monotonic()))
                    response = json.loads(connection.recv())
                    if response.get("id") != message_id:
                        continue
                    if "error" in response:
                        raise ExportError(f"Chromium download isolation failed: {response['error']}")
                    return response.get("result", {})
                raise ExportError("timed out configuring isolated Chromium downloads")

            targets = call(1, "Target.getTargets", {}).get("targetInfos", [])
            hosts = [target for target in targets if target.get("type") == "page"
                     and target.get("url") == self.opened_url]
            if len(hosts) != 1:
                raise ExportError("cannot identify the unique export browser context for downloads")
            params: Dict[str, Any] = {
                "behavior": "allow", "downloadPath": str(self.download_dir.resolve()),
                "eventsEnabled": True,
            }
            # agent-browser may use a non-default browser context. Applying the
            # setting only to Chromium's default context would silently miss it.
            if hosts[0].get("browserContextId"):
                params["browserContextId"] = hosts[0]["browserContextId"]
            call(2, "Browser.setDownloadBehavior", params)
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(f"could not configure isolated Chromium downloads: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def snapshot(self) -> Dict[str, Any]:
        process = self.run(["snapshot", "-i", "-C", "--json"])
        return json_result(process.stdout)

    def close(self) -> None:
        self.run(["close"], timeout=20, check=False)


def snapshot_data(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    data = snapshot.get("data")
    if not isinstance(data, dict):
        raise ExportError(f"invalid agent-browser snapshot: {snapshot}")
    return data


def ref_by_name(snapshot: Dict[str, Any], name: str, role: Optional[str] = None) -> str:
    refs = snapshot_data(snapshot).get("refs")
    if not isinstance(refs, dict):
        raise ExportError("snapshot contains no interactive refs")
    matches = []
    for ref, metadata in refs.items():
        if not isinstance(metadata, dict) or metadata.get("name") != name:
            continue
        if role is not None and str(metadata.get("role", "")).lower() != role.lower():
            continue
        matches.append(ref)
    if not matches:
        raise ExportError(f"could not find {role or 'element'} named {name!r}")
    return matches[-1]


def switch_state(snapshot: Dict[str, Any]) -> Optional[Tuple[str, bool, bool]]:
    text = str(snapshot_data(snapshot).get("snapshot") or "")
    match = re.search(r"switch \[(?P<attrs>[^\]]*?)ref=(?P<ref>e\d+)\]", text)
    if not match:
        return None
    attrs = match.group("attrs")
    return match.group("ref"), "checked=true" in attrs, "disabled" in attrs


def wait_for_export_dialog(browser: BrowserSession, timeout: float = 20.0) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        last = browser.snapshot()
        try:
            ref_by_name(last, "下载", "button")
            return last
        except ExportError:
            time.sleep(0.35)
    raise ExportError(f"export dialog did not become ready: {last}")


def is_pptx(path: Path) -> bool:
    if not path.is_file() or path.name.endswith(".crdownload"):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            checked_archive_members(archive)
            if "ppt/presentation.xml" not in archive.namelist():
                return False
            content_types = archive.read("[Content_Types].xml")
            return PPTX_CONTENT_TYPE.encode("utf-8") in content_types
    except (OSError, KeyError, zipfile.BadZipFile, ExportError):
        return False


def find_download(
    search_roots: Iterable[Path],
    timeout: float = 150.0,
    accept: Callable[[Path], bool] = is_pptx,
    *,
    since: Optional[float] = None,
) -> Path:
    deadline = time.monotonic() + timeout
    last_sizes: Dict[Path, int] = {}
    stable: Dict[Path, int] = {}
    while time.monotonic() < deadline:
        # Called only for isolated job directories by the exporters. Tolerate
        # Chrome renaming .crdownload files between listing and stat().
        entries: List[Tuple[Path, float, int]] = []
        visited = set()
        for root in search_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    info = path.stat()
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in visited:
                    continue
                visited.add(resolved)
                entries.append((path, info.st_mtime, info.st_size))
        candidates: List[Path] = []
        accepted: List[Path] = []
        for path, mtime, size in sorted(entries, key=lambda entry: entry[1], reverse=True):
            if since is not None and mtime < since:
                continue
            if size == last_sizes.get(path) and size > 0:
                stable[path] = stable.get(path, 0) + 1
            else:
                stable[path] = 0
            last_sizes[path] = size
            if accept(path):
                accepted.append(path)
                if stable[path] >= 1:
                    candidates.append(path)
        if len(accepted) > 1:
            raise ExportError(
                "ambiguous export downloads; expected exactly one artifact: "
                + ", ".join(path.name for path in accepted)
            )
        if len(candidates) == 1:
            return candidates[0]
        time.sleep(0.5)
    visible = "\n  ".join(str(path) for path in last_sizes) or "(none)"
    raise ExportError(f"timed out waiting for download; observed files:\n  {visible}")


def replace_transition(slide_xml: bytes, transition: str) -> bytes:
    text = slide_xml.decode("utf-8")
    pattern = re.compile(
        r"<p:transition\b[^>]*(?:/>|>.*?</p:transition>)", re.DOTALL
    )
    text = pattern.sub("", text)
    if transition == "none":
        return text.encode("utf-8")

    # CT_Slide requires transition as a direct child after cSld/clrMapOvr and
    # before timing/extLst. Searching for the first p:extLst is incorrect:
    # shapes may contain their own nested extLst inside cSld, causing Office to
    # ignore a transition inserted there.
    color_map = re.search(
        r"<p:clrMapOvr\b[^>]*(?:/>|>.*?</p:clrMapOvr>)", text, re.DOTALL
    )
    common_slide = re.search(
        r"<p:cSld\b[^>]*(?:/>|>.*?</p:cSld>)", text, re.DOTALL
    )
    anchor = color_map or common_slide
    if anchor is None:
        raise ExportError("slide XML has no cSld/clrMapOvr insertion anchor")
    position = anchor.end()
    return (text[:position] + FADE_TRANSITION_XML + text[position:]).encode("utf-8")


def root_child_names(slide_xml: bytes) -> List[str]:
    try:
        root = ET.fromstring(slide_xml)
    except ET.ParseError as exc:
        raise ExportError(f"invalid slide XML: {exc}") from exc
    return [child.tag.rsplit("}", 1)[-1] for child in root]


def has_direct_fade_transition(slide_xml: bytes) -> bool:
    try:
        root = ET.fromstring(slide_xml)
    except ET.ParseError as exc:
        raise ExportError(f"invalid slide XML: {exc}") from exc
    transition = next(
        (child for child in root if child.tag.rsplit("}", 1)[-1] == "transition"),
        None,
    )
    if transition is None:
        return False
    return any(child.tag.rsplit("}", 1)[-1] == "fade" for child in transition)


def validate_transition_order(slide_xml: bytes, transition: str) -> None:
    names = root_child_names(slide_xml)
    transition_indexes = [index for index, name in enumerate(names) if name == "transition"]
    if transition == "none":
        if transition_indexes:
            raise ExportError("transition=none left a root-level transition")
        return
    if len(transition_indexes) != 1 or not has_direct_fade_transition(slide_xml):
        raise ExportError("slide does not contain exactly one root-level fade transition")
    transition_index = transition_indexes[0]
    for required_before in ("cSld", "clrMapOvr"):
        if required_before in names and names.index(required_before) > transition_index:
            raise ExportError(f"{required_before} appears after transition")
    for required_after in ("timing", "extLst"):
        if required_after in names and names.index(required_after) < transition_index:
            raise ExportError(f"{required_after} appears before transition")


def patch_transitions(pptx: Path, transition: str) -> int:
    temporary = pptx.with_name(f".{pptx.name}.{uuid.uuid4().hex}.tmp")
    slide_count = 0
    try:
        with zipfile.ZipFile(pptx, "r") as source, zipfile.ZipFile(temporary, "w") as target:
            checked_archive_members(source)
            target.comment = source.comment
            for info in source.infolist():
                data = source.read(info.filename)
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", info.filename):
                    data = replace_transition(data, transition)
                    slide_count += 1
                target.writestr(info, data, compress_type=info.compress_type)
        if slide_count == 0:
            raise ExportError("exported PPTX contains no slide XML")
        temporary.replace(pptx)
    finally:
        temporary.unlink(missing_ok=True)
    return slide_count


def verify_output(
    pptx: Path, transition: str, expect_fonts: bool, expected_slides: Optional[int] = None,
) -> Dict[str, Any]:
    if not is_pptx(pptx):
        raise ExportError(f"output is not a valid PPTX ZIP: {pptx}")
    with zipfile.ZipFile(pptx) as archive:
        checked_archive_members(archive)
        broken = archive.testzip()
        if broken:
            raise ExportError(f"PPTX CRC check failed at: {broken}")
        slide_names = [
            name
            for name in archive.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        ]
        if not slide_names:
            raise ExportError("exported PPTX contains zero slides")
        if expected_slides is not None and len(slide_names) != expected_slides:
            raise ExportError(
                f"exported PPTX has {len(slide_names)} slides; source has {expected_slides}"
            )
        slide_xml = {name: archive.read(name) for name in slide_names}
        for data in slide_xml.values():
            validate_transition_order(data, transition)
        transition_hits = sum(has_direct_fade_transition(data) for data in slide_xml.values())
        if transition == "fade" and transition_hits != len(slide_names):
            raise ExportError("fade transition was not written to every slide")
        fonts = [
            name
            for name in archive.namelist()
            if name.startswith("ppt/fonts/") and not name.endswith("/")
        ]
        if expect_fonts and not fonts:
            log(
                "warning: embed-fonts was enabled, but the official writer produced no font part"
            )
        return {
            "slides": len(slide_names),
            "fadeTransitions": transition_hits,
            "fontParts": len(fonts),
            "bytes": pptx.stat().st_size,
        }


def serve(directory: Path) -> Tuple[ThreadingHTTPServer, threading.Thread, str]:
    handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
        *args, directory=str(directory), **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}/export_host.html"


def preflight_layout(manifest: Path) -> Dict[str, Any]:
    """Stop known hard layout failures before opening a remote writer.

    A needs_review result may be rendered to support inspection, but is not
    visual acceptance. The workflow separately requires source and final-file
    review evidence before completing a deliverable.
    """
    try:
        from layout_quality import audit_deck
        report = audit_deck(manifest)
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        raise ExportError(f"layout preflight could not run: {exc}. No export was started.") from exc
    if not isinstance(report, dict) or report.get("status") not in ("passed", "needs_review", "failed"):
        raise ExportError("layout preflight returned an invalid result; no export was started")
    hard_errors = [issue for issue in report.get("issues", []) if issue.get("severity") == "error"]
    if report["status"] == "failed" or hard_errors:
        details = "; ".join(str(issue.get("message", issue.get("code", "layout error"))) for issue in hard_errors[:5])
        raise ExportError("layout preflight failed; repair the source before PPTX export" + (": " + details if details else ""))
    return report


def export_pptx(
    source: Path,
    output: Path,
    transition: str,
    embed_fonts: bool,
    keep_download: bool = False,
    force: bool = False,
    *,
    allow_remote_renderer: bool = False,
) -> Dict[str, Any]:
    require_remote_renderer_consent(allow_remote_renderer)
    manifest = find_manifest(source)
    payload = build_payload(manifest)
    layout_report = preflight_layout(manifest)
    if output.expanduser().is_symlink():
        raise ExportError("output PPTX must not be a symlink")
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".pptx":
        raise ExportError("output path must have a .pptx extension")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        raise ExportError(f"output already exists (pass --force to replace it): {output}")
    agent_browser = ensure_agent_browser()
    ensure_websocket()

    log(f"manifest: {manifest}")
    log(
        f"defaults: transition={transition}, embed_fonts={'on' if embed_fonts else 'off'}"
    )

    with renderer_lock(), temporary_directory(prefix="open-kimi-ppt-export-") as temp_name:
        cdp_port = ensure_debug_chrome()
        temp_dir = Path(temp_name)
        download_dir = temp_dir / "downloads"
        download_dir.mkdir()
        shutil.copy2(HOST_TEMPLATE, temp_dir / HOST_TEMPLATE.name)
        (temp_dir / "payload.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        server, thread, url = serve(temp_dir)
        session = f"open-kimi-ppt-export-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        browser = BrowserSession(agent_browser, session, temp_dir, download_dir, cdp_port)
        handle, staged_name = tempfile.mkstemp(
            prefix=f".{output.stem}-", suffix=".pptx", dir=str(output.parent),
        )
        os.close(handle)
        staged_output = Path(staged_name)
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

            state = switch_state(dialog)
            if state is not None:
                switch_ref, checked, disabled = state
                if disabled and checked != embed_fonts:
                    log("warning: the official font switch is disabled for this deck")
                elif checked != embed_fonts:
                    browser.run(["click", f"@{switch_ref}"])
                    dialog = wait_for_export_dialog(browser)
            elif embed_fonts:
                log("warning: the official export dialog exposed no font switch")

            # CDP has already restricted this browser to the isolated job folder.
            started_at = time.time() - 1.0
            download_ref = ref_by_name(dialog, "下载", "button")
            log("generating PPTX in the browser")
            browser.run(["click", f"@{download_ref}"], timeout=180)
            downloaded = find_download(
                (download_dir,),
                timeout=90,
                since=started_at,
            )
            shutil.copy2(downloaded, staged_output)
            slide_count = patch_transitions(staged_output, transition)
            summary = verify_output(
                staged_output, transition, embed_fonts, expected_slides=len(payload["pages"]),
            )
            if keep_download:
                debug_copy = output.with_name(f"{output.stem}.browser-raw.pptx")
                if debug_copy.is_symlink():
                    raise ExportError("raw debug output must not be a symlink")
                if debug_copy.exists() and not force:
                    raise ExportError(
                        f"raw debug output already exists (pass --force): {debug_copy}"
                    )
                raw_staging = staged_output.with_name(f"{staged_output.name}.raw")
                try:
                    shutil.copy2(downloaded, raw_staging)
                    if force:
                        raw_staging.replace(debug_copy)
                    else:
                        os.link(raw_staging, debug_copy)
                finally:
                    raw_staging.unlink(missing_ok=True)
            if force:
                staged_output.replace(output)
            else:
                # A hard link publishes the already-validated file without
                # silently replacing an output another process just created.
                try:
                    os.link(staged_output, output)
                except FileExistsError as exc:
                    raise ExportError(f"output appeared during export: {output}") from exc
        finally:
            staged_output.unlink(missing_ok=True)
            try:
                browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    summary["transitionPatchedSlides"] = slide_count
    summary["output"] = str(output)
    summary["renderer"] = "kimi-public-iframe"
    summary["verification"] = "OOXML structure; visual and playback review are separate"
    summary["layoutPreflight"] = {
        "status": layout_report["status"],
        "reviewIssues": [issue for issue in layout_report.get("issues", []) if issue.get("severity") == "review"],
        "visualReview": "not_performed",
    }
    return summary


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export a PPTD project to PPTX using Kimi's public browser-side writer. "
            "Defaults: fade transition and embedded fonts."
        )
    )
    parser.add_argument("input", type=Path, help=".pptd manifest or project directory")
    parser.add_argument("--output", "-o", type=Path, help="output .pptx path")
    parser.add_argument(
        "--allow-remote-renderer", action="store_true",
        help="permit deck content/media to be exposed to Kimi's third-party iframe",
    )
    parser.add_argument(
        "--transition",
        choices=("fade", "none"),
        default="fade",
        help="slide transition written to every slide (default: fade)",
    )
    font_group = parser.add_mutually_exclusive_group()
    font_group.add_argument(
        "--embed-fonts",
        dest="embed_fonts",
        action="store_true",
        default=True,
        help="embed fonts when available (default)",
    )
    font_group.add_argument(
        "--no-embed-fonts",
        dest="embed_fonts",
        action="store_false",
        help="disable font embedding",
    )
    parser.add_argument(
        "--keep-browser-raw",
        action="store_true",
        help="also keep the unpatched browser download beside the output",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output file",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        manifest = find_manifest(args.input)
        output = args.output or manifest.with_suffix(".pptx")
        summary = export_pptx(
            args.input,
            output,
            args.transition,
            args.embed_fonts,
            args.keep_browser_raw,
            args.force,
            allow_remote_renderer=args.allow_remote_renderer,
        )
    except (ExportError, OSError, subprocess.SubprocessError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"open-kimi-ppt export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
