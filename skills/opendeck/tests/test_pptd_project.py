import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import pptd_project as project

FIXTURE = Path(__file__).parent / "fixtures/minimal/minimal.pptd"


class ProjectReadingTests(unittest.TestCase):
    def test_minimal_fixture_loads_without_images(self):
        loaded = project.load_project(FIXTURE)
        self.assertEqual(len(loaded["pages"]), 1)
        self.assertEqual(loaded["images"], [])

    def test_paths_cannot_leave_the_project(self):
        with tempfile.TemporaryDirectory() as temp:
            for bad in ("../x", "/etc/passwd", "C:/x", "a/../../x", "", "a\x00b"):
                with self.subTest(path=bad), self.assertRaises(project.ProjectError):
                    project.safe_project_path(Path(temp), bad)

    def test_missing_unsupported_and_remote_schemes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "note.txt").write_text("x")
            for ref in ("missing.png", "note.txt", "ftp://host/a.png", "file:///etc/passwd"):
                with self.subTest(ref=ref), self.assertRaises(project.ProjectError):
                    project.local_images(root, [ref])
            self.assertEqual(project.local_images(root, ["https://example.com/a.png", "data:image/png;base64,AA=="]), [])

    def test_a_manifest_with_no_pages_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "deck.pptd"
            manifest.write_text("version: v2\ntitle: T\npages: []\n")
            with self.assertRaises(project.ProjectError):
                project.load_project(manifest)


class NoRemoteChannelTests(unittest.TestCase):
    """This package must not carry code that sends a deck to a third-party page or writes to Downloads."""

    def test_scripts_contain_no_remote_renderer_or_download_folder_code(self):
        banned = ("kimi.com", "moonshot.cn", "agent-browser", "agent_browser", "allow-remote-renderer",
                  "setDownloadBehavior", "websocket", "Downloads")
        for path in sorted((SKILL / "scripts").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for word in banned:
                with self.subTest(file=path.name, word=word):
                    self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
