"""Behavior checks for source preservation and the external renderer handoff."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("replay_wake", Path(__file__).parents[1] / "scripts/replay_wake.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class ReplayWakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "source with spaces"
        self.src.mkdir()
        (self.src / "index.html").write_text("prepared source")
        self.skill = self.root / "external wake"
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("external dependency")
        for name in ("render.mjs", "reset.py", "encode.py", "qa.py"):
            (self.skill / "scripts" / name).write_text("fixture; never execute")
        self.out = self.root / "new output"

    def run_cli(self, *extra):
        buf = io.StringIO()
        with patch.object(replay.shutil, "which", return_value="/tool"), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = replay.main([str(self.src), "--wake-skill", str(self.skill), "--out", str(self.out), *extra])
        return code, buf.getvalue()

    def test_plan_is_read_only_and_keeps_paths_as_arguments(self):
        code, output = self.run_cli("--plan", "--reset", "paper")
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        report = json.loads(output)
        self.assertEqual(report["commands"][0][2], str(self.out.resolve() / "src"))
        self.assertEqual((self.src / "index.html").read_text(), "prepared source")

    def test_actual_fps_is_shared_by_reset_encode_and_qa(self):
        code, output = self.run_cli("--plan", "--reset", "paper", "--fps", "24")
        self.assertEqual(code, 0)
        commands = json.loads(output)["commands"]
        timed = [c for c in commands if "--fps" in c]
        self.assertEqual(len(timed), 3)
        self.assertTrue(all(c[c.index("--fps") + 1] == "24.0" for c in timed))

    def test_invalid_fps_is_rejected_without_output(self):
        for fps in ("nan", "0", "121"):
            code, _ = self.run_cli("--fps", fps)
            self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())

    def test_other_upstream_transition_is_forwarded_without_shell_syntax(self):
        code, output = self.run_cli("--plan", "--reset", "dissolve")
        self.assertEqual(code, 0)
        commands = json.loads(output)["commands"]
        reset = next(c for c in commands if "--kind" in c)
        self.assertEqual(reset[reset.index("--kind") + 1], "dissolve")
        for value in ("../paper", "paper;echo", ""):
            with self.subTest(value=value):
                code, _ = self.run_cli("--plan", "--reset", value)
                self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())

    def test_existing_output_is_never_overwritten(self):
        self.out.mkdir()
        marker = self.out / "keep.txt"
        marker.write_text("keep me")
        with patch.object(replay.subprocess, "run") as run:
            code, _ = self.run_cli()
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertEqual(marker.read_text(), "keep me")

    def test_missing_dependency_does_not_create_output(self):
        (self.skill / "scripts/render.mjs").unlink()
        code, output = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("dependency missing", output)
        self.assertFalse(self.out.exists())

    def test_non_self_contained_source_is_rejected(self):
        (self.src / "outside.txt").symlink_to(self.root / "missing")
        code, _ = self.run_cli()
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())

    def test_renderer_failure_is_preserved_and_not_reported_success(self):
        with patch.object(replay.subprocess, "run") as run:
            run.return_value.returncode = 23
            code, _ = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(run.call_count, 1)
        receipt = json.loads((self.out / "replay.json").read_text())
        self.assertEqual(receipt["state"], "failed")
        self.assertEqual(receipt["steps"][0]["exit_code"], 23)
        self.assertEqual((self.src / "index.html").read_text(), "prepared source")


if __name__ == "__main__":
    unittest.main()
