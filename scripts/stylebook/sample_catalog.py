"""Optional project catalog from registered samples; never infers comparison groups from filenames."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .contract import sample_state


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def media_info(path, expected=None):
    if not path.is_file():
        return {"availability": "missing", "sha256": None, "width": None, "height": None, "aspect": None}
    checksum = digest(path)
    state = "changed" if expected and expected != checksum else "available"
    width = height = None
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        from PIL import Image
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    return {"availability": state, "sha256": checksum, "width": width, "height": height,
            "aspect": width / height if height else None}


def build(root, relations=None, motion_root=None, styles=None, previous=None):
    root = Path(root).resolve()
    assets, inputs, groups = [], {}, []
    for contract_file in sorted((root / "styles").glob("*/contract.json")):
        if styles and contract_file.parent.name not in styles:
            continue
        contract = json.loads(contract_file.read_text())
        code = contract["code"]
        inputs[str(contract_file)] = digest(contract_file)
        seen = set()
        for sample in contract.get("samples", []):
            relative = Path(sample["file"])
            path = (contract_file.parent / relative).resolve()
            if not path.is_relative_to(contract_file.parent.resolve()):
                raise ValueError("sample path escapes contract directory")
            ident = f"sample:{code}:{relative.as_posix()}"
            if ident in seen:
                raise ValueError("duplicate sample id: " + ident)
            seen.add(ident)
            assets.append({"id": ident, "root": "samples", "path": path.relative_to(root).as_posix(),
                           "kind": "image", "style": code, "style_name": contract.get("name"),
                           "topic": sample.get("topic"), "purpose": sample.get("scene"),
                           "purpose_label": sample.get("topic"), "style_uses": contract.get("uses", []),
                           "palette": sample.get("palette"), "palette_rule": contract.get("palette"),
                           "contract_revision": contract.get("revision"),
                           "generation_revision": (sample.get("generation") or {}).get("revision"),
                           "revision_state": sample_state(contract, sample),
                           "style_admission": (contract.get("evidence") or {}).get("admission", "unknown"),
                           "sample_adoption": sample.get("adoption", "unknown"),
                           "source": {"contract": contract_file.relative_to(root).as_posix(),
                                      "sample_file": sample["file"], "generation": sample.get("generation"),
                                      "registered_source_sha256": sample.get("source_sha256")},
                           "rights": {"sample_use": sample.get("license", "unknown"),
                                      "recipe_license": (contract.get("source") or {}).get("license", "unknown")},
                           **media_info(path)})
    roots = {"samples": str(root)}
    motion_file = root / "gallery/motion-data.json"
    if motion_file.is_file():
        from . import motion_gallery
        document = motion_gallery.parse_text(motion_file.read_text())
        problems = motion_gallery.validate_document(document)
        if problems:
            raise ValueError("invalid motion registry: " + "; ".join(problems))
        inputs[str(motion_file)] = digest(motion_file)
        if motion_root is not None:
            roots["motion"] = str(Path(motion_root).resolve())
        for item in document["items"]:
            members = []
            for field, filename, sha_key in motion_gallery.item_media(item):
                ident = f"motion:{item['id']}:{field}"
                relative = f"{item['id']}/{filename}"
                info = media_info(Path(roots["motion"]) / relative, item[sha_key]) if "motion" in roots else {
                    "availability": "unconfigured", "sha256": None, "width": None, "height": None, "aspect": None}
                if field == "video":
                    info.update(width=item["width"], height=item["height"], aspect=item["width"] / item["height"],
                                dimensions_basis="registered_manifest")
                assets.append({"id": ident, "root": "motion", "path": relative,
                               "kind": "video" if field == "video" else "image", "style": item.get("style_code"),
                               "topic": item["title"], "purpose": item["scene"], "palette": None,
                               "source": {"manifest": "gallery/motion-data.json", "item": item["id"], "field": field,
                                          "registered_sha256": item[sha_key]},
                               "style_admission": "not_applicable", "sample_adoption": "unknown",
                               "rights": {"sample_use": "unknown"}, **info})
                if field in {"source", "video"}:
                    members.append(ident)
            if len(members) == 2:
                groups.append({"id": f"motion-pair:{item['id']}", "type": "still_motion", "members": members,
                               "basis": "explicit_motion_manifest", "source": str(motion_file),
                               "source_sha256": inputs[str(motion_file)]})
    by_id = {asset["id"]: asset for asset in assets}
    if len(by_id) != len(assets):
        raise ValueError("duplicate asset id")
    if relations is not None:
        relations = Path(relations).resolve()
        record = json.loads(relations.read_text())
        if record.get("kind") != "sample-relations/1" or not isinstance(record.get("groups"), list):
            raise ValueError("expected sample-relations/1")
        inputs[str(relations)] = digest(relations)
        group_ids = {group["id"] for group in groups}
        for group in record["groups"]:
            ident, members = group.get("id"), group.get("members")
            if not isinstance(ident, str) or not ident or ident in group_ids:
                raise ValueError("invalid or duplicate group id")
            group_ids.add(ident)
            if group.get("type") not in {"same_content_styles", "same_composition_palettes", "series", "still_motion"}:
                raise ValueError("unknown relationship type")
            if not isinstance(members, list) or len(members) < 2 or len(set(members)) != len(members):
                raise ValueError("group needs distinct members")
            if any(member not in by_id for member in members):
                raise ValueError("unknown relationship member")
            if not isinstance(group.get("condition"), str) or not group["condition"].strip():
                raise ValueError("group requires actual shared condition")
            basis = group.get("evidence", {})
            evidence = (relations.parent / basis.get("file", "")).resolve()
            if not evidence.is_file() or digest(evidence) != basis.get("sha256"):
                raise ValueError("relationship needs fingerprinted source evidence")
            if any(not by_id[m]["sha256"] or group.get("member_sha256", {}).get(m) != by_id[m]["sha256"] for m in members):
                raise ValueError("relationship member missing or changed")
            inputs[str(evidence)] = digest(evidence)
            groups.append({**group, "evidence": {**basis, "file": str(evidence)},
                           "basis": "source_backed_assertion_requires_semantic_review"})
    result = {"kind": "sample-catalog/1", "roots": roots, "assets": assets, "groups": groups, "inputs": inputs}
    if previous is not None:
        old = json.loads(Path(previous).read_text())
        if old.get("kind") != "sample-catalog/1" or any(old.get("filters", {}).values()):
            raise ValueError("--previous requires an unfiltered sample-catalog/1")
        old_assets = {asset["id"]: asset for asset in old["assets"]}
        result["changes"] = {"added": sorted(set(by_id) - set(old_assets)),
                             "removed": sorted(set(old_assets) - set(by_id)),
                             "changed": sorted(key for key in set(by_id) & set(old_assets) if by_id[key] != old_assets[key])}
    return result


def select(catalog, purpose=None, aspect=None, group=None):
    if aspect is not None and (type(aspect) not in {int, float} or not math.isfinite(aspect) or aspect <= 0):
        raise ValueError("aspect must be finite and positive")
    allowed = None
    if group is not None:
        matches = [g for g in catalog["groups"] if g["id"] == group]
        if len(matches) != 1:
            raise ValueError("unknown group")
        allowed = set(matches[0]["members"])
    def fits(asset):
        if allowed is not None and asset["id"] not in allowed:
            return False
        if purpose and purpose not in {asset.get("purpose"), asset.get("purpose_label")} and purpose not in (asset.get("topic") or ""):
            return False
        ratio = asset.get("aspect")
        return not aspect or ratio is not None and abs(ratio - aspect) / aspect < .04
    return [asset for asset in catalog["assets"] if fits(asset)]
