"""风格合同：加载与校验（仅标准库；装了 jsonschema 时额外交叉校验）。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STYLES_DIR = ROOT / "styles"
SCHEMA_PATH = STYLES_DIR / "_schema" / "contract.schema.json"


class ContractError(ValueError):
    """合同不合格：列出全部问题，而不是只报第一个。"""

    def __init__(self, code: str, problems: list[str]):
        self.code, self.problems = code, problems
        super().__init__(f"{code}: " + "；".join(problems))


def profile_dir() -> Path | None:
    """显式目录优先；显式目录不存在时禁用私有层，不回落到本机默认目录。"""
    env = os.environ.get("SANSHENG_IMAGE_PROFILE")
    if env is not None:
        candidate = Path(env).expanduser()
        return candidate if candidate.is_dir() else None
    candidate = Path.home() / ".config" / "sansheng-image" / "profile"
    return candidate if candidate.is_dir() else None


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}


def _check(value: Any, schema: dict, root: dict, path: str, out: list[str]) -> None:
    if "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith("#/$defs/"), ref
        schema = root["$defs"][ref.split("/")[-1]]
    t = schema.get("type")
    if t:
        py = _TYPES[t]
        ok = isinstance(value, py) and not (t in ("integer", "number") and isinstance(value, bool))
        if not ok:
            out.append(f"{path} 应为 {t}")
            return
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path} 取值 {value!r} 不在 {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path} 太短")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path} 格式不对：{value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        out.append(f"{path} 小于 {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path} 至少 {schema['minItems']} 项")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path} 至多 {schema['maxItems']} 项")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            out.append(f"{path} 有重复项")
        if "items" in schema:
            for i, v in enumerate(value):
                _check(v, schema["items"], root, f"{path}[{i}]", out)
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}.{k} 缺失")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                _check(v, props[k], root, f"{path}.{k}", out)
            elif extra is False:
                out.append(f"{path}.{k} 不是合同字段")
            elif isinstance(extra, dict):
                _check(v, extra, root, f"{path}.{k}", out)


def validate(contract: dict, *, where: Path | None = None) -> list[str]:
    """返回问题清单；空清单即合格。"""
    contract = {k: v for k, v in contract.items() if not k.startswith("_")}  # 加载时附带的运行时注记（如 _path）不算合同字段
    root = _schema()
    problems: list[str] = []
    _check(contract, root, root, "合同", problems)
    code = contract.get("code", "?")
    vis = contract.get("visibility")
    if isinstance(code, str) and vis in ("public", "private"):
        if vis == "public" and not code.startswith("C"):
            problems.append("公开风格的风格码必须以 C 开头")
        if vis == "private" and not code.startswith("S"):
            problems.append("私有风格的风格码必须以 S 开头")
    if where is not None and where.parent.name != code:
        problems.append(f"目录名 {where.parent.name} 与风格码 {code} 不一致")
    anchor = contract.get("anchor") or {}
    if where is not None and isinstance(anchor, dict) and isinstance(anchor.get("file"), str):
        anchor_path = Path(anchor["file"])
        if not anchor_path.is_absolute():
            anchor_path = where.parent / anchor_path
        if not anchor_path.is_file():
            problems.append(f"风格锚点不存在：{anchor_path}")
        elif isinstance(anchor.get("sha256"), str) and hashlib.sha256(anchor_path.read_bytes()).hexdigest() != anchor["sha256"]:
            problems.append(f"风格锚点与合同 sha256 不符：{anchor_path}")
    qa = contract.get("qa") or {}
    overlay_layout = qa.get("overlay_layout")
    if isinstance(overlay_layout, dict):
        if not overlay_layout:
            problems.append("qa.overlay_layout 至少需要一条约束")
        ratio = overlay_layout.get("headline_min_font_px_ratio")
        if ratio is not None and (type(ratio) not in (int, float) or not 0 < ratio <= 0.5):
            problems.append("qa.overlay_layout.headline_min_font_px_ratio 须在 (0, 0.5] 内")
        lines = overlay_layout.get("subtitle_max_lines")
        if lines is not None and (type(lines) is not int or lines < 1):
            problems.append("qa.overlay_layout.subtitle_max_lines 须为正整数")
    listed = set(qa.get("must_see", [])) | set(qa.get("must_not_see", []))
    for item in qa.get("when_text", []):
        if item not in listed:
            problems.append(f"when_text 里的条目不在必须 / 禁止清单中：{item[:40]}")
    recipe = contract.get("recipe") or {}
    modes = recipe.get("text_mode", []) if isinstance(recipe, dict) else []
    if isinstance(overlay_layout, dict) and overlay_layout and not set(modes).intersection({"overlay", "hybrid"}):
        problems.append("qa.overlay_layout 需要 recipe.text_mode 包含 overlay 或 hybrid")
    if isinstance(recipe, dict) and recipe.get("text_style_hybrid") and "hybrid" not in modes:
        problems.append("recipe.text_style_hybrid 需要 recipe.text_mode 包含 hybrid")
    if isinstance(recipe, dict):
        req, opt = recipe.get("required_text_roles", []), recipe.get("optional_text_roles", [])
        styles = recipe.get("role_styles", {})
        if isinstance(req, list) and isinstance(opt, list):
            both = sorted(set(req) & set(opt))
            if both:
                problems.append(f"recipe 文字角色 {both} 不能同时是必需和可选")
            if req and not set(modes).intersection({"native", "overlay", "hybrid"}):
                problems.append("recipe.required_text_roles 需要 recipe.text_mode 包含 native、overlay 或 hybrid")
            if isinstance(styles, dict):
                unknown = sorted(k for k in styles if k not in set(req) | set(opt))
                if unknown:
                    problems.append(f"recipe.role_styles 的角色 {unknown} 没有在 required_text_roles / optional_text_roles 里声明")
        layout = recipe.get("layout")
        if isinstance(layout, dict) and layout.get("text_zone") and layout.get("text_zone") == layout.get("hero_zone"):
            problems.append("recipe.layout 的 text_zone 与 hero_zone 不能在同一侧")
    structure_prompts = recipe.get("structure_prompts", {}) if isinstance(recipe, dict) else {}
    if isinstance(structure_prompts, dict):
        structure_data = json.loads((ROOT / "structures" / "structures.json").read_text(encoding="utf-8"))
        known_structures = {entry["id"] for entry in structure_data["structures"]}
        for structure in structure_prompts:
            if structure not in known_structures:
                problems.append(f"recipe.structure_prompts 未知表达结构 {structure}")
    overrides = qa.get("mode_overrides", {})
    if isinstance(overrides, dict):
        for mode, override in overrides.items():
            if mode not in modes:
                problems.append(f"mode_overrides.{mode} 不在 recipe.text_mode 中")
            if not isinstance(override, dict):
                continue  # 类型错误由 Schema 报告
            lists = [override.get("must_see", []), override.get("must_not_see", [])]
            override_listed = {item for group in lists if isinstance(group, list)
                               for item in group if isinstance(item, str)}
            when_text = override.get("when_text", [])
            for item in when_text if isinstance(when_text, list) else []:
                if isinstance(item, str) and item not in override_listed:
                    problems.append(f"mode_overrides.{mode}.when_text 条目不在该模式的必须 / 禁止清单中：{item[:40]}")
    log = contract.get("changelog") or []
    if log and max(e.get("revision", 0) for e in log) != contract.get("revision"):
        problems.append("changelog 最高修订号与 revision 不一致")
    try:  # 交叉校验：两套实现结论不同就说明本地实现有漏洞
        import jsonschema  # type: ignore
        errs = sorted(jsonschema.Draft202012Validator(root).iter_errors(contract), key=str)
        if errs and not problems:
            problems.append("jsonschema 发现本地校验器漏掉的问题：" + errs[0].message)
    except ImportError:
        pass
    return problems


def contract_paths() -> list[Path]:
    paths = sorted(STYLES_DIR.glob("*/contract.json"))
    prof = profile_dir()
    if prof:
        paths += sorted((prof / "styles").glob("*/contract.json"))
    return [p for p in paths if not p.parent.name.startswith("_")]


def _catalog_meta() -> dict:
    try:
        return json.loads((STYLES_DIR / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def canonical(code: str) -> tuple[str, str | None]:
    """旧码 → 现行码。返回（现行码, 说明）；合并进别的画风的码自动转换，已删除的码抛错并给替代建议。"""
    base = code.split("@")[0]
    meta = _catalog_meta()
    aliases = meta.get("aliases", {})
    if base in aliases:
        return aliases[base], f"{base} 已并入 {aliases[base]}"
    retired = meta.get("retired", {})
    if base in retired:
        suggest = retired[base].get("suggest")
        raise KeyError(f"{base} 已从画风库删除" + (f"，建议改用 {suggest}" if suggest else "，没有直接替代，请重新选择用途下的画风"))
    return base, None


def load(code: str) -> dict:
    raw = code
    code, note = canonical(code)
    if note:
        c = load(code)
        c["_alias_from"] = raw.split("@")[0]
        c["_alias_note"] = note
        return c
    for p in contract_paths():
        if p.parent.name == code:
            c = json.loads(p.read_text(encoding="utf-8"))
            problems = validate(c, where=p)
            if problems:
                raise ContractError(code, problems)
            c["_path"] = str(p)
            return c
    raise KeyError(f"找不到风格 {code}（公开库与私有 profile 都没有）")


def load_all(strict: bool = True) -> list[dict]:
    out = []
    for p in contract_paths():
        c = json.loads(p.read_text(encoding="utf-8"))
        problems = validate(c, where=p)
        if problems and strict:
            raise ContractError(c.get("code", p.parent.name), problems)
        c["_path"] = str(p)
        out.append(c)
    return out
