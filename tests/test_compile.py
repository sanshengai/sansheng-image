import copy
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import compile as CP  # noqa: E402
from stylebook import palette as PL  # noqa: E402
from stylebook.sizes import gen_size  # noqa: E402

FREE = {
    "code": "C99", "revision": 2,
    "name": {"zh": "测试风格", "en": "Test Style"},
    "family": "测试", "fit": ["讲故事"], "visibility": "public",
    "source": {"from": "单元测试", "license": "original"},
    "essence": ["第一条命门特征", "第二条命门特征", "第三条命门特征"],
    "recipe": {"positive": "Soft watercolour storybook illustration with visible paper grain and loose ink lines.",
               "hard_constraints": ["Keep large calm areas of empty paper."]},
    "prompt_bans": ["neon", "dark background"],
    "palette": {"recolor": "free", "colors": [{"name": "测试蓝 test blue", "hex": "#1F6F8B"}]},
    "qa": {"must_see": ["必须看到的一", "必须看到的二"], "must_not_see": ["不许看到的一", "不许看到的二"]},
}
LOCKED = dict(copy.deepcopy(FREE), code="C98", palette={"recolor": "locked", "colors": [{"name": "测试蓝 test blue", "hex": "#1F6F8B"}]})

M = {
    "style": "C99", "model": "gpt-image-2", "format": "xhs-carousel",
    "content": {
        "subject": "a grandmother and her grandson making dumplings",
        "relations": "the grandmother on the left looks at the boy on the right",
        "camera": {"shot": "medium shot", "angle": "eye-level", "lens": "50mm", "focus": "on the boy", "composition": "rule of thirds"},
        "lighting": "soft morning window light from the left",
        "background": "a small kitchen with a wooden table",
        "purpose": "an illustration for a family story",
    },
    "structure": "flow", "density": "balanced",
    "palette": {"family": "macaron", "light": 1, "sat": 0},
    "text": {"mode": "native", "items": [{"text": "番茄工作法", "role": "title", "position": "top"}]},
}



def real(refs):
    """自动附上的无字色带不算「用户或合同给的参考图」。"""
    return [r for r in refs if r["role"] != "palette_swatch"]


def c(m=None, contract=FREE):
    return CP.compile_manifest(copy.deepcopy(m or M), contract=copy.deepcopy(contract))


def test_deterministic():
    a, b = c(), c()
    assert a.prompt == b.prompt and a.manifest_hash == b.manifest_hash


@pytest.mark.parametrize("already_prefixed", [False, True])
def test_style_lock_has_one_heading_and_keeps_constraints_first(already_prefixed):
    contract = copy.deepcopy(FREE)
    positive = contract["recipe"]["positive"]
    if already_prefixed:
        contract["recipe"]["positive"] = "Visual style (follow exactly): " + positive
    contract["recipe"]["constraints_first"] = True
    prompt = c(contract=contract).prompt
    assert prompt.count("Visual style (follow exactly):") == 1
    assert "Visual style (follow exactly): Most important:" in prompt
    assert positive in prompt


def test_purpose_and_mood_remain_separate_without_input_punctuation():
    manifest = copy.deepcopy(M)
    manifest["content"].update(purpose="Explain a decision", mood="calm and reassuring")
    assert "Purpose: Explain a decision; Mood: calm and reassuring" in c(manifest).prompt


def test_overlay_reservation_uses_actual_final_boxes_and_rejects_bad_geometry():
    m = copy.deepcopy(M)
    m["text"] = {"mode": "overlay", "reserve": "the card interior", "items": [
        {"text": "精确文案", "box": [0.25, 0.17, 0.69, 0.11], "require_blank": True}]}
    first = c(m)
    assert "[[0.25,0.17,0.69,0.11]]" in first.prompt
    m["text"]["items"][0]["box"] = [0.25, 0.3, 0.69, 0.11]
    changed = c(m)
    assert "[[0.25,0.3,0.69,0.11]]" in changed.prompt
    assert first.prompt != changed.prompt
    m["text"]["items"][0]["box"] = [0.9, 0.3, 0.69, 0.11]
    with pytest.raises(CP.CompileError, match="overlay"):
        c(m)


def test_direct_manifest_rejects_title_too_long_for_square_thumbnail():
    m = copy.deepcopy(M)
    m["format"] = "wechat-cover-square"
    m["text"]["items"] = [{"role": "title", "text": "Jev：要决策，不要文本"}]
    with pytest.raises(CP.CompileError, match="超过.*上限 8 字"):
        c(m)
    m["text"]["items"] = [{"role": "title", "text": "Jev 秒选"}]
    assert "Jev 秒选" in c(m).prompt


def test_fixed_order():
    p = c().prompt
    marks = ["Visual style (follow exactly):", "Subject:", "Spatial relationships:", "Camera:", "Lighting:",
             "Colour palette", "Background:", "Layout structure:", "Information density", "Purpose:",
             "Text in the image", "Aspect ratio", "Keep large calm areas"]
    idx = [p.index(m) for m in marks]
    assert idx == sorted(idx), list(zip(marks, idx))


def test_lock_layer_verbatim():
    assert FREE["recipe"]["positive"] in c().prompt


def test_structure_prompt_only_on_explicit_matching_structure():
    contract = copy.deepcopy(FREE)
    contract["recipe"]["structure_prompts"] = {
        "flow": "Keep flow panels in a simple straight reading order with visible arrowheads.",
    }
    flow = c(contract=contract).prompt
    assert "Style layout for flow (follow exactly):" in flow
    assert contract["recipe"]["structure_prompts"]["flow"] in flow
    no_structure = copy.deepcopy(M)
    no_structure.pop("structure")
    assert "Style layout for flow" not in c(no_structure, contract=contract).prompt
    other_structure = copy.deepcopy(M)
    other_structure["structure"] = "timeline"
    assert "Style layout for flow" not in c(other_structure, contract=contract).prompt


def test_content_inventory_is_literal_and_optional():
    baseline = c()
    m = copy.deepcopy(M)
    m["content"]["inventory"] = "Exactly two people and one cup; no background coworker."
    compiled = c(m)
    assert "Content inventory (literal): Exactly two people and one cup; no background coworker." in compiled.prompt
    assert compiled.prompt.index("Subject:") < compiled.prompt.index("Content inventory (literal):") < compiled.prompt.index("Spatial relationships:")
    assert compiled.manifest_hash != baseline.manifest_hash
    for bad in ("", "  ", ["one cup"]):
        m["content"]["inventory"] = bad
        with pytest.raises(CP.CompileError, match="content.inventory"):
            c(m)


def test_bans_intercepted():
    m = copy.deepcopy(M)
    m["content"]["background"] = "a Dark Background with neon signs"
    with pytest.raises(CP.CompileError, match="冲突词"):
        c(m)


def test_locked_palette_refuses_recolor():
    with pytest.raises(CP.CompileError, match="锁定"):
        c(contract=LOCKED)
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig", "light": 1}
    with pytest.raises(CP.CompileError, match="锁定"):
        c(m, contract=LOCKED)


def test_free_palette_lighter():
    base = next(p for p in PL.palettes()["palettes"] if p["id"] == "macaron")["colors"]
    lighter = PL.resolve("macaron", light=1)
    assert all(PL.luma(l["hex"]) >= PL.luma(b["hex"]) for l, b in zip(lighter, base))
    assert lighter[0]["hex"] in c().prompt


def test_dialects():
    m = copy.deepcopy(M)
    m["references"] = [{"path": "a.png", "role": "identity"}]
    g = c(dict(m, model="gpt-image-2")).prompt
    assert g.startswith("Image 1: character identity reference") and "「番茄工作法」" in g
    ge = c(dict(m, model="gemini-3-pro-image")).prompt
    assert ge.startswith("The first image is a character identity reference") and "Visual style (follow exactly):" not in ge
    sd = c(dict(m, model="seedream")).prompt
    assert sd.startswith("图一：角色身份参考")
    mj = c(dict(m, model="midjourney"))
    assert "--ar 3:4" in mj.prompt and "--no neon" in mj.prompt


def test_composition_reference_keeps_style_lock():
    m = copy.deepcopy(M)
    m["references"] = [{"path": "layout-guide.png", "role": "composition"}]
    g = c(dict(m, model="gpt-image-2"))
    assert g.prompt.startswith("Image 1: composition guide only")
    assert "do not copy its medium, colors, texture" in g.prompt
    assert FREE["recipe"]["positive"] in g.prompt
    assert real(g.references) == m["references"]
    sd = c(dict(m, model="seedream"))
    assert sd.prompt.startswith("图一：只作构图参考")


def test_face_identity_reference_does_not_lock_clothing():
    m = copy.deepcopy(M)
    m["references"] = [{"path": "face-crop.png", "role": "identity_face"}]
    g = c(dict(m, model="gpt-image-2"))
    assert real(g.references) == m["references"]
    assert g.prompt.startswith("Image 1: face identity reference only")
    assert "take clothing, pose, body framing, setting and props only from the subject instructions" in g.prompt
    sd = c(dict(m, model="seedream"))
    assert sd.prompt.startswith("图一：只作面部身份参考")


@pytest.mark.parametrize("model", ["gpt-image-2", "gemini-3-pro-image", "seedream", "flux", "midjourney"])
def test_anchor_isolation_is_compiled_only_when_anchor_is_used(model):
    contract = copy.deepcopy(FREE)
    contract["anchor"] = {
        "file": "anchor.png",
        "sha256": "a" * 64,
        "isolation": "Use only the pale clay material; never copy the dog or blanket.",
    }
    m = copy.deepcopy(M)
    m["model"] = model
    m["references"] = [{"path": "other-style.png", "role": "style"},
                       {"path": "character.png", "role": "identity"}]
    result = c(m, contract=contract)
    assert real(result.references) == [{"path": "anchor.png", "role": "style"},
                                 {"path": "character.png", "role": "identity"}]
    assert contract["anchor"]["isolation"] in result.prompt
    assert result.prompt.count(contract["anchor"]["isolation"]) == 1

    m["use_anchor"] = False
    opted_out = c(m, contract=contract)
    assert real(opted_out.references) == m["references"]
    assert contract["anchor"]["isolation"] not in opted_out.prompt


def test_relative_anchor_resolves_from_contract_file(tmp_path):
    contract = copy.deepcopy(FREE)
    contract["_path"] = str(tmp_path / "styles" / "C99" / "contract.json")
    contract["anchor"] = {"file": "anchor.png", "sha256": "a" * 64}
    result = c(copy.deepcopy(M), contract=contract)
    assert real(result.references) == [{"path": str(tmp_path / "styles" / "C99" / "anchor.png"),
                                  "role": "style"}]


def test_text_none_and_overlay():
    m = copy.deepcopy(M)
    m["text"] = {"mode": "none"}
    assert "No text, letters or captions anywhere" in c(m).prompt
    m["text"] = {"mode": "overlay", "reserve": "the upper third"}
    assert "empty space at the upper third" in c(m).prompt


def test_hybrid_compiles_native_title_but_reserves_exact_command():
    m = copy.deepcopy(M)
    m["text"] = {"mode": "hybrid", "reserve": "a shallow inset below the title", "items": [
        {"render": "native", "role": "title", "text": "第一步：安装", "position": "top"},
        {"render": "overlay", "role": "code", "text": "uv tool install notebooklm-mcp-cli",
         "box": [0.07, 0.37, 0.86, 0.09], "font_px": 58, "min_px": 58},
    ]}
    prompt = c(m).prompt
    assert "「第一步：安装」" in prompt
    assert "a shallow inset below the title" in prompt
    assert "uv tool install notebooklm-mcp-cli" not in prompt
    assert "no letters, symbols or pseudo-text in that reserved space" in prompt
    bad = copy.deepcopy(m)
    bad["text"].pop("reserve")
    with pytest.raises(CP.CompileError, match="reserve"):
        c(bad)


def test_hybrid_text_style_is_scoped_to_hybrid_prompt():
    k = copy.deepcopy(FREE)
    k["recipe"]["text_mode"] = ["native", "hybrid"]
    k["recipe"]["text_style"] = "Native titles and labels are dimensional matte clay letters."
    k["recipe"]["text_style_hybrid"] = "Only the title is dimensional matte clay; reserve blank insets for exact operational text."
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig"}
    native_before = CP.compile_manifest(m, {**k, "recipe": {key: value for key, value in k["recipe"].items()
                                                         if key != "text_style_hybrid"}}).prompt
    assert CP.compile_manifest(m, k).prompt == native_before
    m["text"] = {"mode": "hybrid", "reserve": "one shallow blank inset", "items": [
        {"render": "native", "role": "title", "text": "第一步：安装"},
        {"render": "overlay", "role": "code", "text": "uv tool install notebooklm-mcp-cli",
         "box": [0.07, 0.37, 0.86, 0.09], "font_px": 58, "min_px": 58},
    ]}
    hybrid = CP.compile_manifest(m, k).prompt
    assert "Only the title is dimensional matte clay" in hybrid
    assert "Native titles and labels are dimensional matte clay" not in hybrid


def test_opaque_background_is_compiled_except_for_transparent_format():
    assert "background fully opaque across the whole canvas" in c().prompt
    m = copy.deepcopy(M)
    m.pop("format")  # 矩阵题等没有显式格式时也按普通整图处理
    assert "background fully opaque across the whole canvas" in c(m).prompt
    m["text"]["items"] = [{"role": "title", "text": "番茄法"}]
    m["format"] = "sticker-grid"
    assert "background fully opaque across the whole canvas" not in c(m).prompt
    m["format"] = "sticker-single"
    single = c(m)
    assert "background fully opaque across the whole canvas" not in single.prompt
    assert "One complete isolated sticker character" in single.prompt
    assert single.export_px == (512, 512)


def test_revision_lock():
    with pytest.raises(CP.CompileError, match="锁定在"):
        c(dict(copy.deepcopy(M), style="C99@r1"))
    assert c(dict(copy.deepcopy(M), style="C99@r2")).style == "C99@r2"


def test_bad_inputs():
    for bad in ({"structure": "spiral"}, {"density": "huge"}, {"references": [{"path": "x", "role": "mood"}]}):
        with pytest.raises(CP.CompileError):
            c(dict(copy.deepcopy(M), **bad))
    m = copy.deepcopy(M)
    m["content"].pop("subject")
    with pytest.raises(CP.CompileError, match="subject"):
        c(m)


def test_gen_size_rules():
    for ratio in ("1:1", "3:4", "16:9", "2.35:1", "9:16", "1.91:1"):
        w, h = gen_size(ratio)
        assert w % 16 == 0 and h % 16 == 0 and max(w, h) <= 3840 and max(w, h) / min(w, h) <= 3


@pytest.mark.skipif(not os.environ.get("SANSHENG_IMAGE_PROFILE"), reason="未设置私有 profile")
def test_private_s01_compiles_and_blocks_dark():
    m = dict(copy.deepcopy(M), style="S01", palette={"family": "orig"})
    m["content"]["background"] = "a warm ivory tabletop"
    p = CP.compile_manifest(m).prompt
    lock = p.split("\nSubject:")[0]
    assert "A warm ivory background with a high-key pastel palette" in lock and "#" not in lock
    assert "extruded clay letters" in p  # 这份清单有原生文字，文字材质句要附上
    m["content"]["background"] = "a dark background"
    with pytest.raises(CP.CompileError):
        CP.compile_manifest(m)


def test_ban_matches_across_hyphen_and_spacing():
    """09-25 实跑：冲突词 mustard yellow 被 mustard-yellow 绕过。"""
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig"}
    m["content"] = {"subject": "a woman in a Neon-Lit street"}
    with pytest.raises(CP.CompileError):
        CP.compile_manifest(m, FREE)
    k = copy.deepcopy(FREE)
    k["prompt_bans"] = ["dark background"]
    m["content"] = {"subject": "a cat on a dark_background"}
    with pytest.raises(CP.CompileError):
        CP.compile_manifest(m, k)
    m["content"] = {"subject": "a cat before a darkened backdrop"}
    CP.compile_manifest(m, k)  # 不是同一个词，不拦


def test_text_style_only_when_text_is_drawn():
    k = copy.deepcopy(FREE)
    k["recipe"]["text_style"] = "Text is sculpted as extruded clay letters standing free."
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig"}
    with_text = CP.compile_manifest(m, k).prompt
    assert "extruded clay letters" in with_text
    m["text"] = {"mode": "none"}
    assert "extruded clay letters" not in CP.compile_manifest(m, k).prompt
    m["text"] = {"mode": "overlay"}
    assert "extruded clay letters" not in CP.compile_manifest(m, k).prompt


def test_label_role_is_not_called_label():
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig"}
    m["text"] = {"mode": "native", "items": [{"role": "title", "text": "番茄工作法"}, {"role": "label", "text": "专注25分钟"}]}
    p = CP.compile_manifest(m, FREE).prompt
    assert "short text: 「专注25分钟」" in p and "label:" not in p and "title: 「番茄工作法」" in p
    assert "Do not add step numbers or unrequested digits on props" in p
    assert "show sequence with arrows or unnumbered marks" in p


@pytest.mark.parametrize("ratio", ["1:1", "3:4", "4:3", "16:9", "9:16", "2.35:1", "1.91:1", "3:1", "1:3", "5:4"])
@pytest.mark.parametrize("long_edge", [512, 1024, 1536, 1920, 2048, 3000, 3840])
def test_gen_size_limits(ratio, long_edge):
    w, h = gen_size(ratio, "gpt-image-2", long_edge)
    assert w % 16 == 0 and h % 16 == 0
    assert 655_360 <= w * h <= 8_294_400
    assert max(w, h) <= 3840 and max(w, h) / min(w, h) <= 3.0 + 1e-9


def test_format_export_size_drives_generation_size():
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig"}
    m["format"] = "podcast-cover"
    m["text"] = {"mode": "none"}
    c = CP.compile_manifest(m, FREE)
    assert c.size == (2880, 2880) and c.export_px == (3000, 3000)


def test_format_compose_panels_and_fixes():
    import re as _re
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig"}
    m["format"] = "comic-4panel"
    m["text"] = {"mode": "none"}
    m["content"] = {"subject": "A girl tries to bake a cake.", "panels": ["She reads the recipe.", "Flour everywhere.",
                                                                          "The cake collapses.", "She laughs and eats it anyway."],
                    "relations": "The same collapsed cake from panel 3 stays collapsed in panel 4."}
    m["fixes"] = ["the girl keeps her round glasses in every panel"]
    p = CP.compile_manifest(m, FREE).prompt
    assert "Panel 1: She reads the recipe." in p and "Panel 4:" in p
    assert "2×2 grid of equal panels" in p
    assert "Correct these points from the previous attempt: the girl keeps her round glasses in every panel." in p
    for fmt in PL_FORMATS:  # 提示词里不再混进中文的安全区说明（会被画成字）
        m2 = dict(copy.deepcopy(M), format=fmt, palette={"family": "orig"}, text={"mode": "none"})
        if fmt == "comic-4panel":
            m2["content"]["panels"] = ["First.", "Second.", "Third.", "Fourth."]
        body = CP.compile_manifest(m2, FREE).prompt.split("Aspect ratio")[0].split("Visual style")[-1]
        assert not _re.search(r"[一-鿿]", body.replace("番茄工作法", "")), fmt


PL_FORMATS = ["wechat-cover-head", "xhs-cover", "podcast-cover", "picturebook-page", "storyboard-frame", "vertical-cover"]


@pytest.mark.parametrize("text_mode", ["native", "overlay", "none"])
def test_center_square_requirement_reaches_generation_prompt(text_mode):
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig"}
    m["text"] = {"mode": text_mode}
    if text_mode == "native":
        m["text"]["items"] = [
            {"role": "title", "text": "整理书桌", "position": "top"},
            {"role": "footer", "text": "留出空位", "position": "bottom"},
        ]
    compiled = CP.compile_manifest(m, FREE)
    assert "every requested title, label and footer" in compiled.prompt
    assert "inside the centered square crop" in compiled.prompt
    assert "reserved text areas" in compiled.prompt
    assert compiled.export_px == (1242, 1656)
    assert "Aspect ratio 3:4." in compiled.prompt
    if text_mode == "native":
        assert "整理书桌" in compiled.prompt and "留出空位" in compiled.prompt
    elif text_mode == "none":
        assert "No text, letters or captions" in compiled.prompt


@pytest.mark.parametrize("fmt", ["infographic", None])
def test_no_center_square_requirement_without_format_safe_zone(fmt):
    m = copy.deepcopy(M)
    m["palette"] = {"family": "orig"}
    m["text"] = {"mode": "none"}
    if fmt:
        m["format"] = fmt
    else:
        m.pop("format")
    assert "inside the centered square crop" not in CP.compile_manifest(m, FREE).prompt


@pytest.mark.parametrize("fmt,aspect,expected", [
    ("xhs-cover", None, "x=0.00% to 100.00% and y=12.50% to 87.50%"),
    ("wechat-cover-head", None, "x=28.72% to 71.28% and y=0.00% to 100.00%"),
    ("xhs-cover", "1:1", "x=12.50% to 87.50% and y=12.50% to 87.50%"),
])
def test_center_square_prompt_describes_final_preview_bounds(fmt, aspect, expected):
    m = copy.deepcopy(M)
    m.update(format=fmt, palette={"family": "orig"}, text={"mode": "none"})
    if aspect:
        m["aspect"] = aspect
    compiled = CP.compile_manifest(m, FREE)
    assert expected in compiled.prompt
    assert "relative to this inner square" in compiled.prompt
    assert "not visible lines" in compiled.prompt


def test_disabled_anchor_rejects_forced_use_and_omits_default():
    contract = copy.deepcopy(FREE)
    contract["anchor"] = {"file": "bad.png", "enabled": False, "isolation": "BAD_REFERENCE"}
    assert real(c(contract=contract).references) == []
    assert "BAD_REFERENCE" not in c(contract=contract).prompt
    m = copy.deepcopy(M)
    m["use_anchor"] = True
    with pytest.raises(CP.CompileError, match="已停用"):
        c(m, contract=contract)
    # Mutation at the consumed switch restores the known bad reference.
    contract["anchor"]["enabled"] = True
    assert c(m, contract=contract).references[0]["path"] == "bad.png"


def test_ppt_slide_text_limits():
    """整页幻灯片：标题 + 副标题 + 至多 5 条要点；超出必须被拒（反例）。"""
    base = {"style": "C42", "format": "ppt", "content": {"subject": "A team reviewing a growth chart"},
            "text": {"mode": "native", "items": [{"role": "title", "text": "季度复盘"}, {"role": "subtitle", "text": "增长来自两个渠道"}]
                     + [{"role": "body", "text": f"要点{i}"} for i in range(1, 6)]}}
    out = CP.compile_manifest(base)
    assert "季度复盘" in out.prompt and "要点5" in out.prompt
    too_many = copy.deepcopy(base)
    too_many["text"]["items"].append({"role": "body", "text": "要点6"})
    with pytest.raises(CP.CompileError, match="要点最多 5 条"):
        CP.compile_manifest(too_many)
    long_body = copy.deepcopy(base)
    long_body["text"]["items"][2]["text"] = "很长" * 20
    with pytest.raises(CP.CompileError, match="超过"):
        CP.compile_manifest(long_body)


def test_chosen_palette_attaches_a_textless_swatch_as_last_reference():
    got = c(copy.deepcopy(M))  # M 默认选了 macaron 色系
    swatches = [r for r in got.references if r["role"] == "palette_swatch"]
    assert len(swatches) == 1 and got.references[-1] is swatches[0]
    assert Path(swatches[0]["path"]).is_file()
    assert "no subject, no text" in got.prompt
    again = c(copy.deepcopy(M))
    assert again.references[-1]["path"] == swatches[0]["path"]  # 同一组颜色同一个文件
    plain = copy.deepcopy(M)
    plain["palette"] = {"family": "orig"}
    assert not [r for r in c(plain).references if r["role"] == "palette_swatch"]


@pytest.mark.parametrize("mode", ["none", "overlay", "native", "hybrid"])
def test_ppt_composition_respects_text_mode(mode):
    m = copy.deepcopy(M)
    m["format"] = "ppt"
    if mode == "overlay":
        m["text"] = {"mode": mode, "reserve": "the plain pale left half",
                     "items": [{"text": "活动企划", "role": "title",
                                "box": [0.07, 0.3, 0.45, 0.18],
                                "font_px": 64, "min_px": 24, "require_blank": True}]}
    elif mode == "hybrid":
        m["text"] = {"mode": mode, "reserve": "the plain pale left half",
                     "items": [{"text": "活动企划", "role": "title", "render": "native"},
                               {"text": "示例", "role": "label", "render": "overlay",
                                "box": [0.07, 0.8, 0.3, 0.08], "font_px": 32, "min_px": 24}]}
    else:
        m["text"] = {"mode": mode, "items": [{"text": "活动企划", "role": "title"}]}
    prompt = c(m).prompt
    native_instruction = "the text is part of the slide design and must be exactly the given text"
    if mode in ("none", "overlay"):
        assert native_instruction not in prompt
        assert "Do not render any text, digits or pseudo-characters" in prompt
        assert "5% margins" in prompt
    else:
        assert native_instruction in prompt


@pytest.mark.parametrize("with_footer", [False, True])
def test_infographic_does_not_invent_a_closing_sentence(with_footer):
    m = copy.deepcopy(M)
    m["format"] = "infographic"
    m["palette"] = {"family": "orig"}
    m["text"] = {"mode": "native", "items": [{"role": "title", "text": "整理", "position": "top"}]}
    if with_footer:
        m["text"]["items"].append({"role": "footer", "text": "用完归位", "position": "bottom"})
    prompt = CP.compile_manifest(m, FREE).prompt
    assert "only when the exact text list explicitly supplies one" in prompt
    assert "Never invent a closing sentence" in prompt
    assert ("用完归位" in prompt) == with_footer
    assert "and one short closing line at the bottom." not in prompt


# ---- 画风合同 × 格式 × 编译规则的冲突（2026-10-13）----

def _real(style, fmt, items=None, mode=None):
    text = {}
    if items is not None:
        text["items"] = items
    if mode:
        text["mode"] = mode
    return {"style": style, "format": fmt, "content": {"subject": "an old film camera"}, "text": text}


def _role_contract():
    k = copy.deepcopy(FREE)
    k["recipe"].update(
        required_text_roles=["title", "ghost"], optional_text_roles=["tag"],
        role_styles={"ghost": "A faint all-caps English line set behind the headline, low contrast.",
                     "tag": "A small rounded chip carrying exactly the given words."},
        text_mode=["native", "none"])
    return k


def test_required_text_role_missing_is_rejected_and_present_counts_as_requested_text():
    k = _role_contract()
    m = _real("C99", "xhs-carousel", [{"role": "title", "text": "旧相机"}])
    with pytest.raises(CP.CompileError, match="ghost"):
        CP.compile_manifest(m, k)
    m["text"]["items"].append({"role": "ghost", "text": "OLD CAMERA"})
    prompt = CP.compile_manifest(m, k).prompt
    assert "ghost: 「OLD CAMERA」" in prompt and "(no other text)" in prompt
    assert "faint all-caps English line" in prompt  # 角色呈现方式只在有该角色文字时附上
    assert "small rounded chip" not in prompt       # 可选角色没给文字就不描述


def test_required_role_check_skips_textless_but_covers_any_requested_text():
    k = _role_contract()
    # 没写 text.mode 也没给字：格式默认档也不出字，提示词就是「无字」，不要求合同的文字角色
    assert "No text, letters or captions" in CP.compile_manifest(_real("C99", "xhs-carousel"), k).prompt
    with pytest.raises(CP.CompileError, match="title"):  # 给了字（哪怕只有别的角色）就必须补齐
        CP.compile_manifest(_real("C99", "xhs-carousel", [{"role": "subtitle", "text": "副标题"}]), k)
    with pytest.raises(CP.CompileError, match="title"):  # 显式写了出字档也一样
        CP.compile_manifest(_real("C99", "xhs-carousel", mode="native"), k)
    prompt = CP.compile_manifest(_real("C99", "music-cover"), k).prompt  # 无字格式不要求字
    assert "No text, letters or captions" in prompt and "ghost" not in prompt
    CP.compile_manifest(_real("C99", "xhs-carousel", mode="none"), k)


def test_real_c77_requires_ghost_and_prints_it_in_exact_text():
    with pytest.raises(CP.CompileError, match="ghost"):
        CP.compile_manifest(_real("C77", "wechat-cover-head", [{"role": "title", "text": "旧相机"}]))
    ok = CP.compile_manifest(_real("C77", "wechat-cover-head",
                                   [{"role": "title", "text": "旧相机"}, {"role": "ghost", "text": "OLD CAMERA"}]))
    assert "ghost: 「OLD CAMERA」" in ok.prompt
    assert "ghost line" not in ok.prompt.split("Text in the image")[0]  # 锁定层不再自带影字
    assert "No text, letters or captions" in CP.compile_manifest(_real("C77", "music-cover")).prompt


@pytest.mark.parametrize("style,missing,items", [
    ("C90", "brand", [{"role": "title", "text": "第三期"}, {"role": "tag", "text": "系列"}]),
    ("C90", "tag", [{"role": "title", "text": "第三期"}, {"role": "brand", "text": "叁笙"}]),
    ("C83", "title", [{"role": "label", "text": "慢"}]),
])
def test_real_contract_missing_role_is_rejected(style, missing, items):
    with pytest.raises(CP.CompileError, match=missing):
        CP.compile_manifest(_real(style, "wechat-cover-head", items))


def test_optional_roles_are_text_only_when_given():
    base = [{"role": "title", "text": "编辑部"}]
    plain = CP.compile_manifest(_real("C76", "xhs-cover", base)).prompt
    withissue = CP.compile_manifest(_real("C76", "xhs-cover", base + [{"role": "label", "text": "x"}, {"role": "issue", "text": "VOL.3"}])).prompt
    assert "issue-number" not in plain.split("Text in the image")[1] and "VOL.3" in withissue
    assert "issue-number" in withissue.split("Text in the image")[1]
    # 配方正文里不再自带只有清单才能提供的文字
    from stylebook import contract as CT
    for code, words in (("C76", "issue-number"), ("C81", "hand-written marginal notes"), ("C77", "ghost line"), ("C90", "brand line")):
        assert words not in CT.load(code)["recipe"]["positive"]


def test_c89_has_no_numeral_badges_that_collide_with_the_no_digits_clause():
    from stylebook import contract as CT
    assert "number" not in CT.load("C89")["recipe"]["positive"].replace("no numerals", "")
    prompt = CP.compile_manifest(_real("C89", "infographic", [{"role": "title", "text": "流程"}])).prompt
    assert "carrying no numerals" in prompt and "unrequested digits" in prompt


def test_wide_canvas_only_layout_is_rejected_for_center_square_formats():
    k = copy.deepcopy(FREE)
    k["recipe"]["layout"] = {"text_zone": "left", "hero_zone": "right", "wide_canvas_only": True}
    with pytest.raises(CP.CompileError, match="居中方形"):
        CP.compile_manifest(_real("C99", "wechat-cover-head"), k)
    with pytest.raises(CP.CompileError, match="居中方形"):
        CP.compile_manifest(_real("C99", "xhs-cover"), k)
    CP.compile_manifest(_real("C99", "x-image"), k)  # 不裁方形的格式照常
    k["recipe"]["layout"]["wide_canvas_only"] = False
    CP.compile_manifest(_real("C99", "wechat-cover-head"), k)


def test_declared_zones_are_placed_inside_the_center_square():
    items = [{"role": "title", "text": "旧相机"}, {"role": "ghost", "text": "OLD CAMERA"}]
    cover = CP.compile_manifest(_real("C77", "wechat-cover-head", items)).prompt
    assert "text zone is the left part of the square" in cover and "hero zone the right part of the square" in cover
    assert "left half" not in cover.split("Production")[0].split("Text in the image")[0]
    x = CP.compile_manifest(_real("C77", "x-image", items)).prompt
    assert "layout zones both sit inside" not in x  # 没有方形裁切的格式不加


@pytest.mark.parametrize("style,fmt,items,mode", [
    ("C08", "wechat-cover-head", [{"role": "title", "text": "x"}], None),   # 默认 native，合同只有 none/overlay
    ("C91", "picturebook-page", [{"role": "title", "text": "x", "box": [0.1, 0.1, 0.5, 0.2], "font_px": 40, "min_px": 20}], None),  # 默认 overlay，合同只有 none/native
    ("C95", "article-illustration", [{"role": "title", "text": "x"}], None),  # 合同只有 none
    ("C96", "article-illustration", None, "overlay"),
])
def test_text_mode_not_in_contract_is_a_compile_error(style, fmt, items, mode):
    with pytest.raises(CP.CompileError, match="文字档位只支持"):
        CP.compile_manifest(_real(style, fmt, items, mode))


def test_text_mode_error_names_the_allowed_modes_and_the_fix_compiles():
    with pytest.raises(CP.CompileError) as e:
        CP.compile_manifest(_real("C91", "picturebook-page", [{"role": "title", "text": "x", "box": [0.1, 0.1, 0.5, 0.2], "font_px": 40, "min_px": 20}]))
    assert "['none', 'native']" in str(e.value) and "text.mode" in str(e.value)
    # 默认档不论是 native 还是 overlay，没写档位又没给字都是无字图，同一口径放行
    CP.compile_manifest(_real("C91", "picturebook-page"))
    CP.compile_manifest(_real("C91", "picturebook-page", mode="none"))
    CP.compile_manifest(_real("C08", "wechat-cover-head", [{"role": "title", "text": "x", "box": [0.1, 0.1, 0.5, 0.2],
                                                         "font_px": 40, "min_px": 20}], "overlay"))
    # 不出字永远放行；只靠格式默认落到 native 又没给字，不算在要求这个画风写字
    CP.compile_manifest(_real("C95", "article-illustration"))
    CP.compile_manifest(_real("C95", "article-illustration", mode="none"))
    CP.compile_manifest(_real("C08", "wechat-cover-head"))


def test_hybrid_is_allowed_when_native_and_overlay_are_both_supported():
    k = copy.deepcopy(FREE)
    k["recipe"]["text_mode"] = ["native", "overlay"]
    m = _real("C99", "ppt")
    m["text"] = {"mode": "hybrid", "reserve": "the plain pale left half",
                 "items": [{"text": "活动企划", "role": "title", "render": "native"},
                           {"text": "示例", "role": "label", "render": "overlay",
                            "box": [0.07, 0.8, 0.3, 0.08], "font_px": 32, "min_px": 24}]}
    CP.compile_manifest(m, k)
    k["recipe"]["text_mode"] = ["native"]
    with pytest.raises(CP.CompileError, match="文字档位只支持"):
        CP.compile_manifest(m, k)


C77_TEXT = [{"role": "title", "text": "旧相机"}, {"role": "ghost", "text": "OLD CAMERA"}]


def test_c77_prose_no_longer_hardcodes_left_and_right():
    prompt = CP.compile_manifest(_real("C77", "x-image", C77_TEXT)).prompt
    style = prompt.split("Visual style (follow exactly):")[1].split("\n")[0]
    assert "on the left" not in style and "on the right" not in style and "side by side" not in style
    assert "Layout: the text zone is on the left and the hero zone on the right" in prompt


def test_manifest_layout_overrides_contract_zones_inside_the_square():
    m = {**_real("C77", "wechat-cover-head", C77_TEXT), "layout": {"text_zone": "top", "hero_zone": "bottom"}}
    prompt = CP.compile_manifest(m).prompt
    assert "text zone is the top part of the square and its hero zone the bottom part" in prompt
    assert "left part of the square" not in prompt


@pytest.mark.parametrize("layout", [{"text_zone": "top", "hero_zone": "right"}, {"text_zone": "middle", "hero_zone": "top"},
                                    {"text_zone": "top", "hero_zone": "bottom", "gap": 1}, {}, "top"])
def test_bad_manifest_layout_is_rejected(layout):
    with pytest.raises(CP.CompileError, match="layout|版式分区"):
        CP.compile_manifest({**_real("C77", "x-image", C77_TEXT), "layout": layout})


def test_layout_override_needs_a_contract_that_declares_zones():
    with pytest.raises(CP.CompileError, match="没有声明文字区"):
        CP.compile_manifest({**_real("C95", "article-illustration"), "layout": {"text_zone": "top", "hero_zone": "bottom"}})
