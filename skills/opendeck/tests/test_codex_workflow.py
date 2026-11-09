import argparse
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import codex_workflow as workflow


def catalog():
    return [{"id": name, "inputModalities": ["text", "image"],
             "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ("low", "medium", "high")],
             "defaultReasoningEffort": "medium"}
            for name in ("observed-model-a", "observed-model-b", "observed-model-c")]


def capabilities():
    return {"models": catalog(), "execution": {
        "subagents": True, "model_selection": True, "max_parallel_workers": 6}}


BRIEF = {"title": "Evidence-based planning", "audience": "Project team",
         "goal": "Choose a measured next step", "slide_count": 5}


class SingleModelTests(unittest.TestCase):
    def test_missing_catalog_uses_current_host_without_switching_or_parallelism(self):
        plan = workflow.make_plan(BRIEF)
        self.assertEqual(plan["schema_version"], 2)
        self.assertEqual(plan["max_parallel_workers"], 1)
        self.assertEqual(plan["execution"], "single_model")
        for flag in ("executes_models", "switches_models", "spawns_agents", "changes_reasoning_effort"):
            self.assertFalse(plan[flag])
        self.assertIsNone(plan["current_model"])
        self.assertIsNone(plan["current_effort"])
        self.assertNotIn("routes", plan)

    def test_any_observed_effort_and_future_model_keep_identical_quality_gates(self):
        baseline = workflow.make_plan(BRIEF)
        for effort in (None, "", "unset", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "future setting"):
            with self.subTest(effort=effort):
                cap = {"current_model": " User-chosen future MODEL ", "current_effort": effort}
                plan = workflow.make_plan(BRIEF, cap)
                self.assertEqual(plan["current_model"], cap["current_model"])
                self.assertEqual(plan["current_effort"], effort)
                self.assertEqual(plan["tasks"], baseline["tasks"])
                self.assertEqual(plan["quality_policy"], baseline["quality_policy"])
                self.assertEqual(plan["max_parallel_workers"], 1)

    def test_observed_multimodel_capabilities_do_not_change_workflow(self):
        plan = workflow.make_plan(BRIEF, capabilities())
        self.assertEqual(plan["tasks"], workflow.make_plan(BRIEF)["tasks"])
        self.assertEqual(plan["max_parallel_workers"], 1)
        for task in plan["tasks"]:
            self.assertEqual(task["owner"], "current_host")
            self.assertNotIn("model", task)
        pages = next(task for task in plan["tasks"] if task["id"] == "pages")
        self.assertEqual(pages["outputs"], [f"pages/{i:02d}.page" for i in range(1, 6)])

    def test_catalog_is_not_an_execution_prerequisite(self):
        with patch.object(workflow, "normalise_models", side_effect=AssertionError("catalog consulted")):
            plan = workflow.make_plan(BRIEF, {"models": {"unknown_future_catalog_format": True}, "execution": None})
        self.assertEqual(tuple(task["id"] for task in plan["tasks"]), workflow.TASK_IDS)

    def test_quality_tasks_are_mandatory_and_sequential(self):
        tasks = workflow.make_plan(BRIEF)["tasks"]
        self.assertEqual(tuple(t["id"] for t in tasks), workflow.TASK_IDS)
        for index, task in enumerate(tasks):
            self.assertEqual(task["depends_on"], [tasks[index - 1]["id"]] if index else [])
        self.assertEqual(workflow.TASK_IDS[-1], "layout")
        for gone in ("render", "visual_review", "export", "final_output_review"):
            self.assertNotIn(gone, workflow.TASK_IDS)

    def test_cli_plan_and_init_need_only_a_brief(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            brief, plan, project = root / "brief.json", root / "plan.json", root / "project"
            workflow.atomic_json(brief, BRIEF)
            with patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(workflow.main(["plan", "--brief", str(brief), "--output", str(plan)]), 0)
                self.assertEqual(workflow.main(["init", "--brief", str(brief), "--project", str(project)]), 0)
            self.assertEqual(workflow.read_json(plan)["max_parallel_workers"], 1)
            self.assertEqual(workflow.status_report(project)["ready"], ["story"])

    def test_agent_and_model_switch_commands_are_not_available(self):
        self.assertFalse(hasattr(workflow, "emit_agents"))
        self.assertFalse(hasattr(workflow, "resolve_role"))
        for args in (["agents"], ["plan", "--brief", "x", "--output", "y", "--builder-model", "other"]):
            with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit) as result:
                workflow.main(args)
            self.assertEqual(result.exception.code, 2)

    def test_model_list_json_rpc_envelope_and_hidden_models_remain_diagnostic(self):
        data = catalog() + [{"id": "hidden-model", "hidden": True}]
        self.assertEqual(len(workflow.normalise_models({"id": 6, "result": {"data": data}})), 3)

    def test_malformed_inputs_fail_with_workflow_errors(self):
        for brief, cap in (([], {}), (BRIEF, []), (BRIEF, {"current_effort": 2}),
                           ({**BRIEF, "slide_count": True}, {})):
            with self.subTest(brief=brief, cap=cap), self.assertRaises(workflow.WorkflowError):
                workflow.make_plan(brief, cap)


class StateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from layout_engine import select_font
        from layout_quality import LayoutError
        try:
            cls.font_family, cls.font_policy = select_font(text="Measured layout validation")
        except LayoutError as exc:
            raise unittest.SkipTest(f"No measurable installed fixture font: {exc}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "deck project"
        self.capabilities = {"current_model": "only-current-model", "current_effort": "none"}
        self.plan = workflow.make_plan(BRIEF, self.capabilities)
        workflow.initialise(self.project, BRIEF, self.capabilities, self.plan)

    def tearDown(self):
        self.temp.cleanup()

    def complete(self, name):
        task = next(t for t in self.plan["tasks"] if t["id"] == name)
        for rel in task["outputs"]:
            path = self.project / rel
            if rel == "run/structural-qa.json":
                workflow.atomic_json(path, workflow.validate_deck(self.project / "deck.pptd"))
            elif name == "layout":
                from layout_quality import audit_deck
                workflow.atomic_json(path, audit_deck(self.project / "deck.pptd"))
            elif rel == "run/layout-policy.json":
                workflow.atomic_json(path, {"schema_version": 1, "safe_margin": 24})
            elif path.suffix == ".json":
                workflow.atomic_json(path, {"content": "test evidence", "files": []})
            elif path.suffix == ".page":
                path.write_text("pageType: content\nbackground: {type: solid, color: '#F7F8FC'}\nelements:\n"
                                "  - elementId: title\n    elementType: text\n    bounds: [100, 180, 760, 120]\n"
                                f"    content:\n      fontFamily: {json.dumps(self.font_family)}\n      fontSize: 32\n"
                                "      color: '#171923'\n      text: Measured layout validation\n")
            elif rel == "deck.pptd":
                path.write_text("version: v2\ntitle: Test\nsize: [960, 540]\npages:\n" +
                                "".join(f"  - pages/{i:02d}.page\n" for i in range(1, BRIEF["slide_count"] + 1)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test output", encoding="utf-8")
        return workflow.record(self.project, name, "done")

    def complete_through(self, target):
        for task in self.plan["tasks"]:
            self.complete(task["id"])
            if task["id"] == target:
                break

    def test_init_preserves_existing_user_files(self):
        original = (self.project / "run/state.json").read_bytes()
        with self.assertRaises(workflow.WorkflowError):
            workflow.initialise(self.project, BRIEF, {}, self.plan)
        self.assertEqual(original, (self.project / "run/state.json").read_bytes())

    def test_cannot_complete_task_without_dependencies_and_artifacts(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow.record(self.project, "pages", "done")
        with self.assertRaises(workflow.WorkflowError):
            workflow.record(self.project, "story", "done")
        self.assertEqual(workflow.status_report(self.project)["ready"], ["story"])

    def test_file_changes_invalidate_downstream_after_resume(self):
        for name in ("story", "design", "assets", "pages", "integrate"):
            self.complete(name)
        (self.project / "pages/01.page").write_text("new content")
        report = workflow.status_report(self.project)
        self.assertEqual(report["tasks"]["pages"]["status"], "stale")
        self.assertEqual(report["tasks"]["integrate"]["status"], "stale")
        self.assertFalse(report["complete"])

    def test_asset_hash_changes_invalidate_its_consumer(self):
        for name in ("story", "design"):
            self.complete(name)
        asset = self.project / "media/illustration.bin"
        asset.write_bytes(b"original")
        workflow.atomic_json(self.project / "run/assets.json", {"files": [
            {"path": "media/illustration.bin", "sha256": workflow.digest(asset)}]})
        workflow.record(self.project, "assets", "done")
        self.complete("pages")
        asset.write_bytes(b"edited")
        report = workflow.status_report(self.project)
        self.assertEqual(report["tasks"]["assets"]["status"], "stale")
        self.assertEqual(report["tasks"]["pages"]["status"], "stale")

    def test_changed_brief_requires_replanning_instead_of_reusing_old_qa(self):
        workflow.atomic_json(self.project / "run/brief.json", {**BRIEF, "slide_count": 9})
        with self.assertRaisesRegex(workflow.WorkflowError, "Planning inputs changed"):
            workflow.status_report(self.project)

    def test_blocked_status_requires_reason(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow.record(self.project, "story", "blocked")
        workflow.record(self.project, "story", "blocked", "Source file unavailable")
        self.assertFalse(workflow.status_report(self.project)["complete"])

    def test_path_traversal_and_symlink_escape_rejected(self):
        for rel in ("../outside", "/absolute", "C:/outside", "run/../../secret"):
            with self.assertRaises(workflow.WorkflowError):
                workflow.contained(self.project, rel)
        outside = Path(self.temp.name) / "outside"
        outside.write_text("secret")
        (self.project / "media/link").symlink_to(outside)
        with self.assertRaises(workflow.WorkflowError):
            workflow.contained(self.project, "media/link", exists=True)

    def test_structure_cannot_pass_an_invalid_deck_with_a_claimed_pass(self):
        self.complete_through("pages")
        (self.project / "deck.pptd").write_text("not a real deck")
        workflow.record(self.project, "integrate", "done")
        workflow.atomic_json(self.project / "run/structural-qa.json", {"status": "passed", "slide_count": 5, "files": []})
        with self.assertRaises(workflow.WorkflowError):
            workflow.record(self.project, "structure", "done")
        self.assertFalse(workflow.status_report(self.project)["complete"])

    def test_structure_requires_source_count_to_match_the_plan(self):
        self.complete_through("pages")
        shutil.copyfile(Path(__file__).parent / "fixtures/minimal/minimal.pptd", self.project / "deck.pptd")
        workflow.record(self.project, "integrate", "done")
        qa = workflow.validate_deck(self.project / "deck.pptd")
        qa["slide_count"] = 5
        workflow.atomic_json(self.project / "run/structural-qa.json", qa)
        with self.assertRaisesRegex(workflow.WorkflowError, "slide count"):
            workflow.record(self.project, "structure", "done")

    def test_structure_requires_manifest_and_page_hashes(self):
        self.complete_through("integrate")
        workflow.atomic_json(self.project / "run/structural-qa.json", {"status": "passed", "slide_count": 5, "files": []})
        with self.assertRaisesRegex(workflow.WorkflowError, "fresh hashes"):
            workflow.record(self.project, "structure", "done")

    def test_completed_workflow_keeps_structural_checks_and_judgement_separate(self):
        self.complete_through("layout")
        report = workflow.status_report(self.project)
        self.assertTrue(report["complete"])
        self.assertEqual(report["tasks"]["layout"]["verification"]["kind"], "deterministic_layout_audit")
        self.assertEqual(report["visual_review"], "not_performed")
        self.assertEqual(report["pptx_export"], "not_available")

    def test_actual_referenced_media_are_hashed_even_if_registry_omits_them(self):
        self.complete_through("pages")
        image = self.project / "media/image.png"
        Image.new("RGB", (32, 32), "red").save(image)
        page = self.project / "pages/01.page"
        with page.open("a") as f:
            f.write('\n  - elementId: photo\n    elementType: image\n    bounds: [10, 10, 20, 20]\n    src: media/image.png\n')
        workflow.record(self.project, "pages", "done")
        self.complete("integrate")
        self.complete("structure")
        Image.new("RGB", (32, 32), "blue").save(image)
        self.assertEqual(workflow.status_report(self.project)["tasks"]["structure"]["status"], "stale")

    def test_malformed_state_cannot_be_reported_complete(self):
        state = workflow.read_json(self.project / "run/state.json")
        state["tasks"] = {}
        workflow.atomic_json(self.project / "run/state.json", state)
        with self.assertRaisesRegex(workflow.WorkflowError, "do not match"):
            workflow.status_report(self.project)

    def test_lock_prevents_overlapping_state_writes(self):
        with workflow.state_lock(self.project):
            with self.assertRaisesRegex(workflow.WorkflowError, "Another state update"):
                workflow.record(self.project, "story", "running")


    def test_schema_v1_jobs_require_a_new_run_without_overwriting_evidence(self):
        state_path = self.project / "run/state.json"
        state = workflow.read_json(state_path)
        state["schema_version"] = 1
        workflow.atomic_json(state_path, state)
        original = state_path.read_bytes()
        with self.assertRaisesRegex(workflow.WorkflowError, "schema_version 2"):
            workflow.status_report(self.project)
        self.assertEqual(state_path.read_bytes(), original)

    def test_required_quality_task_and_output_cannot_be_removed_from_plan(self):
        for mode in ("remove_task", "remove_qa_output", "remove_dependency"):
            with self.subTest(mode=mode):
                plan = copy.deepcopy(self.plan)
                if mode == "remove_task":
                    plan["tasks"] = [task for task in plan["tasks"] if task["id"] != "layout"]
                else:
                    task = next(task for task in plan["tasks"] if task["id"] == "layout")
                    task["outputs" if mode == "remove_qa_output" else "depends_on"] = []
                workflow.atomic_json(self.project / "run/plan.json", plan)
                with self.assertRaisesRegex(workflow.WorkflowError, "mandatory"):
                    workflow.status_report(self.project)
        workflow.atomic_json(self.project / "run/plan.json", self.plan)

    def test_hard_layout_failures_block_completion_even_with_claimed_pass(self):
        from layout_quality import audit_deck
        self.assertEqual(self.plan["current_effort"], "none")
        self.complete_through("pages")
        page = self.project / "pages/01.page"
        page.write_text(page.read_text().replace("[100, 180, 760, 120]", "[900, 180, 760, 120]"))
        workflow.record(self.project, "pages", "done")
        self.complete("integrate")
        self.complete("structure")
        qa = audit_deck(self.project / "deck.pptd")
        self.assertEqual(qa["status"], "failed")
        qa["status"] = "passed"
        workflow.atomic_json(self.project / "run/layout-qa.json", qa)
        with self.assertRaisesRegex(workflow.WorkflowError, "hard failures"):
            workflow.record(self.project, "layout", "done")
        self.assertFalse(workflow.status_report(self.project)["complete"])

    def prepare_review_finding(self):
        self.complete_through("pages")
        page = self.project / "pages/01.page"
        page.write_text(page.read_text().replace("    bounds:", "    rotation: 2\n    bounds:"))
        workflow.record(self.project, "pages", "done")
        self.complete("integrate")
        self.complete("structure")

    def test_layout_report_must_retain_findings_status_and_font_fingerprints(self):
        from layout_quality import audit_deck
        self.prepare_review_finding()
        original = audit_deck(self.project / "deck.pptd")
        self.assertEqual(original["status"], "needs_review")
        for field, value in (("status", "passed"), ("issues", []), ("fonts", [])):
            qa = copy.deepcopy(original)
            qa[field] = value
            workflow.atomic_json(self.project / "run/layout-qa.json", qa)
            with self.subTest(field=field), self.assertRaisesRegex(workflow.WorkflowError, "current audit"):
                workflow.record(self.project, "layout", "done")
        workflow.atomic_json(self.project / "run/layout-qa.json", original)
        result = workflow.record(self.project, "layout", "done")
        self.assertEqual(result["verification"]["status"], "needs_review")
        status = workflow.status_report(self.project)
        self.assertEqual(status["ready"], [])
        self.assertEqual(status["visual_review"], "not_performed")
        self.assertEqual(status["unresolved_layout_issues"], [original["issues"][0]["id"]])
        self.assertFalse(status["complete"])

    def test_layout_report_changes_invalidate_the_layout_task(self):
        self.complete_through("layout")
        path = self.project / "run/layout-qa.json"
        qa = workflow.read_json(path)
        qa["checked_at"] = "changed after review"
        workflow.atomic_json(path, qa)
        status = workflow.status_report(self.project)
        self.assertEqual(status["tasks"]["layout"]["status"], "stale")
        self.assertFalse(status["complete"])

    def test_changed_checker_rules_invalidate_unchanged_source_evidence(self):
        self.complete_through("layout")
        changed = workflow.checker_fingerprints()
        changed["layout_quality.py"] = "0" * 64
        with patch.object(workflow, "checker_fingerprints", return_value=changed):
            status = workflow.status_report(self.project)
        self.assertEqual(status["tasks"]["layout"]["status"], "stale")
        self.assertIn("checker implementation", status["tasks"]["layout"]["reason"])
        self.assertFalse(status["complete"])
        state = workflow.read_json(self.project / "run/state.json")
        del state["tasks"]["layout"]["verification"]["checker_sha256"]
        workflow.atomic_json(self.project / "run/state.json", state)
        legacy = workflow.status_report(self.project)
        self.assertEqual(legacy["tasks"]["layout"]["status"], "stale")
        self.assertFalse(legacy["complete"])

    def test_measured_font_bytes_changing_invalidates_layout(self):
        font = Path(self.font_policy["fonts"][self.font_family]["regular"])
        self.complete("story")
        self.complete("design")
        local_font = self.project / "media/measured-font.ttf"
        shutil.copyfile(font, local_font)
        policy = {"fonts": {self.font_family: {"regular": "media/measured-font.ttf"}}}
        workflow.atomic_json(self.project / "run/layout-policy.json", policy)
        workflow.record(self.project, "design", "done")
        for task in ("assets", "pages", "integrate", "structure", "layout"):
            self.complete(task)
        with local_font.open("ab") as stream:
            stream.write(b"changed font bytes")
        status = workflow.status_report(self.project)
        self.assertEqual(status["tasks"]["layout"]["status"], "stale")
        self.assertIn("font:", status["tasks"]["layout"]["reason"])


class DoctorTests(unittest.TestCase):
    def test_doctor_does_not_install_or_probe_app_server_by_default(self):
        args = argparse.Namespace(capabilities=None, model_catalog=None, probe_codex=False)
        with patch.object(workflow, "version", return_value={"available": False}), patch.object(workflow, "probe_models") as probe:
            result = workflow.doctor(args)
        probe.assert_not_called()
        self.assertEqual(result["models"], [])
        self.assertNotIn("renderer", result)
        self.assertNotIn("agent-browser", result["tools"])
        self.assertEqual(result["target_desktop"]["evidence"], "user_reported")

    def test_minimal_fixture_passes_only_structural_subset(self):
        fixture = Path(__file__).parent / "fixtures/minimal/minimal.pptd"
        result = workflow.validate_deck(fixture)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["slide_count"], 1)
        self.assertEqual(result["visual_review"], "not_performed")


class CapturedInput(io.StringIO):
    def close(self):
        self.transcript = self.getvalue()
        super().close()


class FakeAppServer:
    def __init__(self, replies=(), stdout=None):
        self.stdin = CapturedInput()
        self.stdout = stdout if stdout is not None else io.StringIO("".join(json.dumps(x) + "\n" for x in replies))
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def kill(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


class ProbeTests(unittest.TestCase):
    def test_probe_only_initialises_and_lists_models_with_pagination(self):
        proc = FakeAppServer([
            {"id": 0, "result": {"userAgent": "test"}},
            {"id": 1, "result": {"data": catalog()[:1], "nextCursor": "page-two"}},
            {"id": 2, "result": {"data": catalog()[1:], "nextCursor": None}},
        ])
        with patch.object(workflow.subprocess, "Popen", return_value=proc):
            models = workflow.probe_models("fake-codex")
        sent = [json.loads(x) for x in proc.stdin.transcript.splitlines()]
        self.assertEqual([x["method"] for x in sent], ["initialize", "initialized", "model/list", "model/list"])
        self.assertEqual(sent[-1]["params"]["cursor"], "page-two")
        self.assertEqual(len(models), 3)
        self.assertTrue(proc.stopped)

    def test_probe_does_not_answer_an_interactive_approval(self):
        proc = FakeAppServer([{"id": 99, "method": "approval/request", "params": {}}])
        with patch.object(workflow.subprocess, "Popen", return_value=proc), self.assertRaisesRegex(workflow.WorkflowError, "interactive"):
            workflow.probe_models("fake-codex")
        sent = [json.loads(x) for x in proc.stdin.transcript.splitlines()]
        self.assertEqual([x["method"] for x in sent], ["initialize"])

    def test_probe_malformed_error_has_a_controlled_failure(self):
        proc = FakeAppServer([{"id": 0, "error": "invalid"}])
        with patch.object(workflow.subprocess, "Popen", return_value=proc), self.assertRaises(workflow.WorkflowError):
            workflow.probe_models("fake-codex")

    def test_timeout_does_not_block_closing_an_inherited_stdout(self):
        release = threading.Event()

        class InheritedPipe:
            def readline(self, _limit):
                release.wait(10)
                return ""

            def close(self):
                if not release.is_set():
                    raise AssertionError("Closing the reader lock would block")

        proc = FakeAppServer(stdout=InheritedPipe())
        started = time.monotonic()
        try:
            with patch.object(workflow.subprocess, "Popen", return_value=proc), self.assertRaisesRegex(workflow.WorkflowError, "timed out"):
                workflow.probe_models("fake-codex", timeout=0.02)
            self.assertLess(time.monotonic() - started, 2.5)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
