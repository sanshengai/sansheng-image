"""共建约定：Issue 模板、作品 Schema 与公开导出范围必须与正式文件一致。"""
import copy
import json
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import public_export  # noqa: E402
from public_export import REQUIRED, select  # noqa: E402

TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
SCHEMA_PATH = ROOT / "references" / "community-works.schema.json"
MARKER = "<!-- image-share-rights-v1 -->"
DIGEST = "a" * 64
NEW_PUBLIC = {"CONTRIBUTING.md", ".github/ISSUE_TEMPLATE/recommend-style.yml",
              ".github/ISSUE_TEMPLATE/bug.yml"}


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validator(schema=None):
    return Draft202012Validator(schema or load_schema(), format_checker=FormatChecker())


def feedback_work(**over):
    work = {
        "id": "w-001", "style_code": "C01", "submitted_at": "2026-10-08T01:02:03Z",
        "source": {"kind": "feedback", "receipt_id": "r-001"},
        "rights_review": "needs_review", "publication_state": "pending",
        "attribution": {"enabled": False},
        "image_ref": {"receipt_id": "r-001", "digest": DIGEST},
    }
    work.update(over)
    return work


def github_work():
    return feedback_work(id="w-002", source={
        "kind": "github", "repo": "sanshengai/sansheng-image", "number": 7,
        "url": "https://github.com/sanshengai/sansheng-image/issues/7"})


def doc(*works):
    return {"schema_version": 1, "works": list(works)}


def template(name):
    return yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))


def test_recommend_template_keeps_rights_marker_and_required():
    data = template("recommend-style.yml")
    assert data["labels"] == ["feedback:style"]
    boxes = [b for b in data["body"] if b["type"] == "checkboxes"]
    assert [b["id"] for b in boxes] == ["image-share-rights"]
    options = boxes[0]["attributes"]["options"]
    assert len(options) == 1
    assert MARKER in options[0]["label"] and options[0]["required"] is True


def test_bug_template_has_no_share_checkbox():
    data = template("bug.yml")
    assert data["labels"] == ["feedback:bug"]
    assert all(b["type"] != "checkboxes" for b in data["body"])
    assert MARKER not in (TEMPLATES / "bug.yml").read_text(encoding="utf-8")
    required = {b["id"] for b in data["body"] if b.get("validations", {}).get("required")}
    assert required == {"steps", "expected", "actual"}


def test_schema_itself_is_valid():
    Draft202012Validator.check_schema(load_schema())


@pytest.mark.parametrize("sample", [
    doc(), doc(feedback_work()), doc(github_work()),
    doc(feedback_work(publication_state="approved", rights_review="confirmed",
                      attribution={"enabled": True, "display_name": "某某"})),
])
def test_valid_documents_are_accepted(sample):
    assert not list(validator().iter_errors(sample))


def bad_cases():
    return {
        "approved_without_confirmed": doc(feedback_work(publication_state="approved")),
        "attribution_true_no_name": doc(feedback_work(attribution={"enabled": True})),
        "attribution_false_with_name": doc(feedback_work(
            attribution={"enabled": False, "display_name": "某某"})),
        "public_image_url": doc(feedback_work(image_ref={
            "receipt_id": "r-001", "digest": DIGEST, "url": "https://example.com/a.png"})),
        "extra_email": doc(feedback_work(email="a@example.com")),
        "bad_digest": doc(feedback_work(image_ref={"receipt_id": "r-001", "digest": "xyz"})),
        "non_utc": doc(feedback_work(submitted_at="2026-10-08T09:02:03+08:00")),
        "wrong_repo": doc(feedback_work(source={
            "kind": "github", "repo": "other/repo", "number": 7,
            "url": "https://github.com/sanshengai/sansheng-image/issues/7"})),
    }


@pytest.mark.parametrize("name", list(bad_cases()))
def test_bad_documents_are_rejected(name):
    assert list(validator().iter_errors(bad_cases()[name]))


def test_approval_gate_is_load_bearing():
    """去掉 approved→confirmed 条件后，原本被拒的反例必须被接受，证明该闸门真在起作用。"""
    case = bad_cases()["approved_without_confirmed"]
    assert list(validator().iter_errors(case))
    mutated = copy.deepcopy(load_schema())
    del mutated["$defs"]["work"]["allOf"]
    assert not list(validator(mutated).iter_errors(case))


def test_export_selects_new_files_and_rejects_other_github_files():
    count = len(json.loads((ROOT / "styles/catalog.json").read_text(encoding="utf-8"))["styles"])
    names = set(REQUIRED) | NEW_PUBLIC | {"references/community-works.schema.json"}
    names |= {f"styles/C{i + 1:02d}/contract.json" for i in range(count)}
    entries = [("100644", n) for n in sorted(names)] + [
        ("100644", ".github/secrets/notes.txt"), ("100644", ".github/workflows/x.yml")]
    selected = set(select(entries))
    assert NEW_PUBLIC <= selected
    assert "references/community-works.schema.json" in selected
    assert ".github/secrets/notes.txt" not in selected
    assert ".github/workflows/x.yml" not in selected
    assert NEW_PUBLIC <= public_export.PUBLIC_FILES
