"""画风动作语言（motion_dialect）读取入口与合同字段测试。

运行（在 Skill 根目录）：
    python3 -I -m unittest discover -s tests -p "test_motion_dialects.py" -v
默认测试当前仓；要测另一份副本，设 SANSHENG_IMAGE_ROOT=<sansheng-image 根目录>。

覆盖：
- 真实 CLI（sb.py animate dialect）与适配器读取 20 个正式合同，输出与合同文件逐项对得上；
- schema 反例从真实入口 contract.validate 验证（含屏蔽 jsonschema 后的内置校验），并用“放宽 schema”的变异证明用例会失败；
- build.registry / build._entry 把 motion_dialect 原样带入，且不影响静态出图用的 recipe_sha；
- 未知码、未编写、Cxx@rX 不匹配、非法参数一律退出码 2，无 traceback，也不留下输出文件；
- 原四场景白名单校验函数仍原样交给底座，dialect 不加载渲染底座。
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("SANSHENG_IMAGE_ROOT") or Path(__file__).resolve().parents[1]).resolve()
SCRIPTS = ROOT / "scripts"
if sys.path[:1] != [str(SCRIPTS)]:
    sys.path.insert(0, str(SCRIPTS))

from stylebook import animate_adapter as AA  # noqa: E402
from stylebook import build as BD  # noqa: E402
from stylebook import contract as CT  # noqa: E402
from stylebook import motion_dialects as MD  # noqa: E402
from stylebook import motion_pipeline as PM  # noqa: E402

DIALECT_IDS = ["C01", "C04", "C08", "C17", "C21", "C24", "C25", "C31", "C40", "C59",
               "C73", "C56", "C02", "C06", "C27", "C30", "C34", "C42", "C58", "C64"]
CATEGORIES = ["keyframes", "easing", "spring", "squash", "particles", "light_sweep",
              "mask_reveal", "card_flip", "char_text", "number_roll", "path_draw", "loop"]
BAN_IN_LANGUAGE = ("抬手", "少女", "拐杖", "步道", "永远")  # 固定样图人物 / 剧情 / 默认动作不应写进运动语言


def run_adapter(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = AA.main(argv)
    return rc, out.getvalue(), err.getvalue()


def run_cli(argv, cwd=None):
    """真实命令行：python3 -I scripts/sb.py animate ...；SANSHENG_IMAGE_PROFILE 指向不存在的目录以关闭私有层。"""
    env = dict(os.environ, SANSHENG_IMAGE_PROFILE=str(Path(tempfile.gettempdir()) / "no-such-profile-for-test"))
    cp = subprocess.run([sys.executable, "-I", str(SCRIPTS / "sb.py"), "animate", *argv],
                        capture_output=True, text=True, cwd=cwd, env=env, timeout=120)
    return cp.returncode, cp.stdout, cp.stderr


def raw_contract(code: str) -> dict:
    return json.loads((ROOT / "styles" / code / "contract.json").read_text(encoding="utf-8"))


def raw_sha(code: str) -> str:
    c = {k: v for k, v in raw_contract(code).items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(c, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class ContractFieldTests(unittest.TestCase):
    def test_twenty_contracts_have_valid_dialect(self):
        for code in DIALECT_IDS:
            with self.subTest(code=code):
                c = CT.load(code)  # 完整现有验证
                d = c["motion_dialect"]
                self.assertEqual(set(d), {"language", "primitives", "safety"})
                self.assertTrue(d["language"].strip())
                self.assertTrue(d["safety"].strip())
                self.assertTrue(d["primitives"])
                self.assertEqual(len(set(d["primitives"])), len(d["primitives"]))
                self.assertTrue(set(d["primitives"]) <= set(CATEGORIES))
                for word in BAN_IN_LANGUAGE:
                    self.assertNotIn(word, d["language"])
                self.assertTrue(any(k in d["safety"] for k in ("文字", "标题")), "safety 必须点明文字类保护")

    def test_material_specific_wording_not_mixed_up(self):
        self.assertIn("黏土", CT.load("C24")["motion_dialect"]["language"])
        self.assertIn("纸", CT.load("C27")["motion_dialect"]["language"])
        self.assertNotIn("黏土", CT.load("C27")["motion_dialect"]["language"])
        self.assertNotIn("黏土", CT.load("C59")["motion_dialect"]["language"])
        self.assertIn("squash", CT.load("C24")["motion_dialect"]["primitives"])
        self.assertNotIn("squash", CT.load("C27")["motion_dialect"]["primitives"])

    def test_static_compile_does_not_read_dialect(self):
        text = (SCRIPTS / "stylebook" / "compile.py").read_text(encoding="utf-8")
        self.assertNotIn("motion_dialect", text, "静态出图编译器不应读取动作语言")


class SchemaRejectionTests(unittest.TestCase):
    """反例必须由真实入口 contract.validate 拒绝；内置校验在屏蔽 jsonschema 时也必须独立拒绝。"""

    def setUp(self):
        self.base = {k: v for k, v in copy.deepcopy(CT.load("C24")).items() if not k.startswith("_")}

    def case(self, **mut):
        c = copy.deepcopy(self.base)
        d = c["motion_dialect"]
        for key, value in mut.items():
            if value is ...:
                d.pop(key)
            else:
                d[key] = value
        return c

    def problems(self, contract, *, without_jsonschema):
        if without_jsonschema:
            with mock.patch.dict(sys.modules, {"jsonschema": None}):
                return CT.validate(contract)
        return CT.validate(contract)

    BAD = {
        "language_missing": {"language": ...},
        "primitives_missing": {"primitives": ...},
        "safety_missing": {"safety": ...},
        "language_empty": {"language": ""},
        "language_blank": {"language": "   "},
        "language_not_string": {"language": 3},
        "safety_empty": {"safety": ""},
        "safety_blank": {"safety": " \n\t"},
        "safety_not_string": {"safety": ["x"]},
        "primitives_empty": {"primitives": []},
        "primitives_not_list": {"primitives": "keyframes"},
        "primitives_duplicate": {"primitives": ["keyframes", "keyframes"]},
        "primitives_unknown": {"primitives": ["keyframes", "sparkle"]},
        "primitives_wrong_case": {"primitives": ["Keyframes"]},
        "primitives_function_name": {"primitives": ["createKeyframes"]},
        "primitives_not_string_item": {"primitives": [1]},
        "extra_field": {"extra": "x"},
    }

    def test_baseline_accepts(self):
        for blocked in (True, False):
            with self.subTest(blocked=blocked):
                self.assertEqual(self.problems(self.base, without_jsonschema=blocked), [])

    def test_bad_dialects_rejected_by_builtin_checker(self):
        for name, mut in self.BAD.items():
            with self.subTest(name=name):
                problems = self.problems(self.case(**mut), without_jsonschema=True)
                self.assertTrue(any("motion_dialect" in p for p in problems), problems)

    def test_bad_dialects_rejected_with_jsonschema_if_installed(self):
        for name, mut in self.BAD.items():
            with self.subTest(name=name):
                self.assertTrue(self.problems(self.case(**mut), without_jsonschema=False))

    def test_dialect_must_be_object(self):
        for value in ([], "x", 1, None):
            with self.subTest(value=value):
                c = copy.deepcopy(self.base)
                c["motion_dialect"] = value
                problems = self.problems(c, without_jsonschema=True)
                self.assertTrue(any("motion_dialect" in p for p in problems), problems)

    def test_all_twelve_categories_accepted(self):
        for category in CATEGORIES:
            with self.subTest(category=category):
                self.assertEqual(self.problems(self.case(primitives=[category]), without_jsonschema=True), [])
        self.assertEqual(self.problems(self.case(primitives=list(CATEGORIES)), without_jsonschema=True), [])

    def test_mutated_schema_would_let_bad_cases_through(self):
        """变异：放宽 schema 后，对应反例必须变成“通过”，证明上面的拒绝确实来自 schema 的这几条约束。"""
        schema = json.loads(CT.SCHEMA_PATH.read_text(encoding="utf-8"))

        def mutated(edit):
            s = copy.deepcopy(schema)
            edit(s["properties"]["motion_dialect"])
            return s

        cases = {
            "extra_field": lambda d: d.__setitem__("additionalProperties", True),
            "primitives_duplicate": lambda d: d["properties"]["primitives"].pop("uniqueItems"),
            "primitives_unknown": lambda d: d["properties"]["primitives"]["items"].pop("enum"),
            "primitives_empty": lambda d: d["properties"]["primitives"].pop("minItems"),
            "language_blank": lambda d: d["properties"]["language"].pop("pattern"),
            "safety_empty": lambda d: (d["properties"]["safety"].update(minLength=0), d["properties"]["safety"].pop("pattern")),
        }
        for name, edit in cases.items():
            with self.subTest(name=name):
                with mock.patch.object(CT, "_schema", return_value=mutated(edit)):
                    problems = self.problems(self.case(**self.BAD[name]), without_jsonschema=True)
                self.assertFalse([p for p in problems if "motion_dialect" in p], problems)

    def test_schema_loaded_from_disk_has_strict_dialect(self):
        d = json.loads(CT.SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]["motion_dialect"]
        self.assertIs(d["additionalProperties"], False)
        self.assertEqual(sorted(d["required"]), ["language", "primitives", "safety"])
        self.assertEqual(d["properties"]["primitives"]["items"]["enum"], CATEGORIES)
        self.assertTrue(d["properties"]["primitives"]["uniqueItems"])


class DialectCliTests(unittest.TestCase):
    def test_all_twenty_via_adapter(self):
        for code in DIALECT_IDS:
            with self.subTest(code=code):
                rc, out, err = run_adapter(["dialect", "--style", code])
                self.assertEqual((rc, err), (0, ""))
                data = json.loads(out)
                on_disk = raw_contract(code)
                self.assertTrue(data["ok"])
                self.assertEqual(data["style"], code)
                self.assertEqual(data["revision"], on_disk["revision"])
                self.assertEqual(data["pinned"], f"{code}@r{on_disk['revision']}")
                self.assertEqual(data["motion_dialect"], on_disk["motion_dialect"])
                self.assertEqual(data["contract_sha256"], raw_sha(code))
                self.assertEqual(list(data["primitive_functions"]), on_disk["motion_dialect"]["primitives"])
                for category, names in data["primitive_functions"].items():
                    self.assertEqual(names, MD.CATEGORY_FUNCTIONS[category])
                self.assertIn("导演建议", data["scope"])
                self.assertIn("不证明", data["scope"])

    def test_real_sb_cli_reads_contract(self):
        with tempfile.TemporaryDirectory() as td:
            for code in ("C24", "C27", "C59"):
                with self.subTest(code=code):
                    rc, out, err = run_cli(["dialect", "--style", code], cwd=td)
                    self.assertEqual(rc, 0, err)
                    self.assertNotIn("Traceback", err)
                    data = json.loads(out)
                    self.assertEqual(data["motion_dialect"], raw_contract(code)["motion_dialect"])
                    self.assertEqual(data["contract_sha256"], raw_sha(code))
            self.assertEqual(os.listdir(td), [], "读取动作语言不应写任何文件")

    def test_pinned_current_revision_accepted_and_equals_unpinned(self):
        for code in ("C24", "C01", "C58"):
            with self.subTest(code=code):
                rev = raw_contract(code)["revision"]
                rc1, out1, _ = run_adapter(["dialect", "--style", code])
                rc2, out2, _ = run_adapter(["dialect", f"--style={code}@r{rev}"])
                self.assertEqual((rc1, rc2), (0, 0))
                self.assertEqual(json.loads(out1), json.loads(out2))

    def test_sha_excludes_runtime_fields(self):
        c = CT.load("C24")
        self.assertIn("_path", c)
        extra = {**c, "_alias_from": "C99", "_alias_note": "x"}
        self.assertEqual(MD.normalized_contract_sha256(c), MD.normalized_contract_sha256(extra))
        self.assertEqual(MD.normalized_contract_sha256(c), raw_sha("C24"))
        changed = copy.deepcopy(c)
        changed["motion_dialect"]["safety"] += "。"
        self.assertNotEqual(MD.normalized_contract_sha256(changed), MD.normalized_contract_sha256(c))

    def test_loader_is_real_ct_load_with_full_validation(self):
        broken = copy.deepcopy(CT.load("C24"))
        broken["motion_dialect"]["primitives"] = ["sparkle"]
        with tempfile.TemporaryDirectory() as td:
            bad_dir = Path(td) / "C24"
            bad_dir.mkdir()
            (bad_dir / "contract.json").write_text(
                json.dumps({k: v for k, v in broken.items() if not k.startswith("_")}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(CT, "contract_paths", return_value=[bad_dir / "contract.json"]):
                rc, out, err = run_adapter(["dialect", "--style", "C24"])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("不合格", err)
        self.assertNotIn("Traceback", err)

    def test_primitive_functions_exist_in_shipped_library(self):
        text = (SCRIPTS / "stylebook" / "assets" / "motion-primitives.mjs").read_text(encoding="utf-8")
        self.assertEqual(sorted(MD.CATEGORY_FUNCTIONS), sorted(CATEGORIES))
        for category, names in MD.CATEGORY_FUNCTIONS.items():
            for name in names:
                with self.subTest(category=category, name=name):
                    declared = re.search(rf"^export\s+(?:async\s+)?(?:function|const|let|class)\s+{name}\b", text, re.MULTILINE)
                    listed = re.search(rf"^export\s*\{{[^}}]*\b{name}\b", text, re.MULTILINE | re.DOTALL)
                    self.assertTrue(declared or listed, f"{name} 不是动作库的公开导出")

    def test_module_has_no_write_process_or_network_imports(self):
        source = (SCRIPTS / "stylebook" / "motion_dialects.py").read_text(encoding="utf-8")
        for token in ("subprocess", "socket", "urllib", "requests", "http.client", "os.system", "write_text", "write_bytes", "open("):
            self.assertNotIn(token, source)

    def test_help(self):
        for flag in ("-h", "--help"):
            rc, out, err = run_adapter(["dialect", flag])
            self.assertEqual(rc, 0)
            self.assertIn("--style", out)
            self.assertEqual(err, "")


class DialectRejectionTests(unittest.TestCase):
    def assert_rejected(self, argv, expect_in_err=None):
        with tempfile.TemporaryDirectory() as td:
            rc, out, err = run_cli(["dialect", *argv], cwd=td)
            self.assertEqual(rc, 2, (argv, out, err))
            self.assertEqual(out, "")
            self.assertIn("错误", err)
            self.assertNotIn("Traceback", err)
            if expect_in_err:
                self.assertIn(expect_in_err, err)
            self.assertEqual(os.listdir(td), [], "拒绝时不应留下任何输出文件")

    def test_illegal_arguments(self):
        for argv in ([], ["--style"], ["C24"], ["--style", "C24", "--extra"], ["--sty", "C24"], ["--style", ""],
                     ["--style", "c24"], ["--style", "C24 "], ["--style", "--help"], ["--style=--help"], ["--out", "x"],
                     ["--style", "C24", "C01"], ["--style", "C24@r"], ["--style", "C24@r0"], ["--style", "C24@r05"],
                     ["--style", "C24@5"], ["--style", "../C24"], ["--style", "C2"], ["--style", "C1234"]):
            with self.subTest(argv=argv):
                self.assert_rejected(argv)

    def test_unknown_code(self):
        self.assert_rejected(["--style", "C999"], "找不到")
        self.assert_rejected(["--style", "C998@r1"], "找不到")

    def test_private_code_without_profile_rejected_and_no_public_fallback(self):
        self.assert_rejected(["--style", "S998"])

    def test_pinned_revision_mismatch(self):
        rev = raw_contract("C24")["revision"]
        for wrong in (rev + 1, rev - 1, 99):
            with self.subTest(wrong=wrong):
                self.assert_rejected(["--style", f"C24@r{wrong}"], "修订")

    def test_alias_code_not_followed(self):
        aliases = CT._catalog_meta().get("aliases", {})
        for old, new in aliases.items():
            with self.subTest(old=old):
                self.assertNotEqual(old, new)
                with tempfile.TemporaryDirectory() as td:
                    rc, out, err = run_cli(["dialect", "--style", old], cwd=td)
                self.assertEqual((rc, out), (2, ""))
                self.assertNotIn("Traceback", err)

    def test_retired_code_rejected(self):
        for old in CT._catalog_meta().get("retired", {}):
            with self.subTest(old=old):
                rc, out, err = run_adapter(["dialect", "--style", old])
                self.assertEqual((rc, out), (2, ""))
                self.assertNotIn("Traceback", err)

    def test_contract_without_dialect_reports_not_authored_exit2(self):
        bare = {k: v for k, v in copy.deepcopy(CT.load("C24")).items() if k != "motion_dialect"}
        with mock.patch.object(CT, "load", return_value=bare):
            rc, out, err = run_adapter(["dialect", "--style", "C24"])
        self.assertEqual(rc, 2)
        self.assertNotIn("Traceback", err)
        data = json.loads(out)
        self.assertIs(data["ok"], False)
        self.assertEqual(data["status"], "motion_dialect_not_authored")
        self.assertIn("尚未编写", data["message"])
        self.assertNotIn("motion_dialect", data)
        self.assertNotIn("primitive_functions", data)
        self.assertEqual(data["style"], "C24")

    def test_real_uncovered_public_codes_do_not_borrow_other_dialects(self):
        covered = set(DIALECT_IDS)
        for c in CT.load_all(strict=False):
            if c["visibility"] != "public" or c["code"] in covered or "motion_dialect" in c:
                continue
            with self.subTest(code=c["code"]):
                rc, out, err = run_adapter(["dialect", "--style", c["code"]])
                self.assertEqual(rc, 2)
                data = json.loads(out)
                self.assertEqual(data["style"], c["code"])
                self.assertNotIn("motion_dialect", data)

    def test_private_style_readable_when_capability_present(self):
        """私有 S* 与公开码走同一 CT.load；有字段可读，无字段不回落公开默认。"""
        private = {k: v for k, v in copy.deepcopy(CT.load("C24")).items()}
        private.update(code="S09", visibility="private")
        with mock.patch.object(CT, "load", return_value=private):
            rc, out, _ = run_adapter(["dialect", "--style", "S09"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["style"], "S09")
        bare = {k: v for k, v in private.items() if k != "motion_dialect"}
        with mock.patch.object(CT, "load", return_value=bare):
            rc, out, _ = run_adapter(["dialect", "--style", "S09"])
            self.assertEqual(rc, 2)
            self.assertNotIn("motion_dialect", json.loads(out))


class RegistryProjectionTests(unittest.TestCase):
    def test_build_registry_projects_twenty_dialects(self):
        reg = BD.registry(False)
        by = {s["code"]: s for s in reg["styles"]}
        for code in DIALECT_IDS:
            with self.subTest(code=code):
                self.assertEqual(by[code]["motion_dialect"], raw_contract(code)["motion_dialect"])
        for code, entry in by.items():
            if code not in DIALECT_IDS and entry.get("has_contract") and "motion_dialect" not in raw_contract(code):
                self.assertNotIn("motion_dialect", entry)

    def test_entry_only_adds_metadata_and_never_changes_static_fields(self):
        cat = next(s for s in BD._public_catalog() if s["code"] == "C24")
        with_dialect = BD._entry(cat, "public", CT.load("C24"))
        bare_contract = {k: v for k, v in CT.load("C24").items() if k != "motion_dialect"}
        without = BD._entry(cat, "public", bare_contract)
        self.assertNotIn("motion_dialect", without)
        self.assertEqual({k: v for k, v in with_dialect.items() if k != "motion_dialect"}, without)
        self.assertEqual(with_dialect["recipe_sha"], without["recipe_sha"])

    def test_registry_public_check_still_clean_of_private_content(self):
        reg = BD.registry(False)
        blob = json.dumps(reg, ensure_ascii=False)
        self.assertNotRegex(blob, r"\bS\d{2,3}\b")


class AdapterBoundaryTests(unittest.TestCase):
    class Recording:
        def __init__(self, real):
            self._real = real
            self.main_calls = []

        def __getattr__(self, name):
            return getattr(self._real, name)

        def main(self, argv, **kwargs):
            self.main_calls.append((list(argv), kwargs))
            return 0

    def test_original_constants_unchanged(self):
        self.assertEqual(AA.SUPPORTED_SCENES, ("brand", "article", "infographic", "xiaohongshu"))
        self.assertEqual(AA.SUBCOMMANDS, ("doctor", "plan", "render", "qa"))
        self.assertEqual(AA.UNSUPPORTED_PRESETS, ("ppt-mp4-candidate", "music-loop-mp4-candidate"))

    def test_four_scene_mock_still_passes_original_validator(self):
        rec = self.Recording(PM)
        argv = ["doctor", "--hyperframes-cli", "x.mjs", "--node", sys.executable, "--ffmpeg", sys.executable,
                "--ffprobe", sys.executable]
        self.assertEqual(AA.main(argv, pipeline_module=rec), 0)
        self.assertEqual(len(rec.main_calls), 1)
        called_argv, kwargs = rec.main_calls[0]
        self.assertEqual(called_argv, argv)
        validator = kwargs["manifest_validator"]
        for scene in AA.SUPPORTED_SCENES:
            validator(types.SimpleNamespace(scene=scene, presets=["preview-gif"]))
        for scene in ("ppt", "music"):
            with self.assertRaises(PM.ManifestError):
                validator(types.SimpleNamespace(scene=scene, presets=[]))
        for preset in AA.UNSUPPORTED_PRESETS:
            with self.assertRaises(PM.ManifestError):
                validator(types.SimpleNamespace(scene="article", presets=[preset]))

    def test_dialect_never_loads_or_calls_pipeline(self):
        rec = self.Recording(PM)
        with mock.patch.object(AA, "_load_default_pipeline", side_effect=AssertionError("dialect 不应加载渲染底座")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(AA.main(["dialect", "--style", "C24"]), 0)
                self.assertEqual(AA.main(["dialect", "--style", "C24"], pipeline_module=rec), 0)
        self.assertEqual(rec.main_calls, [])

    def test_dialect_not_a_pipeline_subcommand(self):
        self.assertNotIn("dialect", AA.SUBCOMMANDS)
        rec = self.Recording(PM)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = AA.main(["dialects", "--style", "C24"], pipeline_module=rec)  # 近似名不得被当成 dialect
        self.assertEqual(rc, 2)
        self.assertEqual(rec.main_calls, [])


if __name__ == "__main__":
    unittest.main()
