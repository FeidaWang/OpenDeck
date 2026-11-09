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
import zipfile
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
        self.assertLess(workflow.TASK_IDS.index("layout"), workflow.TASK_IDS.index("render"))
        self.assertLess(workflow.TASK_IDS.index("export"), workflow.TASK_IDS.index("final_output_review"))

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
            elif name in ("render", "visual_review"):
                workflow.atomic_json(path, self.image_qa(visual=name == "visual_review"))
            elif name == "final_output_review":
                workflow.atomic_json(path, self.final_qa())
            elif rel == "run/layout-policy.json":
                workflow.atomic_json(path, {"schema_version": 1, "safe_margin": 24})
            elif rel == "deck.pptx":
                self.make_pptx(path)
            elif rel == "run/export-qa.json":
                workflow.atomic_json(path, self.export_qa())
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

    def image_qa(self, visual=False, final=False):
        pages, files = [], []
        for i in range(1, BRIEF["slide_count"] + 1):
            rel = f"run/{'final-renders' if final else 'renders'}/{i:02d}.png"
            image = self.project / rel
            image.parent.mkdir(exist_ok=True)
            Image.new("RGB", (960, 540), (i * 20, 80, 100)).save(image)
            pages.append({"page": i, "path": rel, **({"status": "passed"} if visual else {})})
            files.append({"path": rel, "sha256": workflow.digest(image)})
        result = {"status": "passed", "slide_count": BRIEF["slide_count"], "pages": pages, "files": files,
                  "render_source": "final_pptx" if final else "source_pptd",
                  **({"review_method": "operator_attested"} if visual else {})}
        if visual and not final:
            layout_path = "run/layout-qa.json"
            layout = workflow.read_json(self.project / layout_path)
            layout_sha = workflow.digest(self.project / layout_path)
            result["files"].append({"path": layout_path, "sha256": layout_sha})
            result["layout_review"] = {"report_path": layout_path, "sha256": layout_sha, "resolutions": [
                {"issue_id": issue["id"], "page": issue["page"], "path": pages[issue["page"] - 1]["path"],
                 "status": "resolved", "explanation": "Inspected the specific effect and confirmed readable, unclipped content."}
                for issue in layout["issues"] if issue["severity"] == "review"]}
        return result

    def final_qa(self):
        original = self.image_qa(final=True)
        original["kind"] = "final_pptx_render"
        original["source_pptx"] = {"path": "deck.pptx", "sha256": workflow.digest(self.project / "deck.pptx")}
        original["renderer"] = {"name": "Fixture presentation renderer", "version": "test-only"}
        original["files"].append(original["source_pptx"])
        original["visual_review"] = "not_performed"
        rel = "run/final-render-qa.json"
        workflow.atomic_json(self.project / rel, original)
        qa = copy.deepcopy(original)
        qa["render_report"] = {"path": rel, "sha256": workflow.digest(self.project / rel)}
        qa["files"].append(qa["render_report"])
        qa["review_method"] = "operator_attested"
        qa["visual_review"] = "operator_attested"
        for page in qa["pages"]:
            page["status"] = "passed"
        return qa

    def make_pptx(self, path, count=None):
        import export_pptx
        count = BRIEF["slide_count"] if count is None else count
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml",
                             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                             '<Override PartName="/ppt/presentation.xml" '
                             f'ContentType="{export_pptx.PPTX_CONTENT_TYPE}"/></Types>')
            archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="urn:test"/>')
            for i in range(1, count + 1):
                archive.writestr(f"ppt/slides/slide{i}.xml", '<p:sld xmlns:p="urn:test"><p:cSld/>'
                                 '<p:transition><p:fade/></p:transition></p:sld>')

    def export_qa(self):
        return {"status": "passed", "slide_count": BRIEF["slide_count"], "transition": "fade",
                "font_parts": 0, "files": [{"path": "deck.pptx", "sha256": workflow.digest(self.project / "deck.pptx")}]}

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

    def test_qa_requires_real_images_and_hashes(self):
        for name in ("story", "design", "assets", "pages", "integrate"):
            self.complete(name)
        self.complete("structure")
        self.complete("layout")
        workflow.atomic_json(self.project / "run/render-qa.json", {"status": "passed", "slide_count": 5, "files": []})
        with self.assertRaisesRegex(workflow.WorkflowError, "actual image"):
            workflow.record(self.project, "render", "done")
        image = self.project / "media/render.png"
        image.write_bytes(b"test fixture bytes")
        workflow.atomic_json(self.project / "run/render-qa.json", {"status": "passed", "slide_count": 5, "files": [
            {"path": "media/render.png", "sha256": "bad hash"}]})
        with self.assertRaisesRegex(workflow.WorkflowError, "changed"):
            workflow.record(self.project, "render", "done")

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

    def test_hash_matching_text_named_png_does_not_count_as_rendered_evidence(self):
        self.complete_through("layout")
        qa = self.image_qa()
        image = self.project / qa["files"][0]["path"]
        image.write_bytes(b"not an image")
        qa["files"][0]["sha256"] = workflow.digest(image)
        workflow.atomic_json(self.project / "run/render-qa.json", qa)
        with self.assertRaisesRegex(ValueError, "raster"):
            workflow.record(self.project, "render", "done")

    def test_render_requires_complete_ordered_page_coverage(self):
        self.complete_through("layout")
        qa = self.image_qa()
        qa["pages"][1]["page"] = 1
        workflow.atomic_json(self.project / "run/render-qa.json", qa)
        with self.assertRaisesRegex(workflow.WorkflowError, "source order"):
            workflow.record(self.project, "render", "done")

    def test_visual_quality_is_an_explicit_operator_attestation(self):
        self.complete_through("render")
        qa = self.image_qa(visual=True)
        del qa["review_method"]
        workflow.atomic_json(self.project / "run/visual-qa.json", qa)
        with self.assertRaisesRegex(workflow.WorkflowError, "operator_attested"):
            workflow.record(self.project, "visual_review", "done")
        qa["review_method"] = "operator_attested"
        qa["pages"][0]["status"] = "pending"
        workflow.atomic_json(self.project / "run/visual-qa.json", qa)
        with self.assertRaisesRegex(workflow.WorkflowError, "Each visual"):
            workflow.record(self.project, "visual_review", "done")

    def test_export_cannot_complete_using_non_pptx_bytes_or_the_wrong_count(self):
        self.complete_through("visual_review")
        pptx = self.project / "deck.pptx"
        pptx.write_text("not a real deck")
        workflow.atomic_json(self.project / "run/export-qa.json", self.export_qa())
        with self.assertRaisesRegex(workflow.WorkflowError, "Fresh PPTX"):
            workflow.record(self.project, "export", "done")
        self.make_pptx(pptx, count=1)
        workflow.atomic_json(self.project / "run/export-qa.json", self.export_qa())
        with self.assertRaisesRegex(workflow.WorkflowError, "source has 5"):
            workflow.record(self.project, "export", "done")
        self.assertFalse(workflow.status_report(self.project)["complete"])

    def test_completed_workflow_keeps_structural_checks_and_judgement_separate(self):
        self.complete_through("export")
        self.assertFalse(workflow.status_report(self.project)["complete"])
        self.complete("final_output_review")
        report = workflow.status_report(self.project)
        self.assertTrue(report["complete"])
        self.assertEqual(report["tasks"]["visual_review"]["verification"]["kind"], "operator_attested_visual_review")
        self.assertEqual(report["tasks"]["export"]["verification"]["slides"], 5)
        self.assertEqual(report["tasks"]["export"]["verification"]["playback_review"], "not_performed")

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

    def test_hard_layout_failures_block_low_effort_export_even_with_claimed_pass(self):
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
        for task in ("render", "visual_review", "export", "final_output_review"):
            with self.subTest(task=task), self.assertRaisesRegex(workflow.WorkflowError, "dependencies"):
                workflow.record(self.project, task, "done")
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
        self.assertEqual(status["ready"], ["render"])
        self.assertEqual(status["unresolved_layout_issues"], [original["issues"][0]["id"]])
        self.assertFalse(status["complete"])

    def test_visual_review_requires_each_current_issue_resolved_with_page_evidence(self):
        self.prepare_review_finding()
        self.complete("layout")
        self.complete("render")
        good = self.image_qa(visual=True)
        for mode in ("unresolved", "stale_hash", "wrong_page", "wrong_issue", "no_explanation"):
            qa = copy.deepcopy(good)
            review = qa["layout_review"]
            if mode == "unresolved":
                review["resolutions"] = []
            elif mode == "stale_hash":
                review["sha256"] = "0" * 64
            elif mode == "wrong_page":
                review["resolutions"][0]["page"] = 2
            elif mode == "wrong_issue":
                review["resolutions"][0]["issue_id"] = "not-a-current-issue"
            else:
                review["resolutions"][0]["explanation"] = ""
            workflow.atomic_json(self.project / "run/visual-qa.json", qa)
            with self.subTest(mode=mode), self.assertRaises(workflow.WorkflowError):
                workflow.record(self.project, "visual_review", "done")
        workflow.atomic_json(self.project / "run/visual-qa.json", good)
        workflow.record(self.project, "visual_review", "done")
        report = workflow.status_report(self.project)
        self.assertEqual(report["unresolved_layout_issues"], [])
        self.assertEqual(report["layout_audit_status"], "needs_review")
        self.assertEqual(report["layout_issue_resolution"], "operator_attested")
        self.assertEqual(report["ready"], ["export"])

    def test_layout_report_changes_invalidate_visual_and_final_reviews(self):
        self.complete_through("final_output_review")
        path = self.project / "run/layout-qa.json"
        qa = workflow.read_json(path)
        qa["checked_at"] = "changed after review"
        workflow.atomic_json(path, qa)
        status = workflow.status_report(self.project)
        for task in ("layout", "render", "visual_review", "export", "final_output_review"):
            self.assertEqual(status["tasks"][task]["status"], "stale")
        self.assertFalse(status["complete"])

    def test_changed_checker_rules_invalidate_unchanged_source_evidence(self):
        self.complete_through("final_output_review")
        changed = workflow.checker_fingerprints()
        changed["layout_quality.py"] = "0" * 64
        with patch.object(workflow, "checker_fingerprints", return_value=changed):
            status = workflow.status_report(self.project)
        for task in ("layout", "render", "visual_review", "export", "final_output_review"):
            self.assertEqual(status["tasks"][task]["status"], "stale")
        self.assertIn("checker implementation", status["tasks"]["layout"]["reason"])
        self.assertFalse(status["complete"])
        state = workflow.read_json(self.project / "run/state.json")
        del state["tasks"]["layout"]["verification"]["checker_sha256"]
        workflow.atomic_json(self.project / "run/state.json", state)
        legacy = workflow.status_report(self.project)
        self.assertEqual(legacy["tasks"]["layout"]["status"], "stale")
        self.assertEqual(legacy["tasks"]["final_output_review"]["status"], "stale")
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

    def test_tiny_thumbnail_cannot_substitute_for_readable_page_inspection(self):
        self.complete_through("layout")
        qa = self.image_qa()
        image = self.project / qa["pages"][0]["path"]
        Image.new("RGB", (96, 54), "white").save(image)
        qa["files"][0]["sha256"] = workflow.digest(image)
        workflow.atomic_json(self.project / "run/render-qa.json", qa)
        with self.assertRaisesRegex(workflow.WorkflowError, "source canvas resolution"):
            workflow.record(self.project, "render", "done")

    def test_final_review_requires_current_pptx_and_original_renderer_evidence(self):
        self.complete_through("export")
        good = self.final_qa()
        for mode in ("source_hash", "renderer", "report_hash", "render_source", "missing_report", "page_pending"):
            qa = copy.deepcopy(good)
            if mode == "source_hash":
                qa["source_pptx"]["sha256"] = "0" * 64
            elif mode == "renderer":
                qa["renderer"]["name"] = "Different renderer"
            elif mode == "report_hash":
                qa["render_report"]["sha256"] = "0" * 64
            elif mode == "render_source":
                qa["render_source"] = "source_pptd"
            elif mode == "missing_report":
                del qa["render_report"]
            else:
                qa["pages"][0]["status"] = "pending"
            workflow.atomic_json(self.project / "run/final-output-qa.json", qa)
            with self.subTest(mode=mode), self.assertRaises(workflow.WorkflowError):
                workflow.record(self.project, "final_output_review", "done")
        self.assertFalse(workflow.status_report(self.project)["complete"])
        workflow.atomic_json(self.project / "run/final-output-qa.json", good)
        workflow.record(self.project, "final_output_review", "done")
        status = workflow.status_report(self.project)
        self.assertTrue(status["complete"])
        self.assertEqual(status["final_pptx_review_status"], "operator_attested")
        self.assertEqual(status["playback_review"], "not_performed")

    def test_source_preview_paths_cannot_be_relabelled_as_final_pptx_evidence(self):
        self.complete_through("export")
        qa = self.final_qa()
        source = workflow.read_json(self.project / "run/render-qa.json")
        original = workflow.read_json(self.project / "run/final-render-qa.json")
        original["pages"] = source["pages"]
        original["files"] = source["files"] + [original["source_pptx"]]
        workflow.atomic_json(self.project / "run/final-render-qa.json", original)
        qa["render_report"]["sha256"] = workflow.digest(self.project / "run/final-render-qa.json")
        qa["pages"] = [{**page, "status": "passed"} for page in source["pages"]]
        qa["files"] = original["files"] + [qa["render_report"]]
        workflow.atomic_json(self.project / "run/final-output-qa.json", qa)
        with self.assertRaisesRegex(workflow.WorkflowError, "Source-render image paths"):
            workflow.record(self.project, "final_output_review", "done")

    def test_final_pptx_mutation_invalidates_export_and_final_review(self):
        self.complete_through("final_output_review")
        with (self.project / "deck.pptx").open("ab") as stream:
            stream.write(b"changed after rendering")
        report = workflow.status_report(self.project)
        self.assertEqual(report["tasks"]["export"]["status"], "stale")
        self.assertEqual(report["tasks"]["final_output_review"]["status"], "stale")
        self.assertFalse(report["complete"])

    def test_static_final_render_cannot_claim_application_playback_without_attestation(self):
        self.complete_through("export")
        qa = self.final_qa()
        qa["playback_review"] = "passed"
        workflow.atomic_json(self.project / "run/final-output-qa.json", qa)
        with self.assertRaisesRegex(workflow.WorkflowError, "playback attestation"):
            workflow.record(self.project, "final_output_review", "done")


class DoctorTests(unittest.TestCase):
    def test_doctor_does_not_install_or_probe_app_server_by_default(self):
        args = argparse.Namespace(capabilities=None, model_catalog=None, probe_codex=False)
        with patch.object(workflow, "version", return_value={"available": False}), patch.object(workflow, "probe_models") as probe:
            result = workflow.doctor(args)
        probe.assert_not_called()
        self.assertEqual(result["models"], [])
        self.assertFalse(result["renderer"]["live_verified"])
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
