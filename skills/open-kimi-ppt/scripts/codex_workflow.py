#!/usr/bin/env python3
"""Offline presentation planning and durable state; the Codex host executes tasks.

No model API calls, credential reads, configuration writes or implicit installs.
The optional --probe-codex path only initialises app-server and lists models.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = SKILL_ROOT / "config/codex-profiles.json"
MAX_JSON_BYTES = 4 * 1024 * 1024
EVIDENCE = "https://learn.chatgpt.com/docs/app-server"
WORKFLOW_SCHEMA_VERSION = 2
TASK_IDS = ("story", "design", "assets", "pages", "integrate", "structure", "layout",
            "render", "visual_review", "export", "final_output_review")
TASK_OUTPUTS = {
    "story": ["run/storyboard.json", "run/sources.json"],
    "design": ["run/design-tokens.json", "run/layout-policy.json"],
    "assets": ["run/assets.json"], "integrate": ["deck.pptd"],
    "structure": ["run/structural-qa.json"], "layout": ["run/layout-qa.json"],
    "render": ["run/render-qa.json"], "visual_review": ["run/visual-qa.json"],
    "export": ["deck.pptx", "run/export-qa.json"],
    "final_output_review": ["run/final-output-qa.json"],
}


class WorkflowError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise WorkflowError(f"JSON file exceeds {MAX_JSON_BYTES} bytes: {path.name}")
    def reject_constant(value):
        raise WorkflowError(f"Non-finite JSON value is not supported: {value}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


def require_object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be a JSON object")
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def contained(root: Path, relative: str, *, exists: bool = False) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise WorkflowError("Use nonempty project-relative POSIX paths")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or ":" in relative or "\0" in relative:
        raise WorkflowError(f"Path is not project-relative: {relative!r}")
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise WorkflowError(f"Path escapes project: {relative!r}")
    if exists and not path.is_file():
        raise WorkflowError(f"Required file is missing: {relative}")
    return path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def checker_fingerprints() -> dict[str, str]:
    """A changed acceptance rule requires new evidence, even for unchanged sources."""
    scripts = Path(__file__).resolve().parent
    return {name: digest(scripts / name) for name in
            ("codex_workflow.py", "layout_quality.py", "asset_audit.py", "export_pptx.py")}


def version(command: str) -> dict:
    executable = shutil.which(command)
    if not executable:
        return {"available": False}
    try:
        result = subprocess.run([executable, "--version"], capture_output=True,
                                text=True, timeout=8, check=False)
        return {"available": result.returncode == 0,
                "version": result.stdout.strip()[:200], "executable": executable}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "reason": "version_probe_failed"}


def probe_models(executable: str, timeout: float = 20) -> list[dict]:
    """Read-only JSON-lines app-server handshake, bounded in time and memory."""
    proc = subprocess.Popen([executable, "app-server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8")
    messages: queue.Queue = queue.Queue(maxsize=10)
    deadline = time.monotonic() + timeout

    def reader():
        try:
            while True:
                line = proc.stdout.readline(MAX_JSON_BYTES + 1)
                if not line:
                    messages.put(None, timeout=1)
                    break
                if len(line) > MAX_JSON_BYTES:
                    messages.put({"probe_error": "Oversized app-server message"}, timeout=1)
                    break
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                messages.put(value, timeout=1)
        except (OSError, ValueError, queue.Full):
            return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def send(value: dict):
        proc.stdin.write(json.dumps(value) + "\n")
        proc.stdin.flush()

    def receive(request_id: int) -> dict:
        while time.monotonic() < deadline:
            try:
                item = messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if item is None:
                raise WorkflowError("Codex app-server exited before replying")
            if not isinstance(item, dict):
                continue
            if "probe_error" in item:
                raise WorkflowError(item["probe_error"])
            if item.get("id") == request_id:
                if "error" in item:
                    error = require_object(item["error"], "app-server error")
                    code = error.get("code", "unknown")
                    raise WorkflowError(f"Codex discovery was rejected (code {code}); no config changed")
                result = item.get("result")
                if not isinstance(result, dict):
                    raise WorkflowError("Invalid app-server result")
                return result
            # Never answer approval, attestation, credential, or config requests.
            if "method" in item and "id" in item:
                raise WorkflowError("Discovery needs an interactive host action; use its exported catalog")
        raise WorkflowError("Codex model discovery timed out; use an exported model/list result")

    try:
        send({"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "open_kimi_ppt_doctor", "title": "Open Kimi PPT doctor", "version": "1.4.0"}}})
        receive(0)
        send({"method": "initialized"})
        models, cursor = [], None
        cursors = set()
        for index in range(1, 21):
            params = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            send({"id": index, "method": "model/list", "params": params})
            result = receive(index)
            if not isinstance(result.get("data"), list):
                raise WorkflowError("model/list result lacks a data array")
            models.extend(result["data"])
            cursor = result.get("nextCursor")
            if not cursor:
                return normalise_models(models)
            if not isinstance(cursor, str):
                raise WorkflowError("Invalid model/list pagination cursor")
            if cursor in cursors:
                raise WorkflowError("Repeated model/list cursor")
            cursors.add(cursor)
        raise WorkflowError("Model catalog exceeds discovery page limit")
    finally:
        with contextlib.suppress(OSError, BrokenPipeError):
            proc.stdin.close()
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        thread.join(timeout=1)
        # A descendant can inherit stdout after app-server exits. Closing a
        # buffered stream while its reader owns the lock can block indefinitely.
        if not thread.is_alive():
            proc.stdout.close()


def normalise_models(value: Any) -> list[dict]:
    if isinstance(value, dict):
        value = value.get("result", value)
        require_object(value, "model/list result")
        value = value.get("data", value.get("models", []))
    if not isinstance(value, list):
        raise WorkflowError("Expected model/list data array")
    result, seen = [], set()
    for model in value:
        if not isinstance(model, dict) or model.get("hidden", False):
            continue
        model_id = model.get("model") or model.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise WorkflowError("Catalog entry needs an exact model id")
        if model_id in seen:
            raise WorkflowError(f"Duplicate model id: {model_id}")
        seen.add(model_id)
        efforts = model.get("supportedReasoningEfforts", [])
        if not isinstance(efforts, list):
            raise WorkflowError("supportedReasoningEfforts must be an array")
        normalized = []
        for effort in efforts:
            effort = effort.get("reasoningEffort") if isinstance(effort, dict) else effort
            if not isinstance(effort, str) or not effort:
                raise WorkflowError("Invalid reasoning effort")
            normalized.append(effort)
        # Diagnostic fields remain unobserved when absent; no capability inference.
        modalities = model.get("inputModalities", [])
        if not isinstance(modalities, list) or any(not isinstance(m, str) for m in modalities):
            raise WorkflowError("Invalid inputModalities")
        result.append({**model, "id": model_id, "model": model_id,
                       "supportedReasoningEfforts": normalized,
                       "inputModalities": modalities})
    return result


def session_metadata(capabilities: dict) -> dict:
    """Retain observations exactly; they are never model-selection instructions."""
    result = {}
    for field in ("current_model", "current_effort"):
        value = capabilities.get(field)
        if value is not None and not isinstance(value, str):
            raise WorkflowError(f"{field} metadata must be a string or null")
        result[field] = value
        result[field + "_observed"] = field in capabilities
    return result


def doctor(args) -> dict:
    policy = read_json(PROFILE_PATH)
    supplied = require_object(read_json(Path(args.capabilities)), "Capabilities") if args.capabilities else {}
    report = {
        "schema_version": WORKFLOW_SCHEMA_VERSION, "observed_at": now(),
        "target_desktop": policy["target_desktop"], **session_metadata(supplied),
        "tools": {name: version(name) for name in ("node", "npm", "codex", "agent-browser", "soffice")},
        "python": {"version": sys.version.split()[0], "modules": {
            module: importlib.util.find_spec(module) is not None for module in ("yaml", "PIL", "websocket", "fitz")}},
        "models": normalise_models(supplied.get("models", [])),
        "model_catalog_evidence": "supplied_snapshot" if supplied.get("models") else "unobserved",
        "model_catalog_required": False,
        "execution": {"mode": "single_model", "max_parallel_workers": 1,
                      "switches_models": False, "changes_reasoning_effort": False, "spawns_agents": False},
        "renderer": {"engine": "kimi-browser", "live_verified": False,
                     "requires_remote_permission": True,
                     "origins": ["https://www.kimi.com", "https://statics.moonshot.cn"]},
        "warnings": [], "source": EVIDENCE,
    }
    if args.model_catalog:
        report["models"] = normalise_models(read_json(Path(args.model_catalog)))
        report["model_catalog_evidence"] = "supplied_model_list"
    if args.probe_codex:
        codex = report["tools"]["codex"]
        if codex["available"]:
            try:
                report["models"] = probe_models(codex["executable"])
                report["model_catalog_evidence"] = "live_cli_app_server_model_list"
            except (WorkflowError, OSError) as exc:
                report["warnings"].append(str(exc))
        else:
            report["warnings"].append("Codex CLI is unavailable; desktop build is not inferred from CLI")
    return report


def make_plan(brief: dict, capabilities: dict | None = None) -> dict:
    require_object(brief, "Brief")
    capabilities = {} if capabilities is None else require_object(capabilities, "Capabilities")
    policy = require_object(read_json(PROFILE_PATH), "Workflow policy")
    metadata = session_metadata(capabilities)
    for field in ("title", "audience", "goal"):
        if not isinstance(brief.get(field), str) or not brief[field].strip():
            raise WorkflowError(f"Brief needs nonempty {field}")
    count = brief.get("slide_count", 8)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 200:
        raise WorkflowError("slide_count must be an integer from 1 to 200")
    tasks = []

    def task(name, logical_pass, outputs, instructions):
        tasks.append({"id": name, "logical_pass": logical_pass,
                      "depends_on": [tasks[-1]["id"]] if tasks else [],
                      "outputs": outputs, "owner": "current_host", "instructions": instructions})

    task("story", "director", ["run/storyboard.json", "run/sources.json"],
         "Use the current model and current reasoning setting. Give each slide one conclusion, preserve the requested scope and register sources for factual claims. Label assumptions; never invent numbers.")
    task("design", "director", ["run/design-tokens.json", "run/layout-policy.json"],
         "Choose one grid, palette, verified fonts and readable type hierarchy. Use deterministic supported templates and explicit layout policy; measure text before placing it. Remove redundant labels, not substantive content.")
    task("assets", "builder", ["run/assets.json"],
         "Reuse relevant supplied or permitted existing images and code-native shapes. Record asset provenance and hashes with asset_audit.py. Keep precise text, data and labels editable. No image-generation model or service is required.")
    task("pages", "builder", [f"pages/{page:02d}.page" for page in range(1, count + 1)],
         "Build all pages sequentially in this host using the storyboard, shared tokens and measured templates. Reflow or split overloaded content deliberately; preserve the agreed slide count or revise the brief before replanning. Do not shrink text below policy limits.")
    task("integrate", "director", ["deck.pptd"],
         "Assemble the exact planned slide order and check narrative, source accuracy, type hierarchy and cross-page consistency.")
    task("structure", "builder", ["run/structural-qa.json"],
         "Run codex_workflow.py validate and fix every structural failure. Structural checks are distinct from appearance and playback.")
    task("layout", "builder", ["run/layout-qa.json"],
         "Run layout_quality.py audit/check on the current deck and policy. Fix all hard geometry, font, text-fit and contrast failures. A needs_review result may proceed to rendering only with every review finding retained for individual visual resolution.")
    task("render", "builder", ["run/render-qa.json"],
         "Render every source page with an available permitted renderer. Record render_source=source_pptd and actual ordered image paths/hashes. Missing rendering capability blocks the task; model or effort changes are not prerequisites.")
    task("visual_review", "reviewer", ["run/visual-qa.json"],
         "In a separate review pass in this same host, inspect the contact sheet and every page at readable size. Check typography, overlap, spacing, images, charts and source labels. Reference the current layout report hash and resolve every review issue individually with page image evidence. Fix defects and rerun dependent gates; unresolved failures block export at every reasoning effort.")
    task("export", "builder", ["deck.pptx", "run/export-qa.json"],
         "Export through the permitted backend and verify the actual PPTX, exact slide count and transitions. Export completion is not final visual approval.")
    task("final_output_review", "reviewer", ["run/final-output-qa.json"],
         "Render the exported deck.pptx in an actual presentation renderer, then inspect every final page. Bind source_pptx, renderer and original final-render report to fresh image hashes. Record operator-attested page passes. Keep application playback separate; source preview images cannot substitute for final PPTX rendering. Do not claim completion while any required evidence or defect remains unresolved.")
    return {"schema_version": WORKFLOW_SCHEMA_VERSION, "created_at": now(), "slide_count": count,
            **metadata, "execution": "single_model", "executes_models": False,
            "switches_models": False, "changes_reasoning_effort": False, "spawns_agents": False,
            "max_parallel_workers": 1, "max_renderer_jobs": 1,
            "quality_policy": policy["quality_policy"], "tasks": tasks,
            "boundaries": ["Director, builder and reviewer are sequential passes of the current host, not model assignments.",
                           "Unknown, absent or future model/effort metadata does not change the mandatory gates.",
                           "Unavailable rendering or visual inspection must be reported as blocked; no gate can be skipped.",
                           "Permissions and approvals remain controlled by the host."]}

def initialise(project: Path, brief: dict, capabilities: dict, plan: dict) -> dict:
    project = project.resolve()
    if project.exists() and any(project.iterdir()):
        raise WorkflowError("Initialise in a new or empty project directory; existing files were not changed")
    project.mkdir(parents=True, exist_ok=True)
    (project / "pages").mkdir(exist_ok=True)
    (project / "media").mkdir(exist_ok=True)
    atomic_json(project / "run/brief.json", brief)
    atomic_json(project / "run/capabilities.json", capabilities)
    atomic_json(project / "run/plan.json", plan)
    state = {"schema_version": WORKFLOW_SCHEMA_VERSION, "revision": 1, "updated_at": now(),
             "inputs": {p: digest(project / p) for p in ("run/brief.json", "run/capabilities.json", "run/plan.json")},
             "tasks": {t["id"]: {"status": "pending", "artifacts": {}} for t in plan["tasks"]}}
    atomic_json(project / "run/state.json", state)
    return {"project": str(project), "tasks": len(plan["tasks"]), "state": "run/state.json"}


@contextlib.contextmanager
def state_lock(project: Path):
    lock = contained(project, "run/state.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise WorkflowError("Another state update is active; retry after it finishes. Inspect stale lock ownership before removal.")
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def current_state(project: Path) -> tuple[dict, dict]:
    plan = require_object(read_json(contained(project, "run/plan.json", exists=True)), "Plan")
    state = require_object(read_json(contained(project, "run/state.json", exists=True)), "State")
    if plan.get("schema_version") != WORKFLOW_SCHEMA_VERSION or state.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise WorkflowError("This workflow requires schema_version 2. Preserve the old project and initialise a new run; version 1 evidence cannot satisfy the new quality gates.")
    require_object(state.get("inputs"), "State inputs")
    require_object(state.get("tasks"), "State tasks")
    if not isinstance(plan.get("tasks"), list) or not plan["tasks"]:
        raise WorkflowError("Plan needs a nonempty tasks array")
    expected_ids = [require_object(task, "Plan task").get("id") for task in plan["tasks"]]
    if any(not isinstance(task_id, str) for task_id in expected_ids) or len(set(expected_ids)) != len(expected_ids):
        raise WorkflowError("Plan task ids must be unique strings")
    if tuple(expected_ids) != TASK_IDS:
        raise WorkflowError("Plan must include every mandatory single-model quality gate in order")
    if plan.get("execution") != "single_model" or plan.get("max_parallel_workers") != 1:
        raise WorkflowError("Plan must preserve current-host single-model execution")
    for index, task in enumerate(plan["tasks"]):
        if task.get("depends_on") != ([expected_ids[index - 1]] if index else []):
            raise WorkflowError("Plan cannot remove or reorder mandatory quality dependencies")
        expected_outputs = ([f"pages/{page:02d}.page" for page in range(1, plan["slide_count"] + 1)]
                            if task["id"] == "pages" else TASK_OUTPUTS[task["id"]])
        if task.get("outputs") != expected_outputs:
            raise WorkflowError("Plan cannot remove or replace mandatory quality evidence outputs")
    if set(expected_ids) != set(state["tasks"]):
        raise WorkflowError("State tasks do not match the plan")
    changed_inputs = [rel for rel, sha in state["inputs"].items()
                      if not contained(project, rel).is_file() or digest(contained(project, rel)) != sha]
    if changed_inputs:
        raise WorkflowError("Planning inputs changed: " + ", ".join(changed_inputs) + ". Create a revised plan in a new run; keep previous artifacts.")
    # Content hashes, not wall-clock timestamps, invalidate completed work.
    for task in plan["tasks"]:
        entry = require_object(state["tasks"][task["id"]], "State task entry")
        require_object(entry.get("artifacts"), "Task artifacts")
        if entry.get("status") not in ("pending", "running", "done", "blocked", "stale"):
            raise WorkflowError("State task status is invalid")
        if entry["status"] == "done":
            changed = [rel for rel, sha in entry["artifacts"].items()
                       if not contained(project, rel).is_file() or digest(contained(project, rel)) != sha]
            changed.extend(rel for rel in task["outputs"] if rel not in entry["artifacts"])
            if task["id"] == "layout":
                if entry.get("verification", {}).get("checker_sha256") != checker_fingerprints():
                    changed.append("quality checker implementation")
                for font in entry.get("verification", {}).get("fonts", []):
                    if font.get("verified") and font.get("path") and font.get("sha256"):
                        font_path = Path(font["path"])
                        if not font_path.is_file() or digest(font_path) != font["sha256"]:
                            changed.append("font:" + str(font_path))
            parents = [dep for dep in task["depends_on"] if state["tasks"][dep]["status"] != "done"]
            if changed or parents:
                entry["status"] = "stale"
                entry["reason"] = "Changed output/dependency: " + ", ".join(changed + parents)
    return plan, state


def evidence_files(project: Path, files: Any, label: str) -> dict[str, str]:
    if not isinstance(files, list):
        raise WorkflowError(f"{label} files must be an array")
    result = {}
    for observed in files:
        require_object(observed, f"{label} file")
        rel, sha = observed.get("path"), observed.get("sha256")
        path = contained(project, rel, exists=True)
        if rel in result:
            raise WorkflowError(f"Duplicate {label} file: {rel}")
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha) or digest(path) != sha:
            raise WorkflowError(f"{label} evidence changed or has an invalid hash: {rel}")
        if path.stat().st_size == 0:
            raise WorkflowError(f"Empty {label} evidence: {rel}")
        result[rel] = sha
    return result


def require_current_evidence(files: dict[str, str], expected: list[dict], label: str) -> None:
    if any(files.get(item["path"]) != item["sha256"] for item in expected):
        raise WorkflowError(f"{label} must contain fresh hashes for every required evidence file")


def verify_layout(project: Path, qa: dict, count: int, files: dict[str, str]) -> dict:
    from layout_quality import audit_deck
    fresh = audit_deck(contained(project, "deck.pptd", exists=True))
    if fresh["status"] == "failed" or fresh["slide_count"] != count:
        raise WorkflowError("Fresh layout validation failed or slide count differs from the plan; hard failures cannot be overridden")
    if qa.get("status") != fresh["status"] or qa.get("issues") != fresh["issues"] or qa.get("fonts") != fresh["fonts"]:
        raise WorkflowError("Layout QA must retain the current audit status, every finding and actual font fingerprints")
    require_current_evidence(files, fresh["files"], "Layout QA")
    issues = fresh["issues"]
    if any(issue.get("severity") != "review" for issue in issues):
        raise WorkflowError("Only explicit review findings may proceed to visual inspection")
    return {"kind": "deterministic_layout_audit", "status": fresh["status"], "slide_count": count,
            "unresolved_layout_issues": [issue["id"] for issue in issues], "fonts": fresh["fonts"],
            "checker_sha256": checker_fingerprints(),
            "visual_review": "not_performed", "playback_review": "not_performed"}


def verify_page_images(project: Path, qa: dict, count: int, files: dict[str, str], *, inspect: bool) -> list[dict]:
    from asset_audit import audit_asset
    import yaml
    deck = yaml.safe_load(contained(project, "deck.pptd", exists=True).read_text(encoding="utf-8"))
    width, height = deck.get("size", [960, 540])
    pages = qa.get("pages")
    if not isinstance(pages, list) or len(pages) != count:
        raise WorkflowError("Rendered/visual QA needs actual image files and complete ordered page coverage")
    paths, canonical_paths = set(), set()
    for number, page in enumerate(pages, start=1):
        require_object(page, "QA page")
        if type(page.get("page")) is not int or page["page"] != number:
            raise WorkflowError("QA pages must list every page once in source order, starting at 1")
        rel = page.get("path")
        if not isinstance(rel, str) or rel not in files or rel in paths:
            raise WorkflowError("Every QA page needs a distinct image path present in files with its current hash")
        canonical = contained(project, rel, exists=True)
        if canonical in canonical_paths:
            raise WorkflowError("QA images must refer to distinct files, not duplicate paths or symlinks")
        paths.add(rel)
        canonical_paths.add(canonical)
        image = audit_asset(rel, project)
        if image["format"] not in ("png", "jpeg", "webp") or image["frames"] != 1:
            raise WorkflowError("Page QA evidence must be a static PNG, JPEG or WebP render")
        if image["width"] < width or image["height"] < height:
            raise WorkflowError("Page QA evidence must have at least the source canvas resolution for readable inspection")
        if abs(image["width"] / image["height"] - width / height) > 0.03:
            raise WorkflowError("Page QA image aspect ratio must match the source canvas")
        if "sha256" in page and page["sha256"] != files[rel]:
            raise WorkflowError("QA page hash disagrees with its evidence file")
        if inspect and page.get("status") != "passed":
            raise WorkflowError("Each visual QA page must explicitly pass operator inspection")
    if inspect and qa.get("review_method") != "operator_attested":
        raise WorkflowError("Visual QA must declare review_method=operator_attested; byte checks do not assess appearance")
    if inspect and qa.get("visual_review", "operator_attested") != "operator_attested":
        raise WorkflowError("A visual QA report must not retain a not_performed visual_review claim")
    return pages


def verify_layout_resolutions(project: Path, qa: dict, files: dict[str, str], pages: list[dict]) -> list[str]:
    review = require_object(qa.get("layout_review"), "Visual QA layout_review")
    if review.get("report_path") != "run/layout-qa.json":
        raise WorkflowError("Visual QA must reference run/layout-qa.json")
    report_path = contained(project, review["report_path"], exists=True)
    current_hash = digest(report_path)
    if review.get("sha256") != current_hash or files.get(review["report_path"]) != current_hash:
        raise WorkflowError("Visual QA must contain the latest layout report hash in layout_review and files")
    layout = require_object(read_json(report_path), "Layout QA")
    if layout.get("status") not in ("passed", "needs_review"):
        raise WorkflowError("Failed layout findings cannot be overridden by visual review")
    issues = layout.get("issues")
    if not isinstance(issues, list) or any(not isinstance(item, dict) or item.get("severity") != "review" for item in issues):
        raise WorkflowError("Layout review must retain all review findings and no hard failures")
    expected = {item["id"]: item for item in issues}
    if len(expected) != len(issues):
        raise WorkflowError("Layout issue identifiers must be unique")
    resolutions = review.get("resolutions")
    if not isinstance(resolutions, list):
        raise WorkflowError("Visual QA needs an explicit resolutions array, including when no layout findings remain")
    resolved = set()
    page_paths = {page["page"]: page["path"] for page in pages}
    for resolution in resolutions:
        require_object(resolution, "Layout resolution")
        issue_id = resolution.get("issue_id")
        if not isinstance(issue_id, str) or issue_id not in expected or issue_id in resolved:
            raise WorkflowError("Each layout resolution must identify one distinct current review issue")
        issue = expected[issue_id]
        number = resolution.get("page")
        if type(number) is not int or number != issue.get("page"):
            raise WorkflowError("Each layout resolution must reference its affected page and actual reviewed image")
        if number == 0:
            expected_pages = [{"page": p, "path": path} for p, path in page_paths.items()]
            if resolution.get("pages") != expected_pages:
                raise WorkflowError("A deck-wide layout resolution must reference every actual reviewed page image")
        elif number not in page_paths or resolution.get("path") != page_paths[number]:
            raise WorkflowError("Each layout resolution must reference its affected page and actual reviewed image")
        if resolution.get("status") != "resolved" or not isinstance(resolution.get("explanation"), str) or not resolution["explanation"].strip():
            raise WorkflowError("Each layout finding needs an individual resolved status and concrete inspection explanation")
        resolved.add(issue_id)
    if resolved != set(expected):
        missing = sorted(set(expected) - resolved)
        raise WorkflowError("Unresolved layout issues: " + ", ".join(missing))
    return sorted(resolved)


def verify_final_review(project: Path, qa: dict, plan: dict, files: dict[str, str], pages: list[dict]) -> dict:
    if qa.get("render_source") != "final_pptx":
        raise WorkflowError("Final output review requires render_source=final_pptx")
    source = require_object(qa.get("source_pptx"), "Final source_pptx")
    pptx_sha = digest(contained(project, "deck.pptx", exists=True))
    if source.get("path") != "deck.pptx" or source.get("sha256") != pptx_sha or files.get("deck.pptx") != pptx_sha:
        raise WorkflowError("Final output review must be bound to the current exported deck.pptx hash")
    renderer = require_object(qa.get("renderer"), "Final renderer")
    if not isinstance(renderer.get("name"), str) or not renderer["name"].strip():
        raise WorkflowError("Final output review must name the actual PPTX renderer")
    ref = require_object(qa.get("render_report"), "Final render_report")
    rel = ref.get("path")
    if rel in ("run/render-qa.json", "run/visual-qa.json", "run/final-output-qa.json"):
        raise WorkflowError("Final review needs a distinct original PPTX render report")
    original_path = contained(project, rel, exists=True)
    if ref.get("sha256") != digest(original_path) or files.get(rel) != ref["sha256"]:
        raise WorkflowError("Final output review must include the original render report hash")
    original = require_object(read_json(original_path), "Original final render report")
    if original.get("status") != "passed" or original.get("kind") != "final_pptx_render" or original.get("render_source") != "final_pptx" or original.get("source_pptx") != source or original.get("renderer") != renderer or original.get("slide_count") != plan["slide_count"]:
        raise WorkflowError("Original render report must prove current PPTX provenance and match renderer, source hash and slide count")
    original_files = evidence_files(project, original.get("files", []), "Final render")
    require_current_evidence(files, original.get("files", []), "Final output review")
    if original_files.get("deck.pptx") != pptx_sha:
        raise WorkflowError("Original render report is not bound to the current PPTX")
    original_pages = verify_page_images(project, original, plan["slide_count"], original_files, inspect=False)
    if [item["path"] for item in original_pages] != [item["path"] for item in pages]:
        raise WorkflowError("Final review must inspect the actual final renderer's ordered page images")
    source_render = require_object(read_json(contained(project, "run/render-qa.json", exists=True)), "Source render QA")
    source_paths = {contained(project, item["path"], exists=True) for item in source_render["pages"]}
    if any(contained(project, item["path"], exists=True) in source_paths for item in pages):
        raise WorkflowError("Source-render image paths cannot be reused as final PPTX render evidence")
    playback = qa.get("playback_review", "not_performed")
    if playback != "not_performed":
        require_object(playback, "Application playback attestation")
        if playback.get("status") != "passed" or playback.get("review_method") != "operator_attested" or playback.get("source_pptx_sha256") != pptx_sha or not isinstance(playback.get("application"), str) or not playback["application"].strip() or not isinstance(playback.get("notes"), str) or not playback["notes"].strip():
            raise WorkflowError("Playback passes require a separate current-PPTX operator attestation naming the application and inspected behavior")
    return {"kind": "operator_attested_final_pptx_review", "slide_count": plan["slide_count"],
            "render_source": "final_pptx", "source_pptx": source, "renderer": renderer,
            "render_report": ref, "visual_judgement": "operator_attested",
            "provenance": "renderer_report_and_hashes_checked; rendering origin is operator_attested",
            "unresolved_layout_issues": [], "playback_review": playback}


def verify_task_qa(project: Path, task_id: str, qa: dict, plan: dict,
                   files: dict[str, str]) -> dict:
    count = plan["slide_count"]
    if type(qa.get("slide_count")) is not int or qa["slide_count"] != count:
        raise WorkflowError(f"{task_id} QA must report the planned slide_count ({count})")
    if task_id == "structure":
        fresh = validate_deck(contained(project, "deck.pptd", exists=True))
        if fresh["status"] != "passed" or fresh["slide_count"] != count:
            raise WorkflowError("Fresh structural validation failed or slide count differs from the plan")
        require_current_evidence(files, fresh["files"], "Structural QA")
        return {"kind": "structural_subset", "slide_count": count,
                "visual_review": "not_performed", "playback_review": "not_performed"}
    if task_id == "layout":
        return verify_layout(project, qa, count, files)
    if task_id in ("render", "visual_review", "final_output_review"):
        pages = verify_page_images(project, qa, count, files, inspect=task_id != "render")
        if task_id == "final_output_review":
            return verify_final_review(project, qa, plan, files, pages)
        if qa.get("render_source") != "source_pptd":
            raise WorkflowError("Source render and visual QA must declare render_source=source_pptd")
        if task_id == "visual_review":
            rendered = require_object(read_json(contained(project, "run/render-qa.json", exists=True)), "Source render QA")
            rendered_files = evidence_files(project, rendered.get("files", []), "Source render")
            if [item["path"] for item in rendered["pages"]] != [item["path"] for item in pages]:
                raise WorkflowError("Source visual review must inspect the source renderer's actual ordered page images")
            require_current_evidence(files, [{"path": item["path"], "sha256": rendered_files[item["path"]]} for item in pages], "Source visual review")
            resolved = verify_layout_resolutions(project, qa, files, pages)
            return {"kind": "operator_attested_visual_review", "slide_count": count,
                    "render_source": "source_pptd", "resolved_layout_issues": resolved,
                    "unresolved_layout_issues": [], "playback_review": "not_performed"}
        return {"kind": "rendered_image_structure", "slide_count": count,
                "render_source": "source_pptd", "visual_review": "not_performed", "playback_review": "not_performed"}
    if task_id == "export":
        from export_pptx import ExportError, verify_output
        pptx = contained(project, "deck.pptx", exists=True)
        if files.get("deck.pptx") != digest(pptx):
            raise WorkflowError("Export QA must include the current deck.pptx hash")
        transition = qa.get("transition", "fade")
        if transition not in ("fade", "none"):
            raise WorkflowError("Export QA transition must be fade or none")
        try:
            stats = verify_output(pptx, transition, expect_fonts=False, expected_slides=count)
        except ExportError as exc:
            raise WorkflowError(f"Fresh PPTX verification failed: {exc}") from exc
        if "font_parts" in qa and qa["font_parts"] != stats["fontParts"]:
            raise WorkflowError("Export QA font_parts disagrees with the actual PPTX")
        return {"kind": "pptx_structure", **stats, "transition": transition,
                "visual_review": "final_output_review_pending", "playback_review": "not_performed"}
    raise WorkflowError("No QA verifier is defined for this task")

def record(project: Path, task_id: str, status: str, note: str = "", evidence: list[str] | None = None) -> dict:
    project = project.resolve()
    with state_lock(project):
        plan, state = current_state(project)
        task = next((t for t in plan["tasks"] if t["id"] == task_id), None)
        if task is None:
            raise WorkflowError(f"Unknown task: {task_id}")
        if status not in ("running", "done", "blocked", "pending"):
            raise WorkflowError(f"Invalid task status: {status}")
        if status in ("running", "done"):
            missing = [d for d in task["depends_on"] if state["tasks"][d]["status"] != "done"]
            if missing:
                raise WorkflowError("Unfinished or stale dependencies: " + ", ".join(missing))
        if status == "blocked" and not note.strip():
            raise WorkflowError("Blocked tasks require a concrete reason")
        artifacts = {}
        verification = None
        if status == "done":
            for rel in list(dict.fromkeys(task["outputs"] + (evidence or []))):
                path = contained(project, rel, exists=True)
                if path.stat().st_size == 0:
                    raise WorkflowError(f"Empty artifact: {rel}")
                artifacts[rel] = digest(path)
            if task_id == "assets":
                asset_index = require_object(read_json(contained(project, "run/assets.json", exists=True)), "Asset registry")
                artifacts.update(evidence_files(project, asset_index.get("files", []), "Asset"))
            for rel in task["outputs"]:
                if rel.endswith("-qa.json"):
                    qa = require_object(read_json(contained(project, rel, exists=True)), "QA report")
                    allowed = ("passed", "needs_review") if task_id == "layout" else ("passed",)
                    if qa.get("status") not in allowed:
                        raise WorkflowError(f"QA report has not passed: {rel}")
                    files = evidence_files(project, qa.get("files", []), "QA")
                    verification = verify_task_qa(project, task_id, qa, plan, files)
                    artifacts.update(files)
        state["tasks"][task_id] = {"status": status, "artifacts": artifacts,
                                  "updated_at": now(), "note": note}
        if verification:
            state["tasks"][task_id]["verification"] = verification
        # Mark downstream jobs stale immediately when a task is redone.
        affected = {task_id}
        for downstream in plan["tasks"]:
            if downstream["id"] != task_id and affected.intersection(downstream["depends_on"]):
                affected.add(downstream["id"])
                entry = state["tasks"][downstream["id"]]
                if entry["status"] == "done":
                    entry["status"] = "stale"
                    entry["reason"] = f"Upstream task {task_id} updated"
        state["revision"] += 1
        state["updated_at"] = now()
        atomic_json(project / "run/state.json", state)
        result = {"task": task_id, "status": status, "revision": state["revision"]}
        if verification:
            result["verification"] = verification
        return result


def status_report(project: Path) -> dict:
    plan, state = current_state(project.resolve())
    ready = [t["id"] for t in plan["tasks"] if state["tasks"][t["id"]]["status"] in ("pending", "stale")
             and all(state["tasks"][d]["status"] == "done" for d in t["depends_on"])]
    layout = state["tasks"]["layout"]
    visual = state["tasks"]["visual_review"]
    final = state["tasks"]["final_output_review"]
    unresolved = layout.get("verification", {}).get("unresolved_layout_issues", [])
    if visual["status"] == "done":
        unresolved = visual.get("verification", {}).get("unresolved_layout_issues", unresolved)
    return {"revision": state["revision"], "tasks": state["tasks"], "ready": ready,
            "execution": "single_model", "max_parallel_workers": 1,
            "layout_audit_status": layout.get("verification", {}).get("status", "not_run") if layout["status"] == "done" else layout["status"],
            "unresolved_layout_issues": unresolved,
            "layout_issue_resolution": "operator_attested" if visual["status"] == "done" else "pending",
            "final_pptx_review_status": "operator_attested" if final["status"] == "done" else final["status"],
            "playback_review": final.get("verification", {}).get("playback_review", "not_performed") if final["status"] == "done" else "not_performed",
            "complete": not unresolved and all(t["status"] == "done" for t in state["tasks"].values())}


def validate_deck(manifest: Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise WorkflowError("Structural validation requires PyYAML; install the declared requirements explicitly")
    from export_pptx import ExportError, build_payload, find_manifest
    try:
        manifest = find_manifest(manifest.resolve())
        payload = build_payload(manifest)  # Parser also enforces resource containment.
    except ExportError as exc:
        raise WorkflowError(str(exc)) from exc
    root = manifest.parent
    deck = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    size = deck.get("size", [960, 540])
    if not isinstance(size, list) or len(size) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in size):
        raise WorkflowError("Deck size must contain two positive finite numbers")
    pages = deck.get("pages", [])
    if not pages or len(set(pages)) != len(pages):
        raise WorkflowError("Deck pages must be nonempty and unique")
    errors, warnings = [], []
    files = [{"path": manifest.name, "sha256": digest(manifest)}]
    files.extend({"path": rel, "sha256": digest(contained(root, rel, exists=True))}
                 for rel in sorted(payload["imageMap"]))
    for rel in pages:
        page_path = contained(root, rel, exists=True)
        files.append({"path": rel, "sha256": digest(page_path)})
        page = yaml.safe_load(page_path.read_text(encoding="utf-8"))
        if not isinstance(page, dict):
            errors.append(f"{rel}: expected a page mapping")
            continue
        elements = page.get("elements", [])
        if not isinstance(elements, list):
            errors.append(f"{rel}: elements must be an array")
            continue
        ids = set()
        for element in elements:
            if not isinstance(element, dict):
                errors.append(f"{rel}: element must be a mapping")
                continue
            eid = element.get("elementId")
            if not isinstance(eid, str) or not eid:
                errors.append(f"{rel}: missing/invalid elementId")
                continue
            if eid in ids:
                errors.append(f"{rel}: missing/duplicate elementId {eid!r}")
            ids.add(eid)
            bounds = element.get("bounds")
            if not isinstance(bounds, list) or len(bounds) != 4 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in bounds):
                errors.append(f"{rel}/{eid}: bounds must contain four finite numbers")
                continue
            x, y, w, h = bounds
            if w < 0 or h < 0:
                errors.append(f"{rel}/{eid}: negative dimensions")
            if x < 0 or y < 0 or x + w > size[0] + 0.01 or y + h > size[1] + 0.01:
                warnings.append(f"{rel}/{eid}: extends beyond slide; confirm intentional clipping visually")
    return {"schema_version": 1, "checked_at": now(), "status": "failed" if errors else "passed",
            "kind": "structural_subset", "slide_count": len(pages), "files": files,
            "errors": errors, "warnings": warnings,
            "visual_review": "not_performed", "playback_review": "not_performed",
            "limitations": ["Not a complete PPTD schema validator", "Cannot prove text fit, font embedding or visual fidelity"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="Non-mutating dependency/capability report")
    d.add_argument("--capabilities")
    d.add_argument("--model-catalog")
    d.add_argument("--probe-codex", action="store_true")
    d.add_argument("--output")
    for name in ("plan", "init"):
        p = sub.add_parser(name)
        p.add_argument("--brief", required=True)
        p.add_argument("--capabilities", help="Optional observations; never selects a model or reasoning effort")
        p.add_argument("--output", required=name == "plan")
        if name == "init":
            p.add_argument("--project", required=True)
    r = sub.add_parser("record")
    r.add_argument("project")
    r.add_argument("task")
    r.add_argument("status", choices=("running", "done", "blocked", "pending"))
    r.add_argument("--note", default="")
    r.add_argument("--evidence", action="append", default=[])
    s = sub.add_parser("status")
    s.add_argument("project")
    v = sub.add_parser("validate")
    v.add_argument("manifest")
    v.add_argument("--output")
    layout = sub.add_parser("layout", help="Run mandatory measured layout checks")
    layout.add_argument("manifest")
    layout.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args)
        elif args.command in ("plan", "init"):
            brief = read_json(Path(args.brief))
            capabilities = read_json(Path(args.capabilities)) if args.capabilities else {}
            result = make_plan(brief, capabilities)
            if args.command == "init":
                result = initialise(Path(args.project), brief, capabilities, result)
        elif args.command == "record":
            result = record(Path(args.project), args.task, args.status, args.note, args.evidence)
        elif args.command == "status":
            result = status_report(Path(args.project))
        elif args.command == "validate":
            result = validate_deck(Path(args.manifest))
        else:
            from layout_quality import audit_deck
            result = audit_deck(Path(args.manifest))
        if getattr(args, "output", None):
            atomic_json(Path(args.output), result)
            print(json.dumps({"output": str(Path(args.output).resolve()), "status": result.get("status", "written")}, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("status") in ("failed", "needs_review") else 0
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
