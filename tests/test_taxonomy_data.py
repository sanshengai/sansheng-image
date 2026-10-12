"""分类标签（地域 / 时代 / 朝代 / 风格分类）的数据层：词表校验、标签并入目录、构建期检查（不开浏览器、不编码图片，秒级）。

只依赖当前 Skill 自带的文件：
- 真实目录 styles/catalog.json 做全量校验；
- “标签并入前”的基线由真实目录机器派生（去掉 taxonomy 与新增标签字段，legacyFamily 还原为 family），不读任何外部标签文件；
- 拒绝路径用下面一份很小的手写目录 + 标签 fixture，不随真实目录增删画风而漂移。
运行：python3 -m pytest -p no:cacheprovider tests/test_taxonomy_data.py -q
"""
import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import build as BD  # noqa: E402
from stylebook import taxonomy as TX  # noqa: E402

CATALOG = ROOT / "styles" / "catalog.json"
CODE_RE = re.compile(r"^C\d{2,3}$")


def _doc() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def derive_baseline(doc: dict) -> dict:
    """机器派生“标签并入前”的目录：去掉 taxonomy 与新增标签字段，legacyFamily 还原成 family（保持原字段顺序）。"""
    out = copy.deepcopy(doc)
    out.pop("taxonomy", None)
    styles = []
    for s in out["styles"]:
        ns = {}
        for k, v in s.items():
            if k in TX.NEW_KEYS:
                continue
            ns[k] = s["legacyFamily"] if k == "family" else v
        styles.append(ns)
    out["styles"] = styles
    return out


def derive_rows(doc: dict) -> dict:
    """从已带标签的目录还原评审标签行（confidence / notes 是评审字段，目录里没有，用固定占位）。"""
    return {"rows": [{"id": s["code"], "name": s["zh"], "confidence": "high", "notes": "",
                      "family": s["family"], **{k: copy.deepcopy(s[k]) for k in TX.FIELDS}} for s in doc["styles"]]}


def vocab_of(doc: dict) -> dict:
    return {k: list(doc["taxonomy"][k]) for k in TX.VOCAB_KEYS}


@pytest.fixture(scope="module")
def doc():
    return _doc()


@pytest.fixture(scope="module")
def baseline(doc):
    return derive_baseline(doc)


# ---------------- 真实目录：全部现役画风 ----------------

def test_actual_catalog_is_labelled_and_non_empty(doc):
    """存在性检测不能空转：目录必须有画风、有词表，且每个画风都带齐五个新字段。"""
    assert doc["styles"], "目录没有画风，下面所有逐行检查都会空转"
    assert isinstance(doc.get("taxonomy"), dict), "目录缺少 taxonomy 词表，构建期校验会被整体跳过"
    for s in doc["styles"]:
        assert {"family", *TX.NEW_KEYS} <= set(s), s["code"]


def test_every_active_row_uses_the_vocabulary_and_empties_are_exactly_the_pending_ones(doc):
    tax = doc["taxonomy"]
    pending = set(tax["pending"])
    codes = [s["code"] for s in doc["styles"]]
    assert len(set(codes)) == len(codes), "画风码重复"
    assert all(CODE_RE.match(c) for c in codes)
    assert pending <= set(codes), "待定登记里有目录不存在的画风"
    for k in TX.VOCAB_KEYS:
        assert tax[k] and len(set(tax[k])) == len(tax[k]) and all(isinstance(x, str) and x.strip() for x in tax[k]), k
        assert TX.PENDING_LABEL not in tax[k]
    for s in doc["styles"]:
        code = s["code"]
        assert s["family"] in tax["family"], (code, s["family"])
        assert isinstance(s["legacyFamily"], str) and s["legacyFamily"].strip()
        for k in TX.FIELDS:
            v = s[k]
            assert isinstance(v, list) and len(set(v)) == len(v) and len(v) <= TX.MAX_MULTI, (code, k, v)
            assert all(x in tax[k] for x in v), (code, k, v)
            if TX.SENTINEL[k] in v:
                assert v == [TX.SENTINEL[k]], (code, k, v)
        assert bool(s["dynasty"]) <= ("中国" in s["region"]), code           # 有朝代必须含中国
        assert not ("北欧" in s["region"] and "欧洲" in s["region"]), code
        if code in pending:
            assert s["region"] == s["era"] == s["dynasty"] == [], code
        else:
            assert s["region"] and s["era"], (code, "空值但没登记待定")


def test_actual_catalog_passes_the_shared_validator_and_the_build_check(doc):
    assert TX.validate_styles(doc["styles"], doc["taxonomy"]) == []
    reg = BD.registry(False)
    assert BD.check(reg) == []
    public = [s for s in reg["styles"] if s["visibility"] == "public"]
    assert len(public) == len(doc["styles"]) > 0
    assert all({"legacyFamily", "region", "era", "dynasty"} <= set(s) for s in public)


def test_baseline_derivation_is_lossless_and_nothing_outside_the_label_fields_changed(doc, baseline):
    assert "taxonomy" not in baseline
    assert not any(k in s for s in baseline["styles"] for k in TX.NEW_KEYS)
    assert TX.unchanged_problems(baseline, doc) == []
    for a, b in zip(baseline["styles"], doc["styles"]):
        assert a["family"] == b["legacyFamily"]
        for k, v in b.items():
            if k not in TX.NEW_KEYS and k != "family":
                assert a[k] == v, (a["code"], k)
        assert list(a) == [k for k in b if k not in TX.NEW_KEYS]            # 原字段顺序
    assert {k: v for k, v in baseline.items() if k != "styles"} == {k: v for k, v in doc.items() if k not in ("styles", "taxonomy")}


def test_reapplying_the_labels_to_the_baseline_reproduces_the_actual_catalog_exactly(doc, baseline):
    pending = {c: "测试用原因" for c in doc["taxonomy"]["pending"]}
    new, rep = TX.apply_labels(copy.deepcopy(baseline), derive_rows(doc), vocab=vocab_of(doc), pending=pending)
    assert new == doc
    assert json.dumps(new, ensure_ascii=False) == json.dumps(doc, ensure_ascii=False)   # 键顺序也一致
    assert rep == {"styles": len(doc["styles"]), "extras_ignored": [], "pending": sorted(pending), "tag_rows": len(doc["styles"])}
    assert "测试用原因" not in json.dumps(new, ensure_ascii=False)          # 待定原因只进报告，不进公开目录


def _active(doc):
    return next(s["code"] for s in doc["styles"] if s["code"] not in doc["taxonomy"]["pending"])


def _bad_real(doc, baseline, mutate, **kw):
    rows = derive_rows(doc)
    mutate(rows)
    with pytest.raises(TX.TaxonomyError) as e:
        TX.apply_labels(copy.deepcopy(baseline), rows, vocab=vocab_of(doc),
                        pending={c: "x" for c in doc["taxonomy"]["pending"]}, **kw)
    return "；".join(e.value.problems)


def test_real_data_rejects_missing_duplicate_unknown_and_bad_rows(doc, baseline):
    code = _active(doc)
    row = lambda r: next(x for x in r["rows"] if x["id"] == code)  # noqa: E731
    assert "没有对应行" in _bad_real(doc, baseline, lambda r: r["rows"].remove(row(r)))
    assert "重复" in _bad_real(doc, baseline, lambda r: r["rows"].append(copy.deepcopy(row(r))))
    assert "C777" in _bad_real(doc, baseline, lambda r: r["rows"].append({**copy.deepcopy(row(r)), "id": "C777"}), allow_extra=False)
    assert "词表外" in _bad_real(doc, baseline, lambda r: row(r).update(region=["火星"]))
    assert "pending" in _bad_real(doc, baseline, lambda r: row(r).update(era=[]))
    assert "名称对不上" in _bad_real(doc, baseline, lambda r: row(r).update(name="另一个名字"))
    dup_catalog = copy.deepcopy(baseline)
    dup_catalog["styles"].append(copy.deepcopy(dup_catalog["styles"][0]))
    with pytest.raises(TX.TaxonomyError, match="画风码有重复"):
        TX.apply_labels(dup_catalog, derive_rows(doc), vocab=vocab_of(doc), pending={})


def test_real_data_refuses_silent_overwrite(doc, baseline):
    rows, vocab = derive_rows(doc), vocab_of(doc)
    with pytest.raises(TX.TaxonomyError, match="拒绝覆盖"):
        TX.apply_labels(copy.deepcopy(doc), copy.deepcopy(rows), vocab=vocab, pending={})
    half = copy.deepcopy(baseline)
    half["styles"][0]["region"] = ["中国"]
    with pytest.raises(TX.TaxonomyError, match="拒绝覆盖"):
        TX.apply_labels(half, copy.deepcopy(rows), vocab=vocab, pending={})


# ---------------- 构建期检查（真实目录改坏一处必须被拦） ----------------

def _check_with(monkeypatch, tmp_path, doc, mutate):
    c = copy.deepcopy(doc)
    mutate(c)
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(BD, "CATALOG", p)
    return BD.check(BD.registry(False))


def test_build_check_rejects_broken_catalog_and_accepts_the_untouched_one(monkeypatch, tmp_path, doc):
    i = next(n for n, s in enumerate(doc["styles"]) if s["code"] not in doc["taxonomy"]["pending"])
    run = lambda m: _check_with(monkeypatch, tmp_path, doc, m)  # noqa: E731
    assert run(lambda c: None) == []   # 对照：未改动的目录通过，下面的失败才有意义
    assert any("缺少 region" in x for x in run(lambda c: c["styles"][i].pop("region")))
    assert any("词表外" in x for x in run(lambda c: c["styles"][i].update(era=["中世纪"])))
    assert any("重复" in x for x in run(lambda c: c["styles"][i].update(region=["日本", "日本"])))
    assert any("未登记在 taxonomy.pending" in x for x in run(lambda c: c["styles"][i].update(era=[])))
    assert any("不在目录" in x for x in run(lambda c: c["taxonomy"].update(pending=[*c["taxonomy"]["pending"], "C555"])))
    assert any("缺少 legacyFamily" in x for x in run(lambda c: c["styles"][i].pop("legacyFamily")))


def test_old_catalog_without_taxonomy_still_builds_unchanged(monkeypatch, tmp_path, baseline):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(BD, "CATALOG", p)
    reg = BD.registry(False)
    assert BD.check(reg) == []
    assert not any(k in s for s in reg["styles"] for k in TX.NEW_KEYS)


# ---------------- 小手写 fixture：规则本身（不随真实目录增删画风而漂移） ----------------

VOCAB = {"family": ["水彩", "工笔", "像素"],
         "region": ["中国", "日本", "欧洲", "北欧", "跨地域"],
         "era": ["传统语汇", "20世纪", "未限定"],
         "dynasty": ["宋", "明", "跨朝代"]}


def mini_catalog() -> dict:
    return {"version": 1, "note": "fixture", "aliases": {"旧": "C01"}, "retired": ["C90"], "removed": ["C99"],
            "styles": [
                {"code": "C01", "zh": "甲", "family": "旧A", "fit": ["海报"], "uses": ["u1"], "extra": {"k": [1, 2]}},
                {"code": "C02", "zh": "乙", "family": "旧B", "fit": [], "uses": []},
                {"code": "C03", "zh": "丙", "family": "旧A", "fit": [], "uses": []},
            ]}


def mini_tags() -> dict:
    def row(i, name, family, region, era, dynasty):
        return {"id": i, "name": name, "confidence": "high", "notes": "", "family": family,
                "region": region, "era": era, "dynasty": dynasty}
    return {"rows": [row("C01", "甲", "水彩", ["日本"], ["20世纪"], []),
                     row("C02", "乙", "工笔", ["中国"], ["传统语汇"], ["宋"]),
                     row("C03", "丙", "像素", [], [], [])]}


PENDING = {"C03": "来源冲突，待查"}


def mini_apply(mutate_tags=None, mutate_cat=None, **kw):
    cat, tags = mini_catalog(), mini_tags()
    if mutate_cat:
        mutate_cat(cat)
    if mutate_tags:
        mutate_tags(tags)
    return TX.apply_labels(cat, tags, vocab=VOCAB, pending=kw.pop("pending", PENDING), **kw)


def mini_bad(mutate_tags=None, mutate_cat=None, **kw) -> str:
    with pytest.raises(TX.TaxonomyError) as e:
        mini_apply(mutate_tags, mutate_cat, **kw)
    return "；".join(e.value.problems)


def row_of(t, rid):
    return next(r for r in t["rows"] if r["id"] == rid)


def test_fixture_happy_path_labels_pending_and_report():
    new, rep = mini_apply()
    by = {s["code"]: s for s in new["styles"]}
    assert by["C01"]["family"] == "水彩" and by["C01"]["legacyFamily"] == "旧A"
    assert (by["C02"]["region"], by["C02"]["era"], by["C02"]["dynasty"]) == (["中国"], ["传统语汇"], ["宋"])
    assert (by["C03"]["region"], by["C03"]["era"], by["C03"]["dynasty"]) == ([], [], [])
    assert new["taxonomy"] == {"version": 1, **VOCAB, "pending": ["C03"]}
    assert rep == {"styles": 3, "extras_ignored": [], "pending": ["C03"], "tag_rows": 3}
    assert "待查" not in json.dumps(new, ensure_ascii=False)
    assert TX.validate_styles(new["styles"], new["taxonomy"]) == []


def test_fixture_preserves_every_other_field_and_does_not_mutate_inputs():
    cat, tags = mini_catalog(), mini_tags()
    cat0, tags0 = copy.deepcopy(cat), copy.deepcopy(tags)
    new, _ = TX.apply_labels(cat, tags, vocab=VOCAB, pending=PENDING)
    assert cat == cat0 and tags == tags0                                    # 输入不被改写
    assert TX.unchanged_problems(cat0, new) == []
    assert new["styles"][0]["extra"] == {"k": [1, 2]} and new["styles"][0]["fit"] == ["海报"]
    assert list(new["styles"][0]) == ["code", "zh", "family", "legacyFamily", "region", "era", "dynasty", "fit", "uses", "extra"]
    for k in ("version", "note", "aliases", "retired", "removed"):
        assert new[k] == cat0[k]
    new["styles"][0]["extra"]["k"].append(3)                                # 输出与输入不共享可变对象
    assert cat["styles"][0]["extra"]["k"] == [1, 2]
    new["styles"][1]["region"].append("日本")
    assert tags["rows"][1]["region"] == ["中国"]


# 空输入：必须被明确拒绝，不能当成“没问题”
@pytest.mark.parametrize("cat", [{}, {"styles": []}, {"styles": None}])
def test_empty_catalog_is_rejected(cat):
    with pytest.raises(TX.TaxonomyError, match="没有 styles"):
        TX.apply_labels(cat, {"rows": []}, vocab=VOCAB, pending={})


@pytest.mark.parametrize("tags", [{}, None, [], {"rows": None}, {"rows": "x"}])
def test_missing_or_malformed_rows_document_is_rejected(tags):
    with pytest.raises(TX.TaxonomyError, match="缺少 rows"):
        TX.apply_labels(mini_catalog(), tags, vocab=VOCAB, pending=PENDING)


def test_empty_rows_list_reports_every_style_as_missing():
    msg = mini_bad(lambda t: t["rows"].clear())
    assert all(f"{c} 在标签里没有对应行" in msg for c in ("C01", "C02", "C03"))


def test_non_object_rows_are_rejected():
    assert "标签行不是对象" in mini_bad(lambda t: t["rows"].append("C01"))


@pytest.mark.parametrize("vocab_mut,needle", [
    (lambda v: v.update(family=[]), "taxonomy.family 必须是非空字符串列表"),
    (lambda v: v.update(region=["中国", "中国", "日本", "跨地域"]), "词表有重复项"),
    (lambda v: v.update(era=["传统语汇", "未限定", "资料待定"]), "保留词"),
    (lambda v: v.update(dynasty=["宋", " ", "跨朝代"]), "taxonomy.dynasty 必须是非空字符串列表"),
])
def test_bad_vocabulary_is_rejected(vocab_mut, needle):
    vocab = copy.deepcopy(VOCAB)
    vocab_mut(vocab)
    with pytest.raises(TX.TaxonomyError) as e:
        TX.apply_labels(mini_catalog(), mini_tags(), vocab=vocab, pending=PENDING)
    assert needle in str(e.value)


@pytest.mark.parametrize("tax,needle", [
    (None, "必须是对象"),
    ([], "必须是对象"),
    ({**VOCAB, "pending": [], "mystery": 1}, "未知键"),
    ({**VOCAB, "pending": ["C03", "C03"]}, "pending"),
    ({**VOCAB, "pending": "C03"}, "pending"),
])
def test_validate_styles_rejects_malformed_taxonomy_blocks(tax, needle):
    new, _ = mini_apply()
    assert needle in "；".join(TX.validate_styles(new["styles"], tax))


# 重复
def test_duplicate_row_is_rejected():
    msg = mini_bad(lambda t: t["rows"].append(copy.deepcopy(row_of(t, "C02"))))
    assert "C02" in msg and "重复" in msg


def test_duplicate_style_code_in_the_catalog_is_rejected():
    assert "画风码有重复" in mini_bad(mutate_cat=lambda c: c["styles"].append(copy.deepcopy(c["styles"][0])))


@pytest.mark.parametrize("field,value", [("region", ["日本", "日本"]), ("era", ["20世纪", "20世纪"])])
def test_duplicate_values_inside_a_row_are_rejected(field, value):
    assert "重复值" in mini_bad(lambda t: row_of(t, "C01").update({field: value}))


# 未知
def test_unknown_id_is_rejected_in_strict_mode_and_reported_otherwise():
    def add(t):
        t["rows"].append({**copy.deepcopy(row_of(t, "C02")), "id": "C777"})
    assert "C777" in mini_bad(add, allow_extra=False)
    new, rep = mini_apply(add)
    assert rep["extras_ignored"] == ["C777"] and "C777" not in {s["code"] for s in new["styles"]}
    assert "格式不对" in mini_bad(lambda t: t["rows"].append({**copy.deepcopy(row_of(t, "C02")), "id": "../etc"}))


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r.update(family="油画"), "family"),
    (lambda r: r.update(region=["火星"]), "词表外"),
    (lambda r: r.update(era=["中世纪"]), "词表外"),
    (lambda r: r.update(dynasty=["清"]), "词表外"),
    (lambda r: r.update(region=["跨地域", "日本"]), "不能和其他取值并列"),
    (lambda r: r.update(era=["未限定", "20世纪"]), "不能和其他取值并列"),
    (lambda r: r.update(region=["北欧", "欧洲"]), "北欧与欧洲"),
    (lambda r: r.update(region=["日本", "中国", "欧洲", "北欧"]), "超过"),
    (lambda r: r.update(era=[]), "pending"),
    (lambda r: r.update(region=[]), "pending"),
    (lambda r: r.update(region="日本"), "region"),
    (lambda r: r.update(dynasty=["宋"], region=["日本"]), "不含中国"),
    (lambda r: r.update(name="另一个名字"), "名称对不上"),
])
def test_bad_values_are_rejected(mutate, needle):
    assert needle in mini_bad(lambda t: mutate(row_of(t, "C01")))


@pytest.mark.parametrize("field", ["family", "region", "era", "dynasty", "name", "notes", "confidence"])
def test_missing_field_is_rejected(field):
    msg = mini_bad(lambda t: row_of(t, "C01").pop(field))
    assert "C01" in msg and field in msg


def test_extra_field_in_a_row_is_rejected():
    assert "多" in mini_bad(lambda t: row_of(t, "C01").update(legacy="x"))


def test_pending_must_be_declared_real_and_fully_empty():
    assert "pending" in mini_bad(pending={})                                      # 空值没登记待定
    assert "C555" in mini_bad(pending={**PENDING, "C555": "x"})                   # 登记了不存在的画风
    assert "C555" in mini_bad(pending={**PENDING, "C555": "x"}, mutate_tags=lambda t: t["rows"].append({**copy.deepcopy(row_of(t, "C03")), "id": "C555"}))
    new, _ = mini_apply(lambda t: row_of(t, "C03").update(region=["日本"], era=["20世纪"]))   # 待定画风的标签被忽略并置空
    assert next(s for s in new["styles"] if s["code"] == "C03")["region"] == []
    broken = copy.deepcopy(new)
    next(s for s in broken["styles"] if s["code"] == "C03")["region"] = ["日本"]
    assert any("必须全部留空" in x for x in TX.validate_styles(broken["styles"], broken["taxonomy"]))
    ghost = copy.deepcopy(new)
    ghost["taxonomy"]["pending"] = ["C03", "C555"]
    assert any("C555" in x for x in TX.validate_styles(ghost["styles"], ghost["taxonomy"]))


# 静默覆盖
def test_no_silent_overwrite_of_existing_labels_or_taxonomy_block():
    new, _ = mini_apply()
    with pytest.raises(TX.TaxonomyError, match="拒绝覆盖"):
        TX.apply_labels(copy.deepcopy(new), mini_tags(), vocab=VOCAB, pending=PENDING)
    for key in TX.NEW_KEYS:
        half = mini_catalog()
        half["styles"][1][key] = "旧值" if key == "legacyFamily" else ["中国"]
        with pytest.raises(TX.TaxonomyError, match="拒绝覆盖") as e:
            TX.apply_labels(half, mini_tags(), vocab=VOCAB, pending=PENDING)
        assert "C02" in str(e.value)
    only_block = mini_catalog()
    only_block["taxonomy"] = {"version": 1}
    with pytest.raises(TX.TaxonomyError, match="已含 taxonomy"):
        TX.apply_labels(only_block, mini_tags(), vocab=VOCAB, pending=PENDING)


# 保留其他字段
def test_unchanged_checker_catches_edits_outside_the_label_fields():
    base = mini_catalog()
    new, _ = mini_apply()

    def edited(mutate):
        c = copy.deepcopy(new)
        mutate(c)
        return TX.unchanged_problems(base, c)
    assert edited(lambda c: None) == []
    assert any("C01" in x for x in edited(lambda c: c["styles"][0].update(uses=["改了"])))
    assert any("C01" in x for x in edited(lambda c: c["styles"][0]["extra"]["k"].append(9)))
    assert any("C01" in x for x in edited(lambda c: c["styles"][0].update(zh="改名")))
    assert any("C01" in x for x in edited(lambda c: c["styles"][0].update(legacyFamily="不是原 family")))
    assert any("C01" in x for x in edited(lambda c: c["styles"][0].update(extra_new=1)))   # 新增了标签字段以外的字段
    assert any("顶层" in x for x in edited(lambda c: c["removed"].append("C999")))
    assert any("顶层" in x for x in edited(lambda c: c.update(version=2)))
    assert any("顺序或数量" in x for x in edited(lambda c: c["styles"].pop()))
    assert any("顺序或数量" in x for x in edited(lambda c: c["styles"].reverse()))
    assert any("字段顺序" in x for x in edited(lambda c: c["styles"].__setitem__(0, dict(reversed(list(c["styles"][0].items()))))))
