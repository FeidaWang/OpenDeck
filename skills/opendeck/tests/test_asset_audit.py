import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "asset_audit.py"
SPEC = importlib.util.spec_from_file_location("asset_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class AssetAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "media").mkdir()

    def image(self, name="asset.png", mode="RGB", color="red", size=(128, 96)):
        path = self.root / "media" / name
        Image.new(mode, size, color).save(path)
        return path

    def test_raster_dimensions_and_hash_describe_actual_bytes(self):
        path = self.image()
        result = audit.audit_asset("media/asset.png", self.root)
        self.assertEqual((result["format"], result["width"], result["height"], result["frames"]), ("png", 128, 96, 1))
        self.assertEqual(result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertFalse(result["has_transparency"])
        self.assertEqual(result["visual_review"], "pending")

    def test_actual_alpha_values_distinguish_partial_and_full_transparency(self):
        for alpha in (0, 96, 255):
            path = self.image(mode="RGBA", color=(40, 80, 120, alpha))
            result = audit.audit_asset(path, self.root)
            self.assertEqual(result["has_transparency"], alpha < 255)
            self.assertEqual(result["has_fully_transparent_pixels"], alpha == 0)

    def test_exif_orientation_is_accounted_for_without_mutating_source(self):
        path = self.root / "media" / "rotated.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (128, 96), "red").save(path, exif=exif)
        original = path.read_bytes()
        result = audit.audit_asset(path, self.root)
        self.assertEqual((result["width"], result["height"]), (96, 128))
        self.assertEqual(path.read_bytes(), original)

    def test_animation_frame_count_is_measured_and_visual_review_stays_pending(self):
        path = self.root / "media" / "animation.gif"
        first = Image.new("RGB", (32, 32), "red")
        second = Image.new("RGB", (32, 32), "blue")
        first.save(path, save_all=True, append_images=[second], duration=100, loop=0)
        result = audit.audit_asset(path, self.root)
        self.assertEqual(result["format"], "gif")
        self.assertEqual(result["frames"], 2)
        self.assertEqual(result["visual_review"], "pending")

    def test_path_traversal_absolute_escape_and_escaping_symlink_fail(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "outside.png"
            Image.new("RGB", (16, 16), "red").save(path)
            link = self.root / "media" / "escape.png"
            link.symlink_to(path)
            for candidate in (path, link, Path("..") / Path(outside).name / "outside.png"):
                with self.subTest(candidate=candidate), self.assertRaises(audit.AssetAuditError):
                    audit.audit_asset(candidate, self.root)

    def test_text_named_as_png_does_not_pass_as_image(self):
        path = self.root / "media" / "pretend.png"
        path.write_text("not an image")
        with self.assertRaises(audit.AssetAuditError):
            audit.audit_asset(path, self.root)

    def test_file_extension_does_not_override_detected_raster_format(self):
        path = self.root / "media" / "actually-jpeg.png"
        Image.new("RGB", (32, 24), "red").save(path, format="JPEG")
        self.assertEqual(audit.audit_asset(path, self.root)["format"], "jpeg")

    def test_truncated_raster_fails_without_mutating_file(self):
        path = self.image()
        truncated = path.read_bytes()[:40]
        path.write_bytes(truncated)
        with self.assertRaises(audit.AssetAuditError):
            audit.audit_asset(path, self.root)
        self.assertEqual(path.read_bytes(), truncated)

    def test_asset_byte_and_decoded_pixel_budgets_are_enforced(self):
        path = self.image()
        with mock.patch.object(audit, "MAX_ASSET_BYTES", 16):
            with self.assertRaises(audit.AssetAuditError):
                audit.audit_asset(path, self.root)
        with mock.patch.object(audit, "MAX_RASTER_PIXELS", 100):
            with self.assertRaises(audit.AssetAuditError):
                audit.audit_asset(path, self.root)

    def test_svg_rejects_active_external_and_embedded_content(self):
        path = self.root / "media" / "icon.svg"
        for inner in ('<script>alert(1)</script>', '<image href="https://example.com/x.png"/>',
                      '<use href="data:image/svg+xml,..."/>', '<path onclick="alert(1)"/>',
                      '<style>@import "https://example.com/x.css";</style>',
                      '<rect fill="url(https://example.com/x.svg)"/>', '<foreignObject/>',
                      '<use href="//example.com/x.svg"/>'):
            path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{inner}</svg>')
            with self.subTest(inner=inner), self.assertRaises(audit.AssetAuditError):
                audit.audit_asset(path, self.root)
        path.write_text('<!DOCTYPE svg [<!ENTITY leak SYSTEM "file:///etc/passwd">]><svg viewBox="0 0 1 1"/>')
        with self.assertRaises(audit.AssetAuditError):
            audit.audit_asset(path, self.root)

    def test_static_svg_has_dimensions_but_does_not_claim_actual_transparency(self):
        path = self.root / "media" / "icon.svg"
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 32"><path d="M0 0L64 32"/></svg>')
        result = audit.audit_asset(path, self.root)
        self.assertEqual((result["width"], result["height"]), (64, 32))
        self.assertIsNone(result["has_transparency"])
        self.assertEqual(result["transparency_status"], "requires_render")
        self.assertEqual(result["visual_review"], "pending")

    def test_invalid_svg_dimensions_and_foreign_namespace_fail(self):
        path = self.root / "media" / "icon.svg"
        for svg in ('<svg viewBox="0 0 NaN 32"/>', '<svg viewBox="0 0 -64 32"/>',
                    '<svg width="64" height="32" viewBox="0 0 NaN 32"/>',
                    '<svg width="64" height="32" viewBox="0 0 -64 32"/>',
                    '<svg width="0" height="24"/>', '<svg xmlns="https://unrelated.example/svg" viewBox="0 0 64 32"/>'):
            path.write_text(svg)
            with self.subTest(svg=svg), self.assertRaises(audit.AssetAuditError):
                audit.audit_asset(path, self.root)

    def test_record_keeps_declared_provenance_separate_from_verified_bytes(self):
        path = self.image()
        record = audit.record_asset(path, self.root, "User-supplied photograph")
        self.assertEqual(record["source_verification"], "operator_supplied_unverified")
        self.assertEqual(record["visual_review"], "pending")
        self.assertEqual(record["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        unknown = audit.record_asset(path, self.root)
        self.assertEqual(unknown["source_verification"], "unknown")
        self.assertNotIn("model", record)
        self.assertNotIn("request", record)

    def test_record_rejects_empty_origin(self):
        path = self.image()
        with self.assertRaises(audit.AssetAuditError):
            audit.record_asset(path, self.root, " ")

    def test_cli_writes_only_new_output_file(self):
        path = self.image()
        output = self.root / "asset-report.json"
        args = ["audit", "--asset", str(path), "--project-root", str(self.root), "--output", str(output)]
        self.assertEqual(audit.main(args), 0)
        original = output.read_bytes()
        self.assertEqual(json.loads(original)["visual_review"], "pending")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(audit.main(args), 2)
        self.assertEqual(output.read_bytes(), original)

    def test_cli_exposes_only_local_audit_and_record(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            audit.main(["compile"])
        self.assertEqual(caught.exception.code, 2)
        result = subprocess.run([sys.executable, "-S", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("audit", result.stdout)
        self.assertIn("record", result.stdout)


if __name__ == "__main__":
    unittest.main()
