import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from layout_engine import build_deck, main, select_font
from layout_quality import FontResolver, LayoutError, audit_deck, glyphs, missing_glyphs, sha256, text_width, wrap_text
from layout_quality import main as quality_main


def sample_spec():
    return {"version": 1, "title": "A measured presentation", "slide_count": 6, "slides": [
        {"layout": "cover", "title": "A measured next step", "subtitle": "Content stays editable", "eyebrow": "PROJECT REVIEW"},
        {"layout": "bullets", "title": "Focus on three decisions", "bullets": ["Choose the audience and the decision.", "Keep one main idea on each slide.", "Inspect the exported presentation."], "source": "Source: supplied project brief"},
        {"layout": "two_column", "title": "Connect evidence and action", "columns": [
            {"heading": "Evidence", "bullets": ["State the source.", "Show the comparison."]},
            {"heading": "Action", "bullets": ["Assign an owner.", "Set a review date."]}]},
        {"layout": "metrics", "title": "Track the outcome", "metrics": [
            {"value": "12", "label": "Reviewed items", "detail": "Illustrative input"},
            {"value": "4", "label": "Open questions", "detail": "Illustrative input"},
            {"value": "2", "label": "Next steps", "detail": "Illustrative input"}]},
        {"layout": "table", "title": "Make ownership visible", "headers": ["Workstream", "Owner", "Next review"],
         "rows": [["Research", "Team A", "Monday"], ["Design", "Team B", "Tuesday"], ["Review", "Team C", "Friday"]]},
        {"layout": "image", "title": "Use a relevant source image", "image": "image.png", "body": "Labels and body text remain editable."},
    ]}


class FontCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.family, cls.font_policy = select_font("A measured presentation 123")
        except LayoutError as exc:
            raise unittest.SkipTest(str(exc))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class FontAndWrapTests(FontCase):
    def test_long_english_and_unbroken_words_fit_without_losing_characters(self):
        font, scale = FontResolver(self.root, self.font_policy).font(self.family, 24)
        for text in ("This sentence contains many useful words that should wrap.", "x"*180):
            lines = wrap_text(text, font, 150, scale)
            self.assertGreater(len(lines), 1)
            self.assertEqual("".join(lines).replace(" ", ""), text.replace(" ", ""))
            self.assertTrue(all(text_width(line, font, scale) <= 150 for line in lines))

    def test_cjk_explicit_breaks_and_combining_clusters_are_preserved(self):
        font, scale = FontResolver(self.root, self.font_policy).font(self.family, 24)
        text = "中文排版需要稳定换行。\n\n第二段内容。"
        lines = wrap_text(text, font, 100, scale)
        self.assertIn("", lines)
        self.assertEqual("".join(lines), text.replace("\n", ""))
        self.assertEqual(glyphs("e\u0301a"), ["e\u0301", "a"])
        # Wrapping support is independent from actual font coverage; absent glyphs fail production.
        self.assertTrue(missing_glyphs(chr(0x10FFFD), font))

    def test_explicit_missing_family_and_face_substitution_are_rejected(self):
        with self.assertRaisesRegex(LayoutError, "No exact usable"):
            select_font("Hello", {"family": "Definitely Not Installed Font 987654321"}, self.root)
        files = self.font_policy["fonts"][self.family]
        with self.assertRaisesRegex(LayoutError, "style substitution"):
            select_font("Hello", {"family": self.family, "regular": files["regular"], "bold": files["regular"]}, self.root)


class BuilderTests(FontCase):
    def make_spec(self):
        spec = sample_spec()
        spec["font"] = {"family": self.family, **self.font_policy["fonts"][self.family]}
        return spec

    def test_all_six_templates_pass_all_palettes_and_project_validation(self):
        from pptd_project import load_project
        Image.new("RGB", (320, 240), "#ABCDEF").save(self.root / "image.png")
        for palette in ("warm", "neutral", "dark"):
            with self.subTest(palette=palette):
                spec = self.make_spec()
                spec["style"] = palette
                output = self.root / palette
                report = build_deck(spec, output, self.root)
                self.assertEqual((report["status"], report["slide_count"]), ("passed", 6))
                self.assertEqual(report["issues"], [])
                self.assertEqual(report, audit_deck(output / "deck.pptd"))
                self.assertEqual(report["visual_review"], "not_performed")
                self.assertTrue(all(f["verified"] and len(f["sha256"]) == 64 for f in report["fonts"]))
                self.assertIn("run/layout-spec.json", {f["path"] for f in report["files"]})
                self.assertTrue((output / "pages/01.page").is_file())
                load_project(output / "deck.pptd")

    def test_initialized_project_is_preserved_and_existing_sources_are_not_overwritten(self):
        output = self.root / "project"
        (output / "run").mkdir(parents=True)
        (output / "pages").mkdir()
        marker = output / "run/state.json"
        marker.write_text('{"user_state":true}')
        spec = {"title": "Deck", "slides": [{"layout": "cover", "title": "Editable source"}]}
        build_deck(spec, output, self.root)
        before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(LayoutError, "Refusing"):
            build_deck(spec, output, self.root)
        after = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(marker.read_text(), '{"user_state":true}')

    def test_existing_policy_causes_clean_failure(self):
        output = self.root / "project"
        (output / "run").mkdir(parents=True)
        (output / "run/layout-policy.json").write_text("user policy")
        spec = {"title": "Deck", "slides": [{"layout": "cover", "title": "Editable source"}]}
        with self.assertRaisesRegex(LayoutError, "overwrite"):
            build_deck(spec, output, self.root)
        self.assertEqual([p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()], ["run/layout-policy.json"])

    def test_dense_content_fails_without_shrinking_truncating_or_adding_pages(self):
        spec = {"title": "Dense", "slide_count": 1, "slides": [{"layout": "bullets", "title": "Too much content", "bullets": ["A lengthy explanation "*70]*4}]}
        output = self.root / "dense"
        with self.assertRaisesRegex(LayoutError, "Split or rewrite"):
            build_deck(spec, output, self.root)
        self.assertFalse(output.exists())
        self.assertEqual(len(spec["slides"]), 1)

    def test_invalid_input_and_page_count_fail_without_output(self):
        base = {"title": "Deck", "slides": [{"layout": "cover", "title": "Cover"}]}
        cases = [[], {**base, "slide_count": 2}, {**base, "style": "unknown"}, {**base, "model": "a model"},
                 {**base, "slides": [{"layout": "unsupported", "title": "Cover"}]}]
        for i, spec in enumerate(cases):
            with self.subTest(spec=spec), self.assertRaises(LayoutError):
                build_deck(spec, self.root / f"invalid-{i}", self.root)
            self.assertFalse((self.root / f"invalid-{i}").exists())

    def test_asset_path_escape_and_missing_glyph_fail(self):
        for slide in ({"layout": "image", "title": "Image", "image": "../secret.png"},
                      {"layout": "cover", "title": "Missing " + chr(0x10FFFD)}):
            with self.assertRaises(LayoutError):
                build_deck({"title": "Deck", "slides": [slide]}, self.root / "invalid", self.root)
            self.assertFalse((self.root / "invalid").exists())

    def test_falsey_optional_content_is_rejected_instead_of_dropped(self):
        slides = [{"layout": "cover", "title": "Cover", field: value} for field, value in (("subtitle", 0), ("eyebrow", False), ("source", ""))]
        slides += [{"layout": "image", "title": "Image", "image": "image.png", "body": 0},
                   {"layout": "metrics", "title": "Metrics", "metrics": [{"value": "1", "label": "One", "detail": 0}, {"value": "2", "label": "Two"}]}]
        for slide in slides:
            with self.subTest(slide=slide), self.assertRaises(LayoutError):
                build_deck({"title": "Deck", "slides": [slide]}, self.root / "invalid", self.root)
            self.assertFalse((self.root / "invalid").exists())

    def test_identical_spec_produces_identical_sources_and_report(self):
        spec = {"title": "Repeatable", "slides": [{"layout": "bullets", "title": "A repeatable layout", "bullets": ["Readable typography.", "Measured geometry."]}]}
        reports = [build_deck(spec, self.root / name, self.root) for name in ("first", "second")]
        self.assertEqual(reports[0], reports[1])
        self.assertEqual((self.root / "first/run/layout-qa.json").read_bytes(), (self.root / "second/run/layout-qa.json").read_bytes())

    def test_help_does_not_discover_fonts_or_install_dependencies(self):
        with patch("layout_quality.subprocess.run", side_effect=AssertionError("external command")), patch("sys.stdout", new=io.StringIO()), self.assertRaises(SystemExit) as result:
            main(["--help"])
        self.assertEqual(result.exception.code, 0)


class AuditTests(FontCase):
    def setUp(self):
        super().setUp()
        (self.root / "pages").mkdir()
        (self.root / "run").mkdir()
        self.policy = copy.deepcopy(self.font_policy)
        self.policy["safe_margin"] = 24
        self.policy["element_roles"] = {}
        self.page = {"background": {"type": "solid", "color": "#FFFFFF"}, "elements": [self.text()]}
        (self.root / "deck.pptd").write_text(yaml.safe_dump({"version": "v2", "title": "Audit", "size": [960, 540], "pages": ["pages/01.page"]}))

    def text(self, eid="body", bounds=None, text="Measured text", **style):
        return {"elementId": eid, "elementType": "text", "bounds": bounds or [48, 48, 400, 80],
                "content": {"fontFamily": self.family, "fontSize": 24, "lineHeight": 1.2, "color": "#111111", "text": text, **style}}

    def audit(self):
        (self.root / "pages/01.page").write_text(yaml.safe_dump(self.page, allow_unicode=True))
        (self.root / "run/layout-policy.json").write_text(json.dumps(self.policy))
        return audit_deck(self.root / "deck.pptd")

    def codes(self, report):
        return {issue["code"] for issue in report["issues"]}

    def test_plain_text_and_intentional_card_hierarchy_pass(self):
        self.assertEqual(self.audit()["status"], "passed")
        self.page["elements"].insert(0, {"elementId": "card", "elementType": "shape", "shapeName": "rect", "bounds": [32, 32, 440, 120], "fill": {"type": "solid", "color": "#F4F4F4"}})
        self.assertEqual(self.audit()["status"], "passed")

    def test_curved_shape_bbox_is_not_treated_as_a_solid_background(self):
        self.page["elements"] = [{"elementId": "oval", "elementType": "shape", "shapeName": "ellipse", "bounds": [48, 48, 300, 300], "fill": {"type": "solid", "color": "#000000"}}, self.text(bounds=[52, 52, 150, 40], text="INVISIBLE", color="#FFFFFF")]
        report = self.audit()
        self.assertEqual(report["status"], "needs_review")
        self.assertIn("contrast_unknown", self.codes(report))
        self.assertIn("shape_content_boundary", self.codes(report))

    def test_vertical_text_requires_review_and_shape_embedded_text_is_rejected(self):
        self.page["elements"] = [self.text(bounds=[48, 48, 200, 80], text="ABCDEFGHIJK", textDirection="vertical")]
        self.assertEqual(self.audit()["status"], "needs_review")
        self.page["elements"] = [{"elementId": "bad_shape", "elementType": "shape", "shapeName": "rect", "bounds": [48, 48, 300, 300], "content": {"text": "Must not disappear"}}]
        self.assertEqual(self.audit()["status"], "failed")
        self.assertIn("shape_embedded_text", self.codes(self.audit()))

    def test_text_text_and_text_image_collisions_are_hard_errors(self):
        self.page["elements"].append(self.text("other", [300, 64, 200, 80]))
        self.assertIn("content_overlap", self.codes(self.audit()))
        Image.new("RGB", (20, 20), "white").save(self.root / "image.png")
        self.page["elements"][-1] = {"elementId": "image", "elementType": "image", "bounds": [300, 64, 200, 80], "src": "image.png", "fit": {"mode": "contain"}}
        self.assertEqual(self.audit()["status"], "failed")
        self.assertIn("content_overlap", self.codes(self.audit()))

    def test_covering_foreground_shape_is_not_mistaken_for_background(self):
        self.page["elements"].append({"elementId": "overlay", "elementType": "shape", "shapeName": "rect", "bounds": [32, 32, 440, 120], "fill": {"type": "solid", "color": "#FFFFFF"}})
        self.assertIn("content_overlap", self.codes(self.audit()))

    def test_nonfinite_negative_outside_duplicate_and_margin_violations(self):
        examples = [("invalid_bounds", [48, float("nan"), 400, 80]), ("invalid_dimensions", [48, 48, -4, 80]),
                    ("out_of_bounds", [900, 48, 400, 80]), ("safe_margin", [10, 48, 400, 80])]
        for code, bounds in examples:
            with self.subTest(code=code):
                self.page["elements"] = [self.text(bounds=bounds)]
                self.assertIn(code, self.codes(self.audit()))
        self.page["elements"] = [self.text(), self.text(bounds=[48, 200, 400, 80])]
        self.assertIn("duplicate_id", self.codes(self.audit()))

    def test_html_font_metrics_detect_overflow_and_small_text(self):
        self.page["elements"] = [self.text(text='<p><span style="font-size:64px">Too much large content to fit safely</span></p>')]
        self.assertIn("text_overflow", self.codes(self.audit()))
        self.page["elements"] = [self.text(fontSize=12)]
        self.assertIn("small_text", self.codes(self.audit()))

    def test_no_wrap_and_highlight_background_are_honoured(self):
        self.page["elements"] = [self.text(bounds=[48, 48, 400, 400], text="A long text line "*10, wrap=False)]
        self.assertIn("text_overflow", self.codes(self.audit()))
        self.page["background"]["color"] = "#000000"
        self.page["elements"] = [self.text(color="#FFFFFF", backgroundColor="#FFFFFF")]
        self.assertIn("low_contrast", self.codes(self.audit()))

    def test_theme_reference_resolves_exact_font_and_styles(self):
        manifest = yaml.safe_load((self.root / "deck.pptd").read_text())
        manifest["theme"] = {"textStyles": {"body": {"fontFamily": self.family, "fontSize": 24, "color": "#111111"}}}
        (self.root / "deck.pptd").write_text(yaml.safe_dump(manifest))
        self.page["elements"][0]["content"] = {"style": "$body", "text": "A theme-based paragraph"}
        self.assertEqual(self.audit()["status"], "passed")
        manifest["theme"]["textStyles"]["large"] = {"fontSize": 80}
        (self.root / "deck.pptd").write_text(yaml.safe_dump(manifest))
        self.page["elements"] = [self.text(bounds=[48, 48, 150, 35])]
        self.page["elements"][0]["content"] = {"style": "$large", "fontFamily": self.family, "text": "ABC"}
        self.assertIn("text_overflow", self.codes(self.audit()))

    def test_low_contrast_and_missing_font_or_glyph_fail(self):
        for style, text, code in (({"color": "#FFFFFF"}, "White on white", "low_contrast"),
                                  ({"fontFamily": "Missing Font 987654321"}, "Wrong face", "font_or_text_measurement"),
                                  ({}, chr(0x10FFFD), "missing_glyph")):
            with self.subTest(code=code):
                self.page["elements"] = [self.text(text=text, **style)]
                report = self.audit()
                self.assertEqual(report["status"], "failed")
                self.assertIn(code, self.codes(report))

    def test_unsupported_constructs_and_declared_overlap_require_stable_review(self):
        self.page["elements"].append({"elementId": "chart", "elementType": "chart", "bounds": [500, 150, 400, 300]})
        first, second = self.audit(), self.audit()
        self.assertEqual(first["status"], "needs_review")
        self.assertEqual(first["issues"], second["issues"])
        self.assertEqual(first["issues"][0]["page"], 1)
        self.assertEqual(first["issues"][0]["id"], "p001:chart:unsupported_element")
        self.page["elements"][-1] = self.text("overlay", [300, 64, 200, 80])
        self.policy["intentional_overlaps"] = [{"page": 1, "elements": ["body", "overlay"], "reason": "Planned overlay"}]
        self.assertEqual(self.audit()["status"], "needs_review")
        self.assertIn("intentional_overlap", self.codes(self.audit()))

    def test_source_and_font_fingerprints_are_current(self):
        first = self.audit()
        self.page["elements"][0]["content"]["text"] = "Changed content"
        second = self.audit()
        a, b = ({f["path"]: f["sha256"] for f in r["files"]} for r in (first, second))
        self.assertNotEqual(a["pages/01.page"], b["pages/01.page"])
        self.assertEqual(first["policy_sha256"], second["policy_sha256"])
        self.assertTrue(all(sha256(Path(f["path"])) == f["sha256"] for f in second["fonts"]))

    def test_quality_cli_check_alias_and_direct_form_use_strict_status_codes(self):
        self.audit()
        manifest, output = self.root / "deck.pptd", self.root / "report.json"
        self.assertEqual(quality_main(["check", str(manifest), "--output", str(output)]), 0)
        with patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(quality_main([str(manifest)]), 0)
        self.page["elements"].append({"elementId": "chart", "elementType": "chart", "bounds": [500, 150, 400, 300]})
        self.audit()
        self.assertEqual(quality_main(["check", str(manifest), "--output", str(output)]), 1)
        self.assertEqual(json.loads(output.read_text())["status"], "needs_review")
        self.page["elements"][0]["bounds"][2] = -1
        self.audit()
        self.assertEqual(quality_main(["check", str(manifest), "--output", str(output)]), 1)
        self.assertEqual(json.loads(output.read_text())["status"], "failed")
        with patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(quality_main(["check", str(self.root / "absent.pptd")]), 2)


if __name__ == "__main__":
    unittest.main()
