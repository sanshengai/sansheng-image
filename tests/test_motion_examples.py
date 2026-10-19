"""The shipped examples retain real files and refuse damaged or existing inputs."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/motion"


class MotionExampleTests(unittest.TestCase):
    def test_shipped_complete_examples_match_recorded_bytes(self):
        manifest = json.loads((EXAMPLES / "manifest.json").read_text())
        self.assertEqual({row["method"] for row in manifest["examples"]},
                         {"atmosphere", "material", "explanation"})
        for row in manifest["examples"]:
            self.assertEqual((row["width"], row["height"], row["fps"], row["duration"]),
                             (1280, 720, 24, 8))
            for name in ("index.html", "director.json", "DIRECTOR.md", "timeline.jpg",
                         "assets/source.png", "assets/serif.otf"):
                self.assertIn(name, row["files"])
            for relative, expected in row["files"].items():
                file = EXAMPLES / row["name"] / relative
                self.assertTrue(file.is_file())
                self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), expected)

    def test_prepare_rejects_changed_empty_and_missing_input_before_init(self):
        # Exercise the actual consumer in a copy outside every live Skill scan tree.
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "examples/motion"
            source.mkdir(parents=True)
            shutil.copyfile(EXAMPLES / "prepare.py", source / "prepare.py")
            shutil.copyfile(EXAMPLES / "manifest.json", source / "manifest.json")
            shutil.copytree(EXAMPLES / "rainwater", source / "rainwater")
            file = source / "rainwater/assets/source.png"
            original = file.read_bytes()
            for value in (b"changed source", b"", None):
                with self.subTest(value=value):
                    if value is None:
                        file.unlink()
                    else:
                        file.write_bytes(value)
                    out = base / "output"
                    result = subprocess.run([sys.executable, str(source / "prepare.py"),
                                             "rainwater", "--out", str(out)],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("bundled input changed or missing", result.stderr)
                    self.assertFalse(out.exists())
                    file.write_bytes(original)

    def test_prepare_never_overwrites_existing_project(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "existing"
            out.mkdir()
            marker = out / "index.html"
            marker.write_text("keep existing work")
            result = subprocess.run([sys.executable, str(EXAMPLES / "prepare.py"),
                                     "rainwater", "--out", str(out)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("output already exists", result.stderr)
            self.assertEqual(marker.read_text(), "keep existing work")
