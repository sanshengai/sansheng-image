"""Binding and failure-path tests; real browser/render validation is an integration check."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import wake_runtime as R
from stylebook import wake_workflow as W
from stylebook import animate_adapter as A


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = patch.dict(os.environ, {"SANSHENG_IMAGE_CONFIG_DIR": str(self.root / "config")})
        self.env.start(); self.addCleanup(self.env.stop)
        self.skill = self.root / "runtime"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("fixed upstream")
        self.lock = {**R.LOCK, "files": {"SKILL.md": R.sha(self.skill / "SKILL.md")}}
        self.pin = patch.object(R, "LOCK", self.lock)
        self.pin.start(); self.addCleanup(self.pin.stop)

    def test_setup_uses_existing_runtime_without_fetch_or_node_install(self):
        with patch.object(R, "install_runtime") as fetch, patch.object(R, "run") as run:
            result = R.setup(skill=self.skill)
        self.assertEqual(result["state"], "configured-needs-doctor")
        fetch.assert_not_called(); run.assert_not_called()
        self.assertEqual(Path(R.settings()["wake_skill"]), self.skill)

    def test_missing_and_changed_upstream_are_rejected_before_config_write(self):
        for mutation in ("", "changed"):
            (self.skill / "SKILL.md").write_text(mutation)
            with self.assertRaises(R.WakeError):
                R.setup(skill=self.skill)
            self.assertFalse((R.config_dir() / "motion-wake.json").exists())

    def test_enabled_runtime_detects_upstream_drift(self):
        R.setup(skill=self.skill)
        (self.skill / "SKILL.md").write_text("different code")
        with self.assertRaisesRegex(R.WakeError, "上游版本"):
            R.settings()

    def test_disabled_or_empty_config_never_installs(self):
        for content in ({}, {"enabled": False}):
            R.write_json(R.config_dir() / "motion-wake.json", content)
            with patch.object(R, "install_runtime") as fetch, self.assertRaises(R.WakeError):
                R.settings()
            fetch.assert_not_called()

    def test_legacy_route_does_not_load_optional_runtime(self):
        # Run the real adapter's doctor route with its existing pipeline protocol.
        from stylebook import motion_pipeline as P
        with patch.object(R, "settings", side_effect=AssertionError("motion runtime touched")), \
             patch.object(P, "run_doctor", return_value=0), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            A.main(["doctor"], pipeline_module=P)
        self.assertFalse((R.config_dir() / "motion-wake.json").exists())


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "new motion"
        self.original = self.base / "photo.png"
        self.original.write_bytes(b"original image bytes")
        self.article = self.base / "article.md"
        self.article.write_text("An article with a factual caption", encoding="utf-8")
        self.config = {"wake_skill": str(self.base / "external"), "models_dir": str(self.base / "models")}
        setup = patch.object(R, "settings", return_value=self.config)
        setup.start(); self.addCleanup(setup.stop)

    def init(self, *extra):
        def create(command, *, cwd, log, config):
            src = cwd / "working-alive/src"
            (src / "assets").mkdir(parents=True)
            (src / "index.html").write_text("initial template")
            (src / "assets/source.png").write_bytes(b"normalized image")
        with patch.object(R, "run", side_effect=create), contextlib.redirect_stdout(io.StringIO()):
            code = W.main(["init", str(self.original), "--out", str(self.root), "--purpose", "Explain the claim", *extra])
        self.assertEqual(code, 0)
        return R.read_json(self.root / "motion.json")

    def prepare(self):
        data = self.init("--article", str(self.article))
        (self.root / "src/index.html").write_text("the prepared action")
        plan = {"decision": "animate", "mode": "explanation", "why_motion": "Show the dependency in two states",
                "preserve": ["The subject and the article wording"],
                "beats": [{"at": 0, "event": "before"}, {"at": 4, "event": "after"}],
                "materials": [{"path": "src/assets/source.png", "motion": "fixed", "complete": False,
                               "observation": "Full image retained without separating the hidden body"}],
                "review_times": [0, 2, 5.8], "loop": "Return to the original state"}
        R.write_json(self.root / "director.json", plan)
        return data, plan

    def make_candidate(self):
        data, plan = self.prepare()
        current = W.snapshot(self.root, data)
        preview = self.root / "previews/001"
        preview.mkdir(parents=True)
        for name in W.PREVIEW_FILES:
            (preview / name).write_text("valid preview evidence " + name)
        R.write_json(preview / "preview.json", {"snapshot": current,
            "evidence": {name: R.sha(preview / name) for name in W.PREVIEW_FILES}})
        out = self.root / "renders/001"
        out.mkdir(parents=True)
        for name in W.RENDER_FILES:
            (out / name).write_text("rendered bytes " + name)
        (out / "qa.json").write_text(json.dumps([{"level": "PASS", "item": "render errors", "msg": "none"}]))
        R.write_json(out / "candidate.json", {"snapshot": current, "preview": "previews/001",
            "preview_sha256": R.sha(preview / "preview.json"),
            "files": {name: R.sha(out / name) for name in W.RENDER_FILES}})
        report = {"verdict": "accept", "reviewer": "Test observer", "playback": {"cycles": 2, "method": "Two complete loops in browser"},
            "checks": {key: {"result": "pass", "note": "Observed " + key} for key in W.CHECKS},
            "evidence": ["timeline.jpg", "materials.jpg"], "issues": [],
            "platform": {"state": "untested", "note": "No upload test for this file"}}
        assessment = self.base / "assessment.json"
        R.write_json(assessment, report)
        return data, out, assessment, report

    def test_init_inherits_brief_and_binds_original_article(self):
        brief = self.base / "brief.json"
        R.write_json(brief, {"version": 1, "scene": "wxillus", "style": "C42", "palette": {"family": "orig"},
            "reason": "Explain the article", "source": "article.md", "items": [{"id": "figure-2",
            "position": "factual caption", "message": "Explain the claim", "visual": "Two cards on a table", "text": []}]})
        data = self.init("--brief", str(brief), "--item", "figure-2")
        self.assertEqual(data["scene"], "article")
        self.assertTrue(data["inherit"]["style"].startswith("C42"))
        self.assertEqual(data["inherit"]["item"]["id"], "figure-2")
        self.assertEqual((self.root / "inputs/original.png").read_bytes(), self.original.read_bytes())
        self.assertEqual(data["inputs"]["inputs/article.md"], R.sha(self.article))
        self.assertIn(R.LOCK["license"], (self.root / "UPSTREAM.md").read_text())

    def test_empty_or_mismatched_brief_cannot_create_project(self):
        brief = self.base / "brief.json"
        for value in ({}, {"version": 1, "scene": "wxillus", "selection": {"code": "C42"},
                "tasks": [{"id": "01", "manifest": {"style": "C42", "palette": {"family": "orig"}, "format": "wide"}}],
                "source": {"path": "article.md", "sha256": "wrong"}}):
            R.write_json(brief, value)
            with patch.object(R, "run") as tool, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(W.main(["init", str(self.original), "--out", str(self.root), "--purpose", "why", "--brief", str(brief)]), 2)
            tool.assert_not_called()
            self.assertFalse(self.root.exists())

    def test_real_static_code_and_prepared_plan_keep_the_same_selection(self):
        from stylebook import make, stylecode
        brief = {"version": 1, "scene": "wxillus", "style": "C31", "reason": "Explain the article",
            "code": stylecode.StyleCode(style="C42", palette="orig", light=0, sat=0).format(),
            "source": "article.md", "items": [{"position": "factual caption", "message": "Two options",
            "visual": "Two cards on a table", "text": []}]}
        prepared = make.prepare(brief, base_path=self.base)
        path = self.base / "prepared.json"; R.write_json(path, prepared)
        with patch.object(make, "prepare", side_effect=AssertionError("must not re-resolve an adopted selection")):
            data = self.init("--brief", str(path), "--item", "01")
        self.assertTrue(data["inherit"]["style"].startswith("C42@r"))
        self.assertEqual(data["inherit"]["palette"], prepared["tasks"][0]["manifest"]["palette"])
        self.assertEqual(data["inherit"]["item"]["compiled"], prepared["tasks"][0]["compiled"])
        self.assertEqual(data["inputs"]["inputs/article.md"], prepared["source"]["sha256"])

    def test_existing_project_and_empty_image_are_not_overwritten(self):
        self.root.mkdir()
        marker = self.root / "keep"
        marker.write_text("keep")
        with patch.object(R, "run") as tool, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(W.main(["init", str(self.original), "-o", str(self.root), "--purpose", "why"]), 2)
        tool.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")
        self.original.write_bytes(b"")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(W.main(["init", str(self.original), "-o", str(self.base / "other"), "--purpose", "why"]), 2)
        self.assertFalse((self.base / "other").exists())

    def test_preview_keeps_fonts_bound_but_out_of_image_sheet(self):
        data, plan = self.prepare()
        font = self.root / "src/assets/caption.OTF"
        font.write_bytes(b"local font inventory")
        plan["materials"].append({"path": "src/assets/caption.OTF", "motion": "fixed",
            "observation": "A local caption font loaded before READY"})
        R.write_json(self.root / "director.json", plan)
        commands = []
        def run(command, *, cwd, log, config):
            commands.append(command)
            if log.name == "inspect.json":
                R.write_json(log, {"cfg": {"outW": 1280, "outH": 720,
                    "fps": data["output"]["fps"], "dur": data["output"]["duration"]}})
            elif log.name in {"timeline.log", "materials.log"}:
                (log.parent / ("timeline.jpg" if log.name == "timeline.log" else "materials.jpg")).write_bytes(b"sheet")
        with patch.object(R, "run", side_effect=run):
            result = W.preview(self.root)
        sheet = next(c for c in commands if str(Path(result["preview"]) / "materials.jpg") in map(str, c))
        self.assertIn(str(self.root / "src/assets/source.png"), list(map(str, sheet)))
        self.assertNotIn(str(font), list(map(str, sheet)))
        proof = R.read_json(Path(result["preview"]) / "preview.json")
        self.assertEqual(proof["snapshot"]["src"]["assets/caption.OTF"], R.sha(font))
        # The same consumer path must reject an empty visual input, even with a real font entry.
        plan["materials"] = [plan["materials"][-1]]
        R.write_json(self.root / "director.json", plan)
        with patch.object(R, "run") as tool, self.assertRaisesRegex(R.WakeError, "字体不能"):
            W.preview(self.root)
        tool.assert_not_called()

    def test_pending_and_unmodified_template_are_rejected(self):
        data = self.init()
        with self.assertRaises(R.WakeError):
            W.validate_director(self.root, data)
        # A plan without authored action is still not a prepared animation.
        _, plan = self._plan_after_init()
        R.write_json(self.root / "director.json", plan)
        with self.assertRaisesRegex(R.WakeError, "初始模板"):
            W.validate_director(self.root, data)

    def _plan_after_init(self):
        return None, {"decision": "animate", "mode": "material", "why_motion": "Turn a physical sheet", "preserve": ["full source"],
            "beats": [{"at": 0, "event": "start"}, {"at": 4, "event": "settle"}], "review_times": [0, 2, 5], "loop": "return",
            "materials": [{"path": "src/assets/source.png", "motion": "move", "complete": True, "observation": "whole rectangle"}]}

    def test_incomplete_moving_material_is_rejected_at_preview_entry(self):
        data, plan = self.prepare()
        plan["materials"][0]["motion"] = "move"
        R.write_json(self.root / "director.json", plan)
        with patch.object(R, "run") as tool, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(W.main(["preview", str(self.root)]), 2)
        tool.assert_not_called()
        self.assertFalse((self.root / "previews").exists())
        plan["materials"][0]["complete"] = True
        R.write_json(self.root / "director.json", plan)
        self.assertEqual(W.validate_director(self.root, data)["decision"], "animate")

    def test_no_dynamic_benefit_keeps_static(self):
        self.init()
        R.write_json(self.root / "director.json", {"decision": "static", "why_motion": "Motion would obscure the face"})
        self.assertEqual(W.status(self.root)["state"], "keep-static")
        with patch.object(R, "run") as tool, self.assertRaises(R.WakeError):
            W.render(self.root)
        tool.assert_not_called()

    def test_missing_preview_stops_render_before_external_tools(self):
        self.prepare()
        with patch.object(R, "run") as tool, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(W.main(["render", str(self.root)]), 2)
        tool.assert_not_called()
        self.assertFalse((self.root / "renders").exists())

    def test_empty_preview_evidence_cannot_bypass_render_gate(self):
        data, out, _, _ = self.make_candidate()
        preview = self.root / "previews/001/preview.json"
        record = R.read_json(preview); record["evidence"] = {}; R.write_json(preview, record)
        with self.assertRaisesRegex(R.WakeError, "完整文件清单"):
            W.latest_preview(self.root, W.snapshot(self.root, data))

    def test_materials_and_fonts_changes_invalidate_preview(self):
        data, _, _, _ = self.make_candidate()
        (self.root / "src/font.woff2").write_bytes(b"new font")
        with self.assertRaisesRegex(R.WakeError, "缺少.*预览"):
            W.latest_preview(self.root, W.snapshot(self.root, data))

    def test_raw_input_change_is_rejected(self):
        self.prepare()
        (self.root / "inputs/original.png").write_bytes(b"replaced source")
        with self.assertRaisesRegex(R.WakeError, "原始输入已变化"):
            W.project(self.root)

    def test_blank_assessment_and_pending_are_rejected(self):
        _, out, assessment, _ = self.make_candidate()
        for report in ({}, {"verdict": "pending"}):
            R.write_json(assessment, report)
            with self.assertRaises(R.WakeError):
                W.review(self.root, out, assessment)
        self.assertFalse((out / "reviews").exists())

    def test_real_review_entry_rejects_failed_or_empty_machine_qa(self):
        data, out, assessment, report = self.make_candidate()
        for qa in ([{"level": "FAIL", "item": "render errors", "msg": "missing frame"}], [], {}):
            with self.subTest(qa=qa):
                (out / "qa.json").write_text(json.dumps(qa))
                candidate = R.read_json(out / "candidate.json")
                candidate["files"]["qa.json"] = R.sha(out / "qa.json")
                R.write_json(out / "candidate.json", candidate)
                with contextlib.redirect_stderr(io.StringIO()):
                    code = W.main(["review", str(self.root), "--render", str(out), "--assessment", str(assessment)])
                self.assertEqual(code, 2)
                self.assertFalse((out / "reviews").exists())

    def test_output_mutation_and_empty_file_inventory_are_rejected(self):
        _, out, assessment, _ = self.make_candidate()
        (out / "motion.mp4").write_text("different bytes")
        with self.assertRaisesRegex(R.WakeError, "已被修改"):
            W.review(self.root, out, assessment)
        candidate = R.read_json(out / "candidate.json"); candidate["files"] = {}; R.write_json(out / "candidate.json", candidate)
        with self.assertRaisesRegex(R.WakeError, "完整文件清单"):
            W.review(self.root, out, assessment)

    def test_source_change_invalidates_old_render(self):
        _, out, assessment, _ = self.make_candidate()
        (self.root / "src/index.html").write_text("different action")
        with self.assertRaisesRegex(R.WakeError, "旧渲染"):
            W.review(self.root, out, assessment)

    def test_failed_visual_check_cannot_be_accepted(self):
        _, out, assessment, report = self.make_candidate()
        report["checks"]["integrity"] = {"result": "fail", "note": "Foot clipped"}
        R.write_json(assessment, report)
        with self.assertRaisesRegex(R.WakeError, "不能采用"):
            W.review(self.root, out, assessment)
        report["verdict"] = "revise"; report["issues"] = ["Repair foot mask before rendering again"]
        R.write_json(assessment, report)
        self.assertEqual(W.review(self.root, out, assessment)["state"], "needs-revision")
        self.assertEqual(W.status(self.root)["state"], "needs-revision")

    def test_contact_sheet_alone_and_unproven_platform_are_rejected(self):
        _, out, assessment, report = self.make_candidate()
        report["playback"]["cycles"] = 0
        R.write_json(assessment, report)
        with self.assertRaisesRegex(R.WakeError, "连续播放"):
            W.review(self.root, out, assessment)
        report["playback"]["cycles"] = 2
        report["platform"] = {"state": "tested", "note": "Claimed upload without evidence"}
        R.write_json(assessment, report)
        with self.assertRaisesRegex(R.WakeError, "平台实测"):
            W.review(self.root, out, assessment)

    def test_obvious_defect_can_be_returned_before_full_playback(self):
        _, out, assessment, report = self.make_candidate()
        report["verdict"] = "revise"
        report["playback"] = {"cycles": 0, "method": "Reviewed extremes; continuous visual playback remains unavailable"}
        report["checks"]["integrity"] = {"result": "fail", "note": "Door back has a rectangular floor patch"}
        report["checks"]["loop"] = {"result": "pending", "note": "Not visually observed yet"}
        report["issues"] = ["Repair the door back before another render"]
        R.write_json(assessment, report)
        self.assertEqual(W.review(self.root, out, assessment)["state"], "needs-revision")
        report["verdict"] = "accept"
        R.write_json(assessment, report)
        with self.assertRaisesRegex(R.WakeError, "连续播放"):
            W.review(self.root, out, assessment)

    def test_review_is_append_only_and_does_not_claim_user_adoption(self):
        _, out, assessment, _ = self.make_candidate()
        first = W.review(self.root, out, assessment)
        second = W.review(self.root, out, assessment)
        self.assertNotEqual(first["review"], second["review"])
        self.assertTrue(Path(first["review"]).is_file())
        self.assertEqual(second["user_adoption"], "not-recorded")
        self.assertEqual(W.status(self.root)["state"], "agent-reviewed-candidate")
        (out / "motion.gif").write_bytes(b"mutant gif")
        self.assertEqual(W.status(self.root)["state"], "needs-render")

    def test_empty_materials_and_outside_material_paths_are_rejected(self):
        data, plan = self.prepare()
        for materials in ([], [{"path": "../photo.png", "motion": "move", "complete": True, "observation": "outside"}]):
            plan["materials"] = materials; R.write_json(self.root / "director.json", plan)
            with self.assertRaises(R.WakeError):
                W.validate_director(self.root, data)


if __name__ == "__main__":
    unittest.main()
