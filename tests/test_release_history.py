"""更新记录只来自当前包已发布的 CHANGELOG，不泄露未发布草稿。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stylebook import build as BD  # noqa: E402


def test_history_excludes_drafts_future_versions_and_other_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(BD.CT, "ROOT", tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("""# Changelog
## [未发布]
只供内部看的草稿
## [3.0.0] -- 2026-10-22
尚不属于当前包
## [2.9.0] -- 2026-10-18
- 已发布说明
## [2.10.0] -- 2026-10-19
### 修复
- 最新说明
## 历史迁移备注
不能混进上一版的记录
## [1.0.0] -- 2026-10-01
- 最早说明
""", encoding="utf-8")
    entries = BD._release_history("2.10.0")
    assert [r["version"] for r in entries] == ["2.10.0", "2.9.0", "1.0.0"]
    assert entries[0] == {"version": "2.10.0", "date": "2026-10-19", "notes": "### 修复\n- 最新说明"}
    assert "草稿" not in json.dumps(entries, ensure_ascii=False)
    assert "当前包" not in json.dumps(entries, ensure_ascii=False)
    assert "迁移" not in json.dumps(entries, ensure_ascii=False)
    assert [r["version"] for r in BD._release_history("2.9.0")] == ["2.9.0", "1.0.0"]


@pytest.mark.parametrize("version", ["", "latest", "2.2", "v2.2.0", "2.2.0-rc.1"])
def test_unknown_version_has_no_history(tmp_path, monkeypatch, version):
    monkeypatch.setattr(BD.CT, "ROOT", tmp_path)
    assert BD._release_history(version) == []


def test_missing_and_empty_changelog_have_no_history(tmp_path, monkeypatch):
    monkeypatch.setattr(BD.CT, "ROOT", tmp_path)
    assert BD._release_history("2.2.0") == []
    (tmp_path / "CHANGELOG.md").write_text("", encoding="utf-8")
    assert BD._release_history("2.2.0") == []


def test_notes_preserve_text_and_embedded_history_cannot_close_script(tmp_path, monkeypatch):
    monkeypatch.setattr(BD.CT, "ROOT", tmp_path)
    note = '- 包含 `参数` 和 </script><img src=x onerror="alert(1)"> 的文字。'
    (tmp_path / "CHANGELOG.md").write_text("## [2.2.0] -- 2026-10-19\n\n" + note, encoding="utf-8")
    entries = BD._release_history("2.2.0")
    assert entries[0]["notes"] == note
    embedded = BD._embed_json({"changelog": entries})
    assert "</script>" not in embedded.lower()
    assert json.loads(embedded)["changelog"][0]["notes"] == note
