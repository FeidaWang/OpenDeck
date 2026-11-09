#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_pptx.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("export_pptx", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ExportPptxTests(unittest.TestCase):
    def test_parse_agent_browser_version(self):
        self.assertEqual(MODULE.parse_version("agent-browser 0.33.2"), (0, 33, 2))
        self.assertEqual(MODULE.parse_version("v1.4.0-beta.1"), (1, 4, 0))

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_old_agent_browser_is_rejected_without_installation(self, which, run_command):
        which.side_effect = [
            "/bin/node",
            "/bin/npm",
            "/bin/agent-browser",
        ]
        run_command.side_effect = [
            MODULE.subprocess.CompletedProcess([], 0, "v22.11.0\n"),
            MODULE.subprocess.CompletedProcess([], 0, "agent-browser 0.17.1\n"),
        ]
        with self.assertRaisesRegex(MODULE.ExportError, "No global npm installation"):
            MODULE.ensure_agent_browser()
        self.assertEqual(len(run_command.call_args_list), 2)
        self.assertTrue(all(call.args[0][-1] == "--version" for call in run_command.call_args_list))

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_missing_nodejs_raises_clear_error(self, which, run_command):
        which.return_value = None
        with self.assertRaisesRegex(MODULE.ExportError, "Node.js is not installed"):
            MODULE.ensure_nodejs()
        run_command.assert_not_called()

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_old_nodejs_raises_clear_error(self, which, run_command):
        which.return_value = "/bin/node"
        run_command.return_value = MODULE.subprocess.CompletedProcess([], 0, "v16.20.2\n")
        with self.assertRaisesRegex(MODULE.ExportError, "Node.js 18\\+ is required"):
            MODULE.ensure_nodejs()

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_missing_npm_raises_clear_error(self, which, run_command):
        which.side_effect = ["/bin/node", None]
        run_command.return_value = MODULE.subprocess.CompletedProcess([], 0, "v22.11.0\n")
        with self.assertRaisesRegex(MODULE.ExportError, "npm is not installed"):
            MODULE.ensure_nodejs()

    def test_parse_node_version(self):
        self.assertEqual(MODULE.parse_node_version("v22.11.0"), (22, 11, 0))
        self.assertEqual(MODULE.parse_node_version("18.20.4"), (18, 20, 4))

    def test_fade_is_inserted_before_timing(self):
        source = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<p:sld xmlns:p="urn:test"><p:cSld><p:spTree><p:extLst/>'
            b'</p:spTree></p:cSld><p:clrMapOvr/><p:timing/><p:extLst/></p:sld>'
        )
        result_bytes = MODULE.replace_transition(source, "fade")
        result = result_bytes.decode("utf-8")
        self.assertIn("<p:transition", result)
        self.assertIn("<p:fade/>", result)
        self.assertGreater(result.index("<p:transition"), result.index("<p:clrMapOvr"))
        self.assertLess(result.index("<p:transition"), result.index("<p:timing"))
        MODULE.validate_transition_order(result_bytes, "fade")

    def test_existing_transition_is_replaced_or_removed(self):
        source = (
            b'<p:sld xmlns:p="urn:test"><p:cSld/>'
            b'<p:transition><p:wipe/></p:transition><p:extLst/></p:sld>'
        )
        faded = MODULE.replace_transition(source, "fade").decode("utf-8")
        self.assertNotIn("p:wipe", faded)
        self.assertEqual(faded.count("<p:transition"), 1)
        MODULE.validate_transition_order(faded.encode("utf-8"), "fade")
        cleared = MODULE.replace_transition(source, "none").decode("utf-8")
        self.assertNotIn("p:transition", cleared)
        MODULE.validate_transition_order(cleared.encode("utf-8"), "none")

    def test_nested_transition_is_relocated_to_slide_root(self):
        source = (
            b'<p:sld xmlns:p="urn:test"><p:cSld><p:spTree>'
            b'<p:transition><p:fade/></p:transition><p:extLst/>'
            b'</p:spTree></p:cSld><p:clrMapOvr/><p:extLst/></p:sld>'
        )
        result = MODULE.replace_transition(source, "fade")
        MODULE.validate_transition_order(result, "fade")
        self.assertEqual(MODULE.root_child_names(result), [
            "cSld", "clrMapOvr", "transition", "extLst"
        ])

    def test_patch_transitions_preserves_a_valid_zip(self):
        with tempfile.TemporaryDirectory() as name:
            deck = Path(name) / "test.pptx"
            with zipfile.ZipFile(deck, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Override PartName="/ppt/presentation.xml" '
                    f'ContentType="{MODULE.PPTX_CONTENT_TYPE}"/></Types>',
                )
                archive.writestr("ppt/presentation.xml", "<p:presentation xmlns:p=\"urn:test\"/>")
                archive.writestr(
                    "ppt/slides/slide1.xml",
                    '<p:sld xmlns:p="urn:test"><p:cSld/></p:sld>',
                )
            self.assertEqual(MODULE.patch_transitions(deck, "fade"), 1)
            with zipfile.ZipFile(deck) as archive:
                self.assertIsNone(archive.testzip())
                slide = archive.read("ppt/slides/slide1.xml")
                self.assertIn(b"<p:fade/>", slide)

    @patch.object(MODULE.subprocess, "call", return_value=0)
    def test_run_command_captures_utf8_via_temp_file(self, call):
        def write_sink(*_args, **kwargs):
            kwargs["stdout"].write("agent-browser 0.33.2\n")
            return 0

        call.side_effect = write_sink
        process = MODULE.run_command(["agent-browser", "--version"], timeout=5)
        self.assertEqual(process.returncode, 0)
        self.assertIn("0.33.2", process.stdout)
        self.assertEqual(call.call_args.kwargs["stderr"], MODULE.subprocess.STDOUT)

    def test_find_download_ignores_files_older_than_since(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            old = root / "old.pptx"
            new = root / "new.pptx"
            for path in (old, new):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(
                        "[Content_Types].xml",
                        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                        '<Override PartName="/ppt/presentation.xml" '
                        f'ContentType="{MODULE.PPTX_CONTENT_TYPE}"/></Types>',
                    )
                    archive.writestr("ppt/presentation.xml", "<p:presentation/>")

            older = time.time() - 60
            os.utime(old, (older, older))
            since = time.time() - 5
            found = MODULE.find_download([root], timeout=2.0, since=since)
            self.assertEqual(found.resolve(), new.resolve())

    def test_find_download_survives_files_vanishing_mid_scan(self):
        # Chrome renames "*.crdownload" files away between directory listing
        # and stat(); a vanished file must be skipped, not crash the export.
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            deck = root / "deck.pptx"
            with zipfile.ZipFile(deck, "w") as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Override PartName="/ppt/presentation.xml" '
                    f'ContentType="{MODULE.PPTX_CONTENT_TYPE}"/></Types>',
                )
                archive.writestr("ppt/presentation.xml", "<p:presentation/>")
            ghost = root / "ghost.crdownload"
            ghost.write_bytes(b"partial download")

            real_stat = Path.stat
            seen = {"count": 0}

            def racy_stat(self, **kwargs):
                if self.name == "ghost.crdownload":
                    seen["count"] += 1
                    if seen["count"] > 1:
                        raise FileNotFoundError(2, "vanished mid-scan", str(self))
                return real_stat(self, **kwargs)

            with patch.object(Path, "stat", racy_stat):
                found = MODULE.find_download([root], timeout=2.0)
            self.assertEqual(found.resolve(), deck.resolve())

    def test_browser_open_does_not_pass_download_path(self):
        session = MODULE.BrowserSession(
            "/bin/agent-browser",
            "test-session",
            Path("."),
            Path("/tmp/downloads"),
        )
        with patch.object(session, "run") as run:
            session.open("http://127.0.0.1:9/export_host.html")
        run.assert_called_once_with(
            ["open", "http://127.0.0.1:9/export_host.html"],
            timeout=90,
        )

    def test_ensure_debug_chrome_is_windows_only(self):
        with patch.object(MODULE.sys, "platform", "linux"):
            self.assertIsNone(MODULE.ensure_debug_chrome())

    @patch.object(MODULE, "cdp_alive", return_value=True)
    def test_ensure_debug_chrome_prefers_working_explicit_port(self, cdp_alive):
        with patch.object(MODULE.sys, "platform", "win32"), \
                patch.dict(MODULE.os.environ, {"AGENT_BROWSER_CDP": "9444"}):
            self.assertEqual(MODULE.ensure_debug_chrome(), 9444)
        cdp_alive.assert_called_once_with(9444)

    def test_browser_session_exports_cdp_port_to_env(self):
        with patch.dict(MODULE.os.environ, {}, clear=False):
            MODULE.os.environ.pop("AGENT_BROWSER_CDP", None)
            with_port = MODULE.BrowserSession(
                "/bin/agent-browser", "s", Path("."), Path("/tmp/d"), cdp_port=9337
            )
            self.assertEqual(with_port.env["AGENT_BROWSER_CDP"], "9337")
            without_port = MODULE.BrowserSession(
                "/bin/agent-browser", "s", Path("."), Path("/tmp/d")
            )
            self.assertNotIn("AGENT_BROWSER_CDP", without_port.env)


class ExportSafetyTests(unittest.TestCase):
    def write_deck(self, root, elements=None, background=None):
        (root / "pages").mkdir(parents=True)
        manifest = root / "deck.pptd"
        manifest.write_text(json.dumps({"version": "v2", "pages": ["pages/01.page"]}), encoding="utf-8")
        (root / "pages" / "01.page").write_text(json.dumps({
            "elements": elements or [], "background": background,
        }), encoding="utf-8")
        return manifest

    def make_pptx(self, path, count):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", f'<Types><Override ContentType="{MODULE.PPTX_CONTENT_TYPE}"/></Types>')
            archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="urn:test"/>')
            for number in range(1, count + 1):
                archive.writestr(f"ppt/slides/slide{number}.xml", '<p:sld xmlns:p="urn:test"><p:cSld/></p:sld>')

    def test_import_and_missing_dependencies_never_install(self):
        with patch.dict(sys.modules, {"yaml": None}), patch.object(MODULE, "run_command") as command:
            with self.assertRaisesRegex(MODULE.ExportError, "No packages were installed"):
                MODULE.ensure_pyyaml()
            spec = importlib.util.spec_from_file_location("export_pptx_no_yaml", SCRIPT)
            imported = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(imported)
        command.assert_not_called()

    def test_remote_consent_is_checked_before_any_browser_or_source_access(self):
        with patch.object(MODULE, "ensure_agent_browser") as browser, patch.object(MODULE, "find_manifest") as manifest:
            with self.assertRaisesRegex(MODULE.ExportError, "allow-remote-renderer"):
                MODULE.export_pptx(Path("missing.pptd"), Path("out.pptx"), "fade", True)
        browser.assert_not_called()
        manifest.assert_not_called()

    def test_hard_layout_error_stops_export_before_browser_or_output_creation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = self.write_deck(root, [{
                "elementType": "shape", "elementId": "outside",
                "bounds": [950, 20, 80, 80], "shapeType": "rect",
                "fill": "#000000",
            }])
            source = json.loads(manifest.read_text())
            source["size"] = [960, 540]
            manifest.write_text(json.dumps(source))
            output = root / "uncreated" / "deck.pptx"
            with patch.object(MODULE, "ensure_agent_browser") as browser:
                with self.assertRaisesRegex(MODULE.ExportError, "layout preflight failed"):
                    MODULE.export_pptx(manifest, output, "none", False, allow_remote_renderer=True)
            browser.assert_not_called()
            self.assertFalse(output.parent.exists())

    def test_review_only_layout_is_retained_without_visual_acceptance(self):
        report = {"status": "needs_review", "issues": [{"severity": "review", "code": "complex_shape"}]}
        with patch("layout_quality.audit_deck", return_value=report):
            result = MODULE.preflight_layout(Path("deck.pptd"))
        self.assertEqual(result["status"], "needs_review")
        self.assertNotIn("visual_review", result)

    def test_claimed_pass_cannot_override_a_hard_layout_issue(self):
        report = {"status": "passed", "issues": [{"severity": "error", "message": "text overflow"}]}
        with patch("layout_quality.audit_deck", return_value=report):
            with self.assertRaisesRegex(MODULE.ExportError, "text overflow"):
                MODULE.preflight_layout(Path("deck.pptd"))

    def test_payload_includes_only_declared_images_and_image_fills(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "media").mkdir()
            (root / ".qa-images").mkdir()
            for image in ("used.png", "background.png", "unrelated.png"):
                (root / "media" / image).write_bytes(b"fake image content")
            (root / ".qa-images" / "overview.jpg").write_bytes(b"old QA image")
            manifest = self.write_deck(
                root, [{"elementType": "image", "src": "media/used.png"}],
                {"type": "image", "src": "media/background.png"},
            )
            with patch.object(MODULE, "run_command") as command:
                payload = MODULE.build_payload(manifest)
            command.assert_not_called()
            self.assertEqual(set(payload["imageMap"]), {"media/used.png", "media/background.png"})

    def test_referenced_image_symlink_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root = base / "deck"
            root.mkdir()
            outside = base / "private.png"
            outside.write_bytes(b"private")
            try:
                (root / "leak.png").symlink_to(outside)
            except OSError:
                self.skipTest("file symlinks are not available")
            with self.assertRaisesRegex(MODULE.ExportError, "escapes"):
                MODULE.build_image_map(root, ["leak.png"])

    def test_missing_referenced_image_fails_instead_of_disappearing(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(MODULE.ExportError, "missing referenced image"):
                MODULE.build_image_map(Path(name), ["media/missing.png"])

    def test_remote_image_references_are_not_fetched_locally(self):
        with tempfile.TemporaryDirectory() as name, patch.object(MODULE.urllib.request, "urlopen") as network:
            self.assertEqual(MODULE.build_image_map(Path(name), ["https://example.invalid/hero.png"]), {})
        network.assert_not_called()

    def test_ambiguous_downloads_fail_without_selecting_or_deleting(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for filename in ["a.pptx", "b.pptx"]:
                self.make_pptx(root / filename, 1)
            with self.assertRaisesRegex(MODULE.ExportError, "ambiguous"):
                MODULE.find_download([root], timeout=1)
            self.assertEqual(len(list(root.iterdir())), 2)

    def test_zero_and_mismatched_slide_counts_fail_verification(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "deck.pptx"
            self.make_pptx(path, 0)
            with self.assertRaisesRegex(MODULE.ExportError, "zero slides"):
                MODULE.verify_output(path, "none", False)
            self.make_pptx(path, 1)
            with self.assertRaisesRegex(MODULE.ExportError, "source has 2"):
                MODULE.verify_output(path, "none", False, expected_slides=2)
            self.assertEqual(MODULE.verify_output(path, "none", False, expected_slides=1)["slides"], 1)

    def test_archive_member_and_total_size_bounds(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "large.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("a.bin", b"a" * 20)
                archive.writestr("b.bin", b"b" * 20)
            with zipfile.ZipFile(path) as archive, patch.object(MODULE, "MAX_ARCHIVE_MEMBER_BYTES", 16):
                with self.assertRaisesRegex(MODULE.ExportError, "member exceeds"):
                    MODULE.checked_archive_members(archive)
            with zipfile.ZipFile(path) as archive, patch.object(MODULE, "MAX_ARCHIVE_TOTAL_BYTES", 30):
                with self.assertRaisesRegex(MODULE.ExportError, "uncompressed limit"):
                    MODULE.checked_archive_members(archive)

    def test_download_isolation_uses_only_job_directory_and_requires_cdp_success(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session = MODULE.BrowserSession("browser", "job", root, root / "downloads")
            session.opened_url = "http://127.0.0.1:9999/export_host.html"
            connection = MagicMock()
            target_response = json.dumps({"id": 1, "result": {"targetInfos": [{
                "type": "page", "url": session.opened_url, "browserContextId": "job-context",
            }]}})
            connection.recv.side_effect = [target_response, json.dumps({"id": 2, "result": {}})]
            websocket = MagicMock()
            websocket.create_connection.return_value = connection
            command_result = MODULE.subprocess.CompletedProcess([], 0, "ws://127.0.0.1:9337/devtools/browser/test")
            with patch.object(session, "run", return_value=command_result), patch.object(MODULE, "ensure_websocket", return_value=websocket):
                session.configure_downloads()
                sent = json.loads(connection.send.call_args.args[0])
                self.assertEqual(sent["method"], "Browser.setDownloadBehavior")
                self.assertEqual(sent["params"]["downloadPath"], str((root / "downloads").resolve()))
                self.assertEqual(sent["params"]["browserContextId"], "job-context")
                connection.recv.side_effect = [target_response, json.dumps({"id": 2, "error": {"message": "unsupported"}})]
                with self.assertRaisesRegex(MODULE.ExportError, "download isolation failed"):
                    session.configure_downloads()


if __name__ == "__main__":
    unittest.main()
