#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("export_images", SCRIPTS_DIR / "export_images.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def make_images_zip(path: Path, names=("1.jpeg", "10.jpeg", "2.jpeg")) -> None:
    # 1x1 white JPEG, the smallest valid payload Pillow can open.
    import base64

    pixel = base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////"
        "////////////////////////////////////////////2wBDAf//////////////////"
        "////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB"
        "/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMB"
        "AAIQAxAAAAGf/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAA"
        "AAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgB"
        "AgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAA"
        "AAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABCf/8QAFBEBAAAAAAAAAAAA"
        "AAAAAAAAAP/aAAgBAwEPEBB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEPEBB/"
        "/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, pixel)


class ExportImagesTests(unittest.TestCase):
    def test_page_sort_key_orders_numeric_stems(self):
        paths = [Path("10.jpeg"), Path("2.jpeg"), Path("cover.jpeg"), Path("1.jpeg")]
        ordered = sorted(paths, key=MODULE.page_sort_key)
        self.assertEqual(
            [path.name for path in ordered],
            ["1.jpeg", "2.jpeg", "10.jpeg", "cover.jpeg"],
        )

    def test_is_image_zip_accepts_image_entries_only(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            good = root / "images.zip"
            make_images_zip(good)
            self.assertTrue(MODULE.is_image_zip(good))

            bad = root / "text.zip"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("readme.txt", "hello")
            self.assertFalse(MODULE.is_image_zip(bad))
            self.assertFalse(MODULE.is_image_zip(root / "missing.zip"))

    def test_unzip_images_flattens_and_sorts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(archive_path, names=("1.jpeg", "10.jpeg", "2.jpeg", "note.txt"))
            images = MODULE.unzip_images(archive_path, root / "pages")
            self.assertEqual(
                [path.name for path in images], ["1.jpeg", "2.jpeg", "10.jpeg"]
            )

    def test_stitch_overview_grid(self):
        try:
            image_cls, draw_cls, image_font = MODULE.ensure_pillow()
        except MODULE.ExportError:
            self.skipTest("Pillow is not available")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            images = []
            for index in range(1, 5):
                path = root / f"{index}.jpeg"
                image = image_cls.new("RGB", (320, 180), (index * 40 % 255, 30, 60))
                image.save(path, "JPEG")
                images.append(path)
            overview = MODULE.stitch_overview(
                images, root / "overview.jpg", image_cls, draw_cls, image_font
            )
            self.assertTrue(overview.is_file())
            with image_cls.open(overview) as result:
                self.assertEqual(
                    result.width,
                    3 * MODULE.OVERVIEW_THUMB_WIDTH + 4 * MODULE.OVERVIEW_GAP,
                )
                rows = 2
                cell = MODULE.OVERVIEW_LABEL_HEIGHT + 360
                self.assertEqual(result.height, rows * cell + (rows + 1) * MODULE.OVERVIEW_GAP)


class ImageExportSafetyTests(unittest.TestCase):
    def source(self, root):
        root.mkdir(exist_ok=True)
        (root / "pages").mkdir()
        (root / "media").mkdir()
        manifest = root / "deck.pptd"
        manifest.write_text("source", encoding="utf-8")
        (root / "pages" / "1.page").write_text("page", encoding="utf-8")
        (root / "media" / "hero.png").write_bytes(b"image")
        payload = {"pages": [{"path": "pages/1.page"}], "imageMap": {"media/hero.png": "image"}}
        return manifest, payload

    def owned_output(self, directory, manifest, content="old"):
        directory.mkdir()
        (directory / "pages").mkdir()
        (directory / "pages" / "1.jpeg").write_text(content, encoding="utf-8")
        (directory / "overview.jpg").write_text(content, encoding="utf-8")
        (directory / MODULE.QA_MARKER).write_text(json.dumps({
            "owner": "open-kimi-ppt", "schema": 1,
            "manifest": str(manifest.resolve()), "files": ["pages/1.jpeg", "overview.jpg"],
        }), encoding="utf-8")

    def test_force_rejects_source_directories_and_ancestors(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root = base / "deck"
            manifest, payload = self.source(root)
            for output in [root, base, root / "pages", root / "media"]:
                with self.subTest(output=output), self.assertRaisesRegex(MODULE.ExportError, "source"):
                    MODULE.validate_qa_output(output, manifest, payload, True)
            self.assertEqual(manifest.read_text(), "source")
            self.assertTrue((root / "pages" / "1.page").exists())

    def test_force_rejects_unowned_output_without_deleting_anything(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest, payload = self.source(root)
            output = root / ".qa-images"
            output.mkdir()
            (output / "valuable.txt").write_text("keep me", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ExportError, "unowned"):
                MODULE.validate_qa_output(output, manifest, payload, True)
            self.assertEqual((output / "valuable.txt").read_text(), "keep me")

    def test_owned_output_with_added_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest, payload = self.source(root)
            output = root / ".qa-images"
            self.owned_output(output, manifest)
            (output / "user-note.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ExportError, "added or missing"):
                MODULE.validate_qa_output(output, manifest, payload, True)
            self.assertTrue((output / "user-note.txt").exists())

    def test_completed_qa_run_replaces_owned_output_only_after_staging(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest, payload = self.source(root)
            output = root / ".qa-images"
            staging = root / ".staging-test"
            self.owned_output(output, manifest, "old")
            self.owned_output(staging, manifest, "new")
            MODULE.publish_qa_output(staging, output, manifest, payload, True)
            self.assertEqual((output / "overview.jpg").read_text(), "new")
            self.assertFalse(staging.exists())
            self.assertFalse(list(root.glob("*.previous-*")))
            self.assertEqual(manifest.read_text(), "source")

    def test_failed_staging_commit_restores_previous_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest, payload = self.source(root)
            output = root / ".qa-images"
            staging = root / ".staging-test"
            self.owned_output(output, manifest, "old")
            self.owned_output(staging, manifest, "new")
            original_rename = Path.rename

            def failing_rename(path, destination):
                if path == staging:
                    raise OSError("simulated commit failure")
                return original_rename(path, destination)

            with patch.object(Path, "rename", failing_rename), self.assertRaisesRegex(OSError, "commit failure"):
                MODULE.publish_qa_output(staging, output, manifest, payload, True)
            self.assertEqual((output / "overview.jpg").read_text(), "old")
            self.assertTrue(staging.exists())

    def test_duplicate_flattened_image_names_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(archive_path, ("first/1.jpeg", "second/1.jpeg"))
            with self.assertRaisesRegex(MODULE.ExportError, "duplicate flattened"):
                MODULE.unzip_images(archive_path, root / "pages", expected_pages=2)
            self.assertFalse((root / "pages").exists())

    def test_rendered_count_and_order_must_match_source(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(archive_path, ("1.jpeg",))
            with self.assertRaisesRegex(MODULE.ExportError, "does not match"):
                MODULE.unzip_images(archive_path, root / "pages", expected_pages=2)
            make_images_zip(archive_path, ("cover.jpeg", "summary.jpeg"))
            with self.assertRaisesRegex(MODULE.ExportError, "unambiguous page order"):
                MODULE.unzip_images(archive_path, root / "pages", expected_pages=2)
            make_images_zip(archive_path, ("page-2.jpeg", "page-1.jpeg"))
            images = MODULE.unzip_images(archive_path, root / "pages", expected_pages=2)
            self.assertEqual([path.name for path in images], ["page-1.jpeg", "page-2.jpeg"])

    def test_remote_consent_blocks_all_browser_work(self):
        with patch.object(MODULE, "ensure_agent_browser") as browser, patch.object(MODULE, "find_manifest") as manifest:
            with self.assertRaisesRegex(MODULE.ExportError, "allow-remote-renderer"):
                MODULE.export_images(Path("missing.pptd"), Path("qa"))
        browser.assert_not_called()
        manifest.assert_not_called()

    def test_pillow_is_not_automatically_installed(self):
        with patch.dict(sys.modules, {"PIL": None}), patch.object(MODULE, "run_command") as command:
            with self.assertRaisesRegex(MODULE.ExportError, "No packages were installed"):
                MODULE.ensure_pillow()
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
