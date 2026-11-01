"""版本化选择记录：页面、Agent 与计划共用的确定性边界。

语义规划仍由宿主读原文完成。本模块只验证选择、记录来源及范围、计算成图影响。
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from . import contract as CT
from . import data as D
from . import palette as PAL
from . import stylecode as SC

VERSION = 1
SOURCES = {"explicit", "project", "preference", "observed", "author", "factory"}


class SelectionError(ValueError):
    pass


def _keys(value: dict, allowed: set[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise SelectionError(f"{label} 有未知字段：{sorted(extra)}")


def _provenance(value: dict, label: str, scope: str) -> dict:
    source = value.get("source", "explicit")
    if source not in SOURCES:
        raise SelectionError(f"{label}.source 不支持：{source!r}")
    actual_scope = value.get("scope", scope)
    if actual_scope != scope:
        raise SelectionError(f"{label}.scope 必须是 {scope}")
    locked = value.get("locked", source in {"explicit", "project"})
    if type(locked) is not bool:
        raise SelectionError(f"{label}.locked 必须是布尔值")
    return {"source": source, "scope": scope, "locked": locked}


def _style(value: dict, label: str, scope: str) -> dict:
    if not isinstance(value, dict):
        raise SelectionError(f"{label} 必须是对象")
    _keys(value, {"code", "revision", "source", "scope", "locked"}, label)
    code = value.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"(?:C|S)\d{2,3}", code):
        raise SelectionError(f"{label}.code 不是风格码")
    try:
        contract = CT.load(code)
    except (KeyError, CT.ContractError) as exc:
        raise SelectionError(f"{label} 风格不可用：{code}（{exc}）") from exc
    if contract.get("_alias_from"):  # 旧码已并入新码：按新码的现行修订
        code, value = contract["code"], {k: v for k, v in value.items() if k != "revision"}
    rev = value.get("revision", contract["revision"])
    if type(rev) is not int or rev != contract["revision"]:
        raise SelectionError(f"{code} 当前合同是 r{contract['revision']}，选择中的修订 r{rev} 不可用")
    return {"code": code, "revision": rev, **_provenance(value, label, scope)}


def _palette(value: dict, label: str) -> dict:
    if not isinstance(value, dict):
        raise SelectionError(f"{label} 必须是对象")
    _keys(value, {"family", "light", "sat", "custom", "source", "scope", "locked"}, label)
    family = value.get("family", "orig")
    if family not in {p["id"] for p in D.palettes()["palettes"]}:
        raise SelectionError(f"未知色系：{family}")
    light, sat = value.get("light", 0), value.get("sat", 0)
    if type(light) is not int or light not in (-1, 0, 1) or type(sat) is not int or sat not in (-1, 0, 1):
        raise SelectionError("light / sat 只能是 -1、0、1")
    custom = value.get("custom")
    if family == "brand":
        if not isinstance(custom, list) or not custom or any(not isinstance(c, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in custom):
            raise SelectionError("品牌色须提供 custom: [\"#RRGGBB\", ...]")
    elif custom is not None:
        raise SelectionError("只有 brand 色系可以填写 custom")
    result = {"family": family, "light": light, "sat": sat,
              **({"custom": [c.upper() for c in custom]} if custom else {}),
              **_provenance(value, label, "series")}
    try:
        PAL.resolve(family, light, sat, result.get("custom"))
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc
    return result


def _expression(value: dict, label: str, scope: str) -> dict:
    if not isinstance(value, dict):
        raise SelectionError(f"{label} 必须是对象")
    _keys(value, {"form", "structure", "density", "source", "scope", "locked"}, label)
    form, structure = value.get("form", "auto"), value.get("structure", "auto")
    density = value.get("density", "auto")
    if form not in {"auto", "scene", "metaphor", "structure"}:
        raise SelectionError(f"{label}.form 不支持：{form!r}")
    if structure != "auto" and structure not in D.structures():
        raise SelectionError(f"{label}.structure 不支持：{structure!r}")
    if form != "structure" and structure != "auto":
        raise SelectionError(f"{label} 只有结构图能指定结构")
    if density not in {"auto", "sparse", "balanced", "dense"}:
        raise SelectionError(f"{label}.density 不支持：{density!r}")
    if form in {"scene", "metaphor"} and density != "auto":
        raise SelectionError(f"{label} 只有结构图能指定信息密度")
    return {"form": form, "structure": structure, "density": density, **_provenance(value, label, scope)}


def _palette_compatible(style: dict, palette: dict) -> None:
    c = CT.load(style["code"])
    rule = c["palette"]["recolor"]
    if rule == "locked" and (palette["family"] != "orig" or palette["light"] or palette["sat"]):
        raise SelectionError(f"{style['code']} 锁色，不能按当前选择换色")
    if rule == "lightness_only" and (palette["family"] != "orig" or palette["sat"]):
        raise SelectionError(f"{style['code']} 只允许调整原色深浅")
    if palette["family"] == "orig" and (palette["light"] or palette["sat"]) and not c["palette"].get("colors"):
        raise SelectionError(f"{style['code']} 没有登记原色，无法调整原色")


def normalize(raw: dict | str, *, scene: str | None = None, item_ids: set[str] | None = None) -> dict:
    """归一化 v1 JSON、sb1 或 sb2。未知版本、修订、字段和颜色冲突均拒绝。"""
    if isinstance(raw, str):
        legacy = SC.parse(raw)
        problems = SC.validate(legacy)
        if problems:
            raise SelectionError("；".join(problems))
        code, _, rev = legacy.style.partition("@r")
        raw = {"version": VERSION, "scene": legacy.scene or scene,
               "style": {"code": code, **({"revision": int(rev)} if rev else {}), "source": "explicit"},
               "palette": {"family": legacy.palette, "light": legacy.light, "sat": legacy.sat, **({"custom": list(legacy.custom)} if legacy.custom else {}), "source": "explicit"},
               "expression": {"form": "structure" if legacy.structure and legacy.structure != "auto" else "auto",
                              "structure": legacy.structure or "auto", "density": legacy.density or "auto", "source": "explicit"}}
        if legacy.palette == "brand" and not legacy.custom:
            raise SelectionError("旧 sb1 品牌色缺具体 HEX，请改用 selection JSON")
        if legacy.layout:
            raw["poster_layout"] = legacy.layout
        if legacy.variant:
            raw["variant"] = legacy.variant
    if not isinstance(raw, dict):
        raise SelectionError("selection 必须是对象或 sb1/sb2 风格码")
    _keys(raw, {"version", "scene", "series_id", "style", "palette", "expression", "items", "poster_layout", "variant"}, "selection")
    if raw.get("version") != VERSION:
        raise SelectionError(f"不支持的 selection 版本：{raw.get('version')!r}")
    target_scene = raw.get("scene") or scene
    if target_scene not in D.scenes() or (scene and target_scene != scene):
        raise SelectionError(f"场景不可用或与任务不一致：{target_scene!r}")
    series_id = raw.get("series_id")
    if series_id is not None and (not isinstance(series_id, str) or not series_id.strip()):
        raise SelectionError("series_id 必须是非空文字")
    style = _style(raw.get("style"), "style", "series")
    palette = _palette(raw.get("palette", {"family": "orig", "source": "factory"}), "palette")
    _palette_compatible(style, palette)
    expression = _expression(raw.get("expression", {"source": "factory"}), "expression", "series")
    poster = {}
    if raw.get("poster_layout") is not None:
        if raw["poster_layout"] not in D.layouts():
            raise SelectionError(f"未知海报版式：{raw['poster_layout']!r}；可选 {list(D.layouts())}")
        poster["poster_layout"] = raw["poster_layout"]
    if raw.get("variant") is not None:
        variants = (CT.load(style["code"]).get("recipe") or {}).get("variants") or {}
        if raw["variant"] not in variants:
            raise SelectionError(f"{style['code']} 没有手法变体 {raw['variant']!r}" + (f"；可选 {list(variants)}" if variants else "（该画风没有变体）"))
        poster["variant"] = raw["variant"]
    items = raw.get("items", {})
    if not isinstance(items, dict):
        raise SelectionError("items 必须按图片 ID 存储")
    out_items = {}
    for item_id, override in items.items():
        if not isinstance(item_id, str) or not item_id or (item_ids is not None and item_id not in item_ids):
            raise SelectionError(f"未知图片 ID：{item_id!r}")
        if not isinstance(override, dict):
            raise SelectionError(f"items.{item_id} 必须是对象")
        _keys(override, {"style", "expression", "text_direction"}, f"items.{item_id}")
        item = {}
        if "style" in override:
            item["style"] = _style(override["style"], f"items.{item_id}.style", "item")
        if "expression" in override:
            item["expression"] = _expression(override["expression"], f"items.{item_id}.expression", "item")
        if "text_direction" in override:
            direction = override["text_direction"]
            if not isinstance(direction, str) or not direction.strip() or len(direction) > 200:
                raise SelectionError(f"items.{item_id}.text_direction 应为 1–200 字的修改要求")
            item["text_direction"] = direction.strip()
        if not item:
            raise SelectionError(f"items.{item_id} 没有实际修改")
        out_items[item_id] = item
    return {"version": VERSION, "scene": target_scene, **({"series_id": series_id} if series_id else {}),
            "style": style, "palette": palette, "expression": expression, **poster, "items": out_items}


def apply(plan: dict, selection: dict, *, base_path: Path | None = None) -> dict:
    """将选择作用到配图计划；文字缩写要求只标给宿主，不能靠截断原文实现。"""
    from . import plan as PL
    ids = {it["id"] for it in plan.get("items", [])}
    selected = normalize(selection, scene=plan.get("scene"), item_ids=ids)
    series = plan.get("series") or {}
    if selected.get("series_id") and (not isinstance(series, dict) or series.get("id") != selected["series_id"]):
        raise SelectionError("series_id 与计划不一致")
    result = copy.deepcopy(plan)
    style = selected["style"]
    result["style"] = {"code": f"{style['code']}@r{style['revision']}", "source": style["source"],
                       "why": f"{PL.SOURCE_ZH[style['source']]}：沿用系列画风 {style['code']} 当前修订"}
    result["palette"] = {k: v for k, v in selected["palette"].items() if k in {"family", "light", "sat", "custom"}}
    for key in ("poster_layout", "variant"):  # 计划顶层作默认，图片项自己写了的不覆盖
        if key in selected:
            result[key] = selected[key]
    for it in result.get("items", []):
        item_sel = selected["items"].get(it["id"], {})
        expr = item_sel.get("expression", selected["expression"])
        if expr["form"] != "auto":
            it["form"] = expr["form"]
            it["manual"] = expr["source"] == "explicit"
            if expr["form"] == "structure":
                if expr["structure"] != "auto":
                    it["structure"] = expr["structure"]
            else:
                it.pop("structure", None)
        if it.get("form") == "structure" and expr["density"] != "auto":
            it["density"] = expr["density"]
        if "style" in item_sel:
            local = item_sel["style"]
            _palette_compatible(local, selected["palette"])
            it["style"] = f"{local['code']}@r{local['revision']}"
            it["manual"] = True
        if "text_direction" in item_sel:
            it["text_direction"] = item_sel["text_direction"]
    errors, _ = PL.check(result, base_path=base_path)
    if errors:
        raise SelectionError("选择作用后计划不合格：" + "；".join(errors))
    return result


def affected(before: dict, after: dict, plan: dict, *, replace_master: bool = False) -> list[str]:
    """计算要重画的图。单图改母版时保留旧母版给其他图；显式替换系列母版才传播。"""
    ids = {it["id"] for it in plan.get("items", [])}
    left = normalize(before, scene=plan.get("scene"), item_ids=ids)
    right = normalize(after, scene=plan.get("scene"), item_ids=ids)
    if left["scene"] != right["scene"] or left.get("series_id") != right.get("series_id"):
        raise SelectionError("场景或系列变化要建立新任务，不能局部沿用旧图")
    changed = set()
    if ({k: left["style"][k] for k in ("code", "revision")} !=
        {k: right["style"][k] for k in ("code", "revision")} or
        any(left["palette"].get(k) != right["palette"].get(k) for k in ("family", "light", "sat", "custom")) or
        any(left["expression"].get(k) != right["expression"].get(k) for k in ("form", "structure", "density")) or
        any(left.get(k) != right.get(k) for k in ("poster_layout", "variant"))):
        changed.update(ids)
    else:
        for item_id in ids:
            old, new = left["items"].get(item_id, {}), right["items"].get(item_id, {})
            for key in ("style", "expression", "text_direction"):
                a, b = old.get(key), new.get(key)
                if key in {"style", "expression"}:
                    a = {k: v for k, v in (a or {}).items() if k not in {"source", "scope", "locked"}}
                    b = {k: v for k, v in (b or {}).items() if k not in {"source", "scope", "locked"}}
                if a != b:
                    changed.add(item_id)
    series = plan.get("series") or {}
    master_id = series.get("master_id") if isinstance(series, dict) else None
    if replace_master and master_id in changed:
        changed.update(ids)
    return [it["id"] for it in plan.get("items", []) if it["id"] in changed]


def recommend(scene: str, *, shapes: list[str] | None = None, explicit: str | None = None,
              project: Path | None = None) -> dict:
    """场景给候选；内容形状只决定表达建议，不用风格默认构图覆盖它。"""
    from . import plan as PL
    if scene not in D.scenes():
        raise SelectionError(f"未知场景：{scene}")
    shapes = shapes or []
    if any(shape not in PL.SHAPES for shape in shapes):
        raise SelectionError(f"未知内容形状：{[x for x in shapes if x not in PL.SHAPES]}")
    chosen = PL.resolve_style(scene, explicit, project)
    code, alternates, _ = D.scene_choice(scene)
    use = D.use_of_scene(scene)
    pool = D.styles_for_use(use) if use else []
    chosen_code = CT.canonical(chosen["code"])[0] if chosen["code"] else chosen["code"]
    candidates = list(dict.fromkeys([chosen_code, code, *alternates]))
    output = []
    for candidate in candidates:
        try:
            c = CT.load(candidate)
        except (KeyError, CT.ContractError):
            output.append({"code": candidate, "status": "unavailable", "reason": "本环境没有可用合同"})
            continue
        fit = set(c.get("fit", []))
        desired = {"讲故事" if shape == "story" else "讲道理" for shape in shapes}
        missing = sorted(desired - fit)
        out_of_use = bool(use) and candidate not in pool
        output.append({"code": candidate, "revision": c["revision"], "in_use_pool": not out_of_use,
                       "status": c.get("evidence", {}).get("admission", "pending"),
                       "fit": sorted(fit), "content_fit": "suggested" if not missing else "unverified",
                       "reason": (f"这个画风没被标为适合「{use}」；照你的选择继续，但请逐张检查" if out_of_use else
                                  "目录未声明该内容用途：" + "、".join(missing) if missing else "目录列为候选；仍需按成图验收")})
    expression = [{"shape": shape, "forms": PL.SHAPES[shape]["forms"],
                   "structures": PL.SHAPES[shape].get("structures", [])} for shape in shapes]
    return {"scene": scene, "use": use, "pool": pool, "chosen": chosen, "candidates": output, "expression_by_content": expression,
            "note": "内容关系先决定逐图表达；候选未验证不等于不能用。已锁定画风不会因表达建议自动更换。"}


def dumps(selection: dict) -> str:
    return json.dumps(normalize(selection), ensure_ascii=False, indent=2) + "\n"
