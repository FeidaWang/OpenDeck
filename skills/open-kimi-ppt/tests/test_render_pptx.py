import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

import fitz

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_pptx.py"
SPEC = importlib.util.spec_from_file_location("render_pptx", SCRIPT)
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


def make_pptx(path, count=2, declared_count=None, external_type=None):
    declared_count = count if declared_count is None else declared_count
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
                         f'<Types xmlns="{render.CONTENT_NS}"><Override PartName="/ppt/presentation.xml" '
                         f'ContentType="{render.PPTX_CONTENT_TYPE}"/></Types>')
        ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, declared_count + 1))
        archive.writestr("ppt/presentation.xml",
                         f'<p:presentation xmlns:p="{render.PRESENTATION_NS}" xmlns:r="{render.REL_NS}">'
                         f'<p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')
        relationships = "".join(f'<Relationship Id="rId{i}" Type="{render.REL_NS}/slide" '
                                f'Target="slides/slide{i}.xml"/>' for i in range(1, count + 1))
        if external_type:
            relationships += (f'<Relationship Id="rExternal" Type="{render.REL_NS}/{external_type}" '
                              'TargetMode="External" Target="https://example.invalid/resource"/>')
        archive.writestr("ppt/_rels/presentation.xml.rels",
                         f'<Relationships xmlns="{render.PACKAGE_REL_NS}">{relationships}</Relationships>')
        for i in range(1, count + 1):
            archive.writestr(f"ppt/slides/slide{i}.xml", f'<p:sld xmlns:p="{render.PRESENTATION_NS}"><p:cSld/></p:sld>')


class FinalPptxRenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # Resolve the temp dir: on macOS /var is a symlink to /private/var and the CLI resolves paths.
        self.root = Path(self.temporary.name).resolve() / "project with spaces"
        self.root.mkdir()
        self.source = self.root / "deck.pptx"
        make_pptx(self.source)
        self.output = self.root / "run/final-renders"
        self.report = self.root / "run/final-render-qa.json"
        self.commands = []

    def fake_runner(self, argv, cwd, timeout, *, capture=False, pages=2, width=960, height=540):
        self.commands.append((argv, cwd, timeout, capture))
        if capture:
            return "LibreOffice Test 1.0"
        directory = Path(argv[argv.index("--outdir") + 1])
        with fitz.open() as document:
            for i in range(pages):
                page = document.new_page(width=width, height=height)
                page.insert_text((40, 60), f"Final slide {i + 1}", fontsize=24)
            document.save(directory / "source.pdf")
        return ""

    def run_render(self, runner=None, **kwargs):
        with mock.patch.object(render, "find_soffice", return_value=Path(sys.executable)), \
                mock.patch.object(render, "_run", side_effect=runner or self.fake_runner):
            return render.render_pptx(self.source, self.root, **kwargs)

    def assert_no_publication(self):
        self.assertFalse(self.report.exists())
        if self.output.exists():
            self.assertEqual(list(self.output.iterdir()), [])

    def test_report_binds_final_pptx_and_actual_page_files_without_visual_claims(self):
        original = self.source.read_bytes()
        report = self.run_render()
        self.assertEqual(report, json.loads(self.report.read_text()))
        self.assertEqual(report["kind"], "final_pptx_render")
        self.assertEqual(report["render_source"], "final_pptx")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["slide_count"], 2)
        self.assertEqual(report["visual_review"], "not_performed")
        self.assertEqual(report["playback"], "not_performed")
        self.assertNotIn("review_method", report)
        self.assertEqual(report["source_pptx"], {"path": "deck.pptx", "sha256": render.digest(self.source)})
        self.assertEqual(report["renderer"]["name"], "LibreOffice")
        self.assertEqual(len(report["files"]), 3)
        for page in report["pages"]:
            image = self.root / page["path"]
            self.assertEqual(page["status"], "rendered")
            self.assertEqual(page["sha256"], render.digest(image))
            pixmap = fitz.Pixmap(str(image))
            self.assertEqual((pixmap.width, pixmap.height), (1920, 1080))
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(len(list(self.output.iterdir())), 2)
        self.assertFalse(list(self.output.parent.glob(".final-pptx-stage-*")))

    def test_argv_uses_neutral_pinned_file_and_fresh_isolated_profile(self):
        dangerous = self.root / "deck ; $(touch unwanted) 'quoted'.pptx"
        self.source.rename(dangerous)
        self.source = dangerous
        self.run_render()
        version, conversion = self.commands
        self.assertIsInstance(conversion[0], list)
        self.assertEqual(version[0][1], conversion[0][1])
        self.assertTrue(conversion[0][1].startswith("-env:UserInstallation=file:"))
        self.assertTrue(conversion[0][-1].endswith("/source.pptx"))
        self.assertNotIn(str(dangerous), conversion[0])
        self.assertIn("pdf:impress_pdf_Export", conversion[0])
        self.assertFalse((self.root / "unwanted").exists())
        self.assertFalse(conversion[1].exists())

    def test_existing_outputs_and_reports_are_never_overwritten(self):
        self.output.mkdir(parents=True)
        existing = self.output / "user.png"
        existing.write_bytes(b"preserve this")
        with self.assertRaisesRegex(render.RenderError, "new or empty"):
            self.run_render()
        self.assertEqual(existing.read_bytes(), b"preserve this")
        self.assertEqual(self.commands, [])
        existing.unlink()
        self.report.write_text("existing review")
        with self.assertRaisesRegex(render.RenderError, "already exists"):
            self.run_render()
        self.assertEqual(self.report.read_text(), "existing review")
        self.assertEqual(self.commands, [])

    def test_existing_empty_output_is_supported(self):
        self.output.mkdir(parents=True)
        self.run_render()
        self.assertTrue(self.report.is_file())

    def test_source_and_output_cannot_escape_project(self):
        outside = self.root.parent / "outside.pptx"
        make_pptx(outside)
        self.source.unlink()
        self.source.symlink_to(outside)
        with self.assertRaisesRegex(render.RenderError, "contained"):
            self.run_render()
        self.source.unlink()
        make_pptx(self.source)
        for kwargs in ({"output": "../outside"}, {"report": "../report.json"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(render.RenderError):
                self.run_render(**kwargs)
        self.assertEqual(self.commands, [])

    def test_output_symlink_is_rejected_even_when_target_is_inside_project(self):
        actual = self.root / "actual"
        actual.mkdir()
        self.output.parent.mkdir()
        self.output.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(render.RenderError, "symlink"):
            self.run_render()
        self.assertEqual(list(actual.iterdir()), [])

    def test_false_pptx_and_inconsistent_slide_count_are_rejected_before_renderer(self):
        self.source.write_text("not a presentation")
        with self.assertRaises(render.RenderError):
            self.run_render()
        make_pptx(self.source, count=2, declared_count=3)
        with self.assertRaisesRegex(render.RenderError, "slide count"):
            self.run_render()
        self.assertEqual(self.commands, [])

    def test_linked_external_resources_rejected_but_inert_hyperlinks_supported(self):
        make_pptx(self.source, external_type="image")
        with self.assertRaisesRegex(render.RenderError, "External linked"):
            render.inspect_pptx(self.source)
        make_pptx(self.source, external_type="hyperlink")
        self.assertEqual(render.inspect_pptx(self.source)["slide_count"], 2)

    def test_mismatched_pdf_count_and_aspect_are_not_published(self):
        for dimensions in ({"pages": 1}, {"width": 540, "height": 960}):
            with self.subTest(dimensions=dimensions), self.assertRaises(render.RenderError):
                self.run_render(lambda *args, **kwargs: self.fake_runner(*args, **kwargs, **dimensions))
            self.assert_no_publication()

    def test_renderer_failure_or_missing_pdf_is_not_published(self):
        def failing(argv, *args, **kwargs):
            if kwargs.get("capture"):
                return "LibreOffice Test"
            raise render.RenderError("renderer failed")
        with self.assertRaisesRegex(render.RenderError, "renderer failed"):
            self.run_render(failing)
        self.assert_no_publication()
        with self.assertRaisesRegex(render.RenderError, "bounded PDF"):
            self.run_render(lambda *args, **kwargs: "LibreOffice Test" if kwargs.get("capture") else "")
        self.assert_no_publication()

    def test_source_changes_during_render_are_detected(self):
        def changing(*args, **kwargs):
            result = self.fake_runner(*args, **kwargs)
            if not kwargs.get("capture"):
                with self.source.open("ab") as file:
                    file.write(b"external concurrent edit")
            return result
        with self.assertRaisesRegex(render.RenderError, "changed during"):
            self.run_render(changing)
        self.assert_no_publication()

    def test_source_changes_during_validation_are_detected_before_execution(self):
        real_inspect = render.inspect_pptx
        def changing(path):
            result = real_inspect(path)
            with path.open("ab") as file:
                file.write(b"concurrent edit")
            return result
        with mock.patch.object(render, "inspect_pptx", side_effect=changing):
            with self.assertRaisesRegex(render.RenderError, "changed while"):
                self.run_render()
        self.assertEqual(self.commands, [])
        self.assert_no_publication()

    def test_page_and_total_pixel_budgets_block_publication(self):
        for limit in ("MAX_PAGE_PIXELS", "MAX_TOTAL_PIXELS"):
            with self.subTest(limit=limit), mock.patch.object(render, limit, 100):
                with self.assertRaisesRegex(render.RenderError, "pixel budget"):
                    self.run_render()
            self.assert_no_publication()

    def test_concurrent_output_is_preserved_and_not_mixed_with_render(self):
        def competing(*args, **kwargs):
            result = self.fake_runner(*args, **kwargs)
            if not kwargs.get("capture"):
                (self.output / "concurrent-user.txt").write_text("preserve me")
            return result
        with self.assertRaisesRegex(render.RenderError, "ownership changed"):
            self.run_render(competing)
        self.assertEqual((self.output / "concurrent-user.txt").read_text(), "preserve me")
        self.assertEqual(len(list(self.output.iterdir())), 1)
        self.assertFalse(self.report.exists())

    def test_concurrent_report_is_preserved_and_own_partial_publication_rolls_back(self):
        def competing(*args, **kwargs):
            result = self.fake_runner(*args, **kwargs)
            if not kwargs.get("capture"):
                self.report.write_text("concurrent report")
            return result
        with self.assertRaises(FileExistsError):
            self.run_render(competing)
        self.assertEqual(self.report.read_text(), "concurrent report")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_timeout_stops_local_process_and_does_not_hang(self):
        started = time.monotonic()
        with self.assertRaisesRegex(render.RenderError, "timeout"):
            render._run([sys.executable, "-c", "import time; time.sleep(10)"], self.root, 0.05)
        self.assertLess(time.monotonic() - started, 3)

    def test_missing_renderer_is_explicit_and_help_needs_no_site_packages(self):
        with self.assertRaisesRegex(render.RenderError, "Nothing was installed"):
            render.find_soffice(self.root / "missing-libreoffice")
        result = subprocess.run([sys.executable, "-S", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--project-root", result.stdout)

    def test_explicit_cli_paths_follow_current_directory_without_duplicating_project(self):
        name = self.root.name
        previous = Path.cwd()
        os.chdir(self.root.parent)
        try:
            with mock.patch.object(render, "render_pptx", return_value={}) as operation, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(render.main([f"{name}/deck.pptx", "--project-root", name,
                                              "--output", f"{name}/run/final-renders",
                                              "--report", f"{name}/run/final-render-qa.json"]), 0)
        finally:
            os.chdir(previous)
        args, kwargs = operation.call_args
        self.assertEqual(args, (self.source, self.root))
        self.assertEqual(kwargs["output"], self.output)
        self.assertEqual(kwargs["report"], self.report)


if __name__ == "__main__":
    unittest.main()
