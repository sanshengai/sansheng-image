"""Read-only host evidence import; no generation, model calls or style registration."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from . import compile as CP
from . import contract as CT
from . import matrix as MX


class EvidenceError(ValueError):
    pass


def _need(ok, message):
    if not ok:
        raise EvidenceError(message)


def _load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise EvidenceError(f"证据缺失、为空或不是JSON：{path}") from exc
    _need(isinstance(data, dict), f"证据必须是对象：{path}")
    return data


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _bound(path, digest):
    p = Path(path)
    _need(p.is_file() and p.stat().st_size > 0, f"证据缺失或为空：{p}")
    _need(isinstance(digest, str) and _sha(p) == digest, f"证据哈希不符：{p}")
    return p


def _image(path, digest):
    p = _bound(path, digest)
    try:
        with Image.open(p) as im:
            im.verify()
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"不是有效图片：{p}") from exc
    return p


def _native_input(record):
    p = _image(record["path"], record["sha256"])
    with Image.open(p) as im:
        _need(list(im.size) == record.get("native_size"), "独立看图不是原尺寸图片")
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=90)
    _need(hashlib.sha256(buf.getvalue()).hexdigest() == record.get("jpeg_sha256"),
          "独立看图实际编码摘要不是原尺寸JPEG90")
    return p


def _core(contract):
    return {k: v for k, v in contract.items() if not k.startswith("_")}


def _response_json(response):
    _need(response.get("model", "").replace("-", "").replace(".", "").startswith("deepseekv41flash"),
          "独立意见返回型号不是deepseek-v4.1-flash")
    choice = (response.get("choices") or [{}])[0]
    _need(choice.get("finish_reason") == "stop", "独立意见未完整结束")
    text = (choice.get("message") or {}).get("content")
    _need(isinstance(text, str) and bool(text.strip()), "独立意见为空")
    if text.strip().startswith("```"):
        text = text.strip().split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise EvidenceError("独立意见不是完整JSON") from exc
    _need(isinstance(data, dict), "独立意见必须是对象")
    return data


def _answer(response):
    data = _response_json(response)
    for key in ("content", "text", "style", "reference_leak", "overall"):
        _need(data.get(key) in ("pass", "fail", "uncertain"), f"独立意见缺项：{key}")
    _need(bool(data.get("evidence")) and isinstance(data["evidence"], list), "独立意见缺具体证据")
    _need(isinstance(data.get("uncertain"), list), "独立意见缺疑点记录")
    # Preserve disputed and internally inconsistent opinions for Root arbitration.
    return data


def _unit(unit, current, by_question):
    image = _image(unit["original"], unit["original_sha256"])
    contract_path = _bound(unit["contract"], unit["contract_sha256"])
    contract = _load(contract_path)
    _need(_core(contract) == _core(current), "矩阵合同与当前合同不同")
    qpath = _bound(unit["question"], unit["question_sha256"])
    q = _load(qpath)
    _need(q.get("id") in by_question and q == by_question[q["id"]], "矩阵题面不是当前标准题")
    d = contract_path.parent
    manifest = _load(d / "manifest.json")
    _need(manifest.get("style", "").split("@")[0] == current["code"], "生成清单画风不符")
    _need(manifest.get("content", {}).get("subject") == q["subject"], "生成清单主体不是当前题面")
    _need(manifest.get("text") == q["text"] and manifest.get("aspect") == q["aspect"], "题面文字或比例不符")
    if q.get("inventory"):
        inventory = manifest.get("content", {}).get("inventory", "")
        _need(inventory.startswith(q["inventory"]), "生成清单遗漏当前题面数量要求")
    compiled = CP.compile_manifest(manifest, contract=contract)
    actual = _load(d / "generation-metadata.json")
    args = actual.get("actual_arguments")
    _need(args == _load(d / "tool-arguments-prepared.json"), "实际参数与prepare不符")
    expected_args = {"prompt": compiled.prompt, "transparent_background": False}
    if compiled.references:
        expected_args["referenced_image_paths"] = [r["path"] for r in compiled.references]
    _need(args == expected_args, "完整实际工具参数与当前编译结果不同")
    _image(actual["source_path"], unit["original_sha256"])
    request_path = _bound(unit["request"], unit["request_sha256"])
    request = _load(request_path)
    _need(request.get("route") == "coding_plan" and request.get("requested_model") == "deepseek-v4.1-flash",
          "独立意见未使用指定Coding Plan路线")
    _need(request.get("question_sha256") == unit["question_sha256"]
          and request.get("contract_sha256") == unit["contract_sha256"], "独立意见审了不同题面或合同")
    inputs = request.get("inputs") or []
    _need(len(inputs) == 1 + len(compiled.references), "独立看图缺原图或参考图")
    _need(Path(inputs[0]["path"]).resolve() == image.resolve()
          and inputs[0]["sha256"] == unit["original_sha256"], "独立意见绑定了不同原图")
    _native_input(inputs[0])
    for record, ref in zip(inputs[1:], compiled.references):
        _native_input(record)
        _need(record["role"] == ref["role"] and Path(record["path"]).resolve() == Path(ref["path"]).resolve(),
              "独立意见参考职责或路径不符")
    if q.get("refs_from"):
        refs = compiled.references
        _need(any(r["role"] == "identity" for r in refs), "T2没有实际身份参考")
    response_path = _bound(unit["independent_response"], unit["response_sha256"])
    opinion = _answer(_load(response_path))
    root_path = _bound(unit["root"], unit["root_sha256"])
    root = _load(root_path)
    bound_contract = _bound(unit["root_bound_contract"], unit["root_bound_contract_sha256"])
    merged = MX.merged_contract(contract, q)
    _need(json.dumps(q, ensure_ascii=False) in request.get("prompt", "")
          and json.dumps(merged["qa"], ensure_ascii=False) in request.get("prompt", ""),
          "独立请求没有完整当前题面和QA")
    root_contract = _load(bound_contract)
    _need(root_contract == contract or root_contract == merged, "Root合同不是本题原合同或合并合同")
    _need(root.get("original_sha256") == unit["original_sha256"]
          and root.get("contract_sha256") == unit["root_bound_contract_sha256"], "Root绑定图或合同不符")
    _need(root.get("qa_checked") == merged["qa"], "Root未完整核对当前QA")
    _need(root.get("result") in ("pass", "partial", "fail") and bool(root.get("observation")), "Root裁决或原图观察缺失")
    _need(root.get("deepseek_original_overall") == opinion["overall"], "Root未保留实际独立意见")
    _need(root.get("root_adopted_candidate") is (root["result"] == "pass"), "Root采用状态与结论矛盾")
    result = "pass" if root["result"] == "pass" else "fail"
    return q["id"], {"image": str(image), "result": result, "root_result": root["result"],
                     "reviews": [{"source": "ark_coding_plan", "review": opinion, "proof": str(response_path)},
                                 {"source": "maintainer_codex_native", "review": root, "proof": str(root_path)}],
                     "problems": root.get("limits", []), "verified_pair": True,
                     "actual_arguments": args, "evidence_unit": unit}


def verify(bundle_path, contract_path):
    """Re-read every actual input; bundle counts and verdicts are never authoritative."""
    bundle = _load(bundle_path)
    contract = _load(contract_path)
    _need(not CT.validate(contract), "当前合同不合格")
    _need(bundle.get("style") == contract.get("code"), "证据包画风不符")
    units = bundle.get("matrix")
    _need(isinstance(units, list) and len(units) == 8, "必须有八题完整证据")
    by = {q["id"]: q for q in MX.questions()}
    records = {}
    for unit in units:
        qid, record = _unit(unit, contract, by)
        _need(qid not in records, "矩阵题重复")
        records[qid] = record
    _need(set(records) == set(by), "矩阵题缺失")
    t1 = Path(records["T1"]["image"]).resolve()
    # Locate T2 by its question, rather than trusting bundle order.
    t2_unit = records["T2"]["evidence_unit"]
    t2_compiled = CP.compile_manifest(_load(Path(t2_unit["contract"]).parent / "manifest.json"), contract=_load(t2_unit["contract"]))
    identity = [r for r in t2_compiled.references if r["role"] == "identity"]
    _need(len(identity) == 1 and Path(identity[0]["path"]).resolve() == t1, "T2身份图不是本轮T1")
    stable = _bound(bundle["stability"]["receipt"], bundle["stability"]["sha256"])
    stability = _load(stable)
    stable_question = stability.get("question", "T3")
    _need(isinstance(stable_question, str) and stable_question in by,
          "稳定测试题不是当前标准题")
    stable_record = records[stable_question]
    cohort = stability.get("trials", stability.get("cohort"))
    _need(stability.get("result") == "pass" and isinstance(cohort, list) and len(cohort) == 3, "三次稳定证据不完整")
    hashes = []
    for trial in cohort:
        image = _image(trial["image"], trial["sha256"])
        hashes.append(trial["sha256"])
        actual = _load(image.parent / "generation-metadata.json")
        _need(actual.get("actual_arguments") == stable_record["actual_arguments"], "稳定试次不是完整同词同参")
        _image(actual["source_path"], trial["sha256"])
        root = _load(trial["root_ruling"])
        _need(root.get("result") == "pass" and root.get("original_sha256", root.get("sha256")) == trial["sha256"], "稳定裁决没有绑定本张通过图")
        _need(bool(root.get("observation", root.get("evidence"))), "稳定裁决缺原图观察")
        merged = MX.merged_contract(contract, by[stable_question])
        if root.get("qa_sha256"):
            bound = _bound(image.parent / "merged-contract.json", root["qa_sha256"])
            _need(_load(bound) == merged, "稳定裁决的合并合同不是当前测试题")
        else:
            _need(root.get("qa_checked") == merged["qa"], "稳定裁决未核对当前测试题的QA")
    _need(len(set(hashes)) == 3 and _sha(stable_record["image"]) in hashes, "稳定试次重复或缺本轮测试题原图")
    neighbors = _bound(bundle["neighbors"]["receipt"], bundle["neighbors"]["sha256"])
    neighbor = _load(neighbors)
    _need(neighbor.get("style") == contract["code"] and neighbor.get("result") == "pass"
          and bool(neighbor.get("observations")) and len(neighbor.get("inputs", [])) >= 3, "近邻证据不完整")
    for inp in neighbor["inputs"]:
        _image(inp["path"], inp["sha256"])
    _need(any(Path(i["path"]).resolve() == t1 for i in neighbor["inputs"]), "近邻没有当前T1")
    _need(bool(neighbor.get("coding_plan_blind_records")), "近邻缺独立盲审")
    for rec in neighbor["coding_plan_blind_records"]:
        request = _load(_bound(rec["request"], rec["request_sha256"]))
        _need(request.get("requested_model") == "deepseek-v4.1-flash", "近邻盲审请求型号不符")
        _need(len(request.get("inputs", [])) == 3, "近邻盲审缺实际比较图")
        _need(json.dumps(by["T1"], ensure_ascii=False) in request.get("prompt", ""), "近邻盲审未核对当前T1题面")
        _need(Path(request["inputs"][0]["path"]).resolve() == t1, "近邻盲审首图不是当前T1")
        known = {(str(Path(i["path"]).resolve()), i["sha256"]) for i in neighbor["inputs"]}
        for inp in request.get("inputs", []):
            _native_input(inp)
            _need((str(Path(inp["path"]).resolve()), inp["sha256"]) in known, "近邻盲审图片不在原生裁决图组")
        response = _load(_bound(rec["actual_response"], rec["response_sha256"]))
        answer = _response_json(response)
        original = _load(_bound(rec["original_review"], rec["original_review_sha256"]))
        _need(answer == original, "近邻盲审解析意见与实际返回不同")
        pairs = answer.get("pairs", [])
        _need(answer.get("overall") in ("pass", "fail", "uncertain") and len(pairs) == 2,
              "近邻盲审缺完整两组比较意见")
        _need({p.get("other_image") for p in pairs} == {2, 3}, "近邻盲审比较图编号不完整")
        for pair in pairs:
            _need(pair.get("distinguishable") in ("pass", "fail", "uncertain")
                  and isinstance(pair.get("evidence"), list) and bool(pair["evidence"])
                  and isinstance(pair.get("uncertain"), list), "近邻盲审缺具体证据或疑点")
    passed = sum(rec["result"] == "pass" for rec in records.values())
    native = "native" in contract.get("recipe", {}).get("text_mode", [])
    line = passed >= 7 and records["T3"]["result"] == "pass" and (not native or records["T8"]["result"] == "pass")
    return {"style": contract["code"], "workflow": "verified_host_evidence", "questions": records,
            "provider": "codex_builtin", "model": None, "est_usd_total": None, "spent_this_run": None,
            "new_model_calls": 0, "new_generations": 0, "abilities": MX.abilities({"questions": records}),
            "contract_sha256": _sha(contract_path), "bundle_sha256": _sha(bundle_path),
            "admission": {"verdict": "pending", "matrix_verdict": "pass" if line else "fail",
                          "evidence_verdict": "pass" if line else "fail", "paired_reviews_verified": 8,
                          "stability_verified": True, "neighbors_verified": True,
                          "reasons": ["当前实际证据已核对；保留失败题和独立意见分歧；正式登记与场景能力边界另行完成"],
                          "todo": ["正式合同/能力与场景登记、派生及样图一致性", "发布候选与批准"]},
            "stability_question": stable_question,
            "neighbor_limitations": neighbor.get("limits", []), "formal_admission": False}


def import_evidence(bundle_path, contract_path, out):
    state = verify(bundle_path, contract_path)
    out = Path(out)
    _need(not out.exists(), "输出目录已存在，拒绝覆盖旧验收")
    out.mkdir(parents=True)
    (out / "matrix-evidence-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MX.render(out, state, MX.questions())
    return state
