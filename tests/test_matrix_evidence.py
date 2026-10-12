"""Exercise the actual offline import path, including tampered evidence chains."""
import copy
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stylebook import compile as CP, contract as CT, matrix as MX
from stylebook.matrix_evidence import EvidenceError, import_evidence, verify


def put(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return p


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text())


def input_record(p, role="待审图"):
    with Image.open(p) as im:
        size = list(im.size)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=90)
    return {"path": str(p), "role": role, "sha256": sha(p), "native_size": size,
            "jpeg_sha256": hashlib.sha256(buf.getvalue()).hexdigest()}


@pytest.fixture
def case(tmp_path):
    """All opinions are explicit test doubles; no actual model or generation calls."""
    contract = CT.load("C06")
    cp = put(tmp_path / "current-contract.json", contract)
    units = []
    for i, q in enumerate(MX.questions()):
        d = tmp_path / q["id"]
        d.mkdir()
        image = d / "original.png"
        Image.new("RGB", (48, 48), (120 + i, 170, 210)).save(image)
        kp = put(d / "contract.json", contract)
        qp = put(d / "question.json", q)
        refs = [(Path(units[0]["original"]), "identity")] if q["id"] == "T2" else None
        manifest = MX.manifest_for("C06", q, "gpt-image-2", refs)
        put(d / "manifest.json", manifest)
        compiled = CP.compile_manifest(manifest, contract=contract)
        args = {"prompt": compiled.prompt, "transparent_background": False}
        if compiled.references:
            args["referenced_image_paths"] = [r["path"] for r in compiled.references]
        put(d / "tool-arguments-prepared.json", args)
        put(d / "generation-metadata.json", {"actual_arguments": args, "source_path": str(image)})
        merged = MX.merged_contract(contract, q)
        request = {"route": "coding_plan", "requested_model": "deepseek-v4.1-flash",
                   "question_sha256": sha(qp), "contract_sha256": sha(kp),
                   "inputs": [input_record(image)] + [input_record(Path(r["path"]), r["role"]) for r in compiled.references],
                   "prompt": json.dumps(q, ensure_ascii=False) + json.dumps(merged["qa"], ensure_ascii=False)}
        rp = put(d / "request.json", request)
        opinion = {k: "pass" for k in ("content", "text", "style", "reference_leak", "overall")}
        opinion.update(evidence=["test double only"], uncertain=[])
        response = {"model": "deepseek-v4-1-flash", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(opinion)}}]}
        ap = put(d / "response.json", response)
        root = {"original_sha256": sha(image), "contract_sha256": sha(kp), "result": "pass",
                "root_adopted_candidate": True, "qa_checked": merged["qa"],
                "observation": ["test double only"], "deepseek_original_overall": "pass"}
        rootp = put(d / "root.json", root)
        units.append({"original": str(image), "original_sha256": sha(image), "contract": str(kp),
                      "contract_sha256": sha(kp), "question": str(qp), "question_sha256": sha(qp),
                      "request": str(rp), "request_sha256": sha(rp), "independent_response": str(ap),
                      "response_sha256": sha(ap), "root": str(rootp), "root_sha256": sha(rootp),
                      "root_bound_contract": str(kp), "root_bound_contract_sha256": sha(kp)})
    t3 = units[2]
    cohort = [{"image": t3["original"], "sha256": t3["original_sha256"], "root_ruling": t3["root"]}]
    for i in [2, 3]:
        d = tmp_path / f"trial{i}"
        d.mkdir()
        im = d / "original.png"
        Image.new("RGB", (48, 48), (200, 160 + i, 190)).save(im)
        put(d / "generation-metadata.json", {"source_path": str(im), "actual_arguments": read(Path(t3["contract"]).parent / "generation-metadata.json")["actual_arguments"]})
        root = read(t3["root"])
        root["original_sha256"] = sha(im)
        rootp = put(d / "root.json", root)
        cohort.append({"image": str(im), "sha256": sha(im), "root_ruling": str(rootp)})
    sp = put(tmp_path / "stable.json", {"result": "pass", "cohort": cohort})
    ni = [input_record(Path(units[i]["original"])) for i in [0, 3, 4]]
    nrp = put(tmp_path / "neighbor-request.json", {"requested_model": "deepseek-v4.1-flash", "inputs": ni,
              "prompt": json.dumps(MX.questions()[0], ensure_ascii=False)})
    opinion = {"overall": "pass", "pairs": [{"other_image": i, "distinguishable": "pass",
               "evidence": ["fixture only"], "uncertain": []} for i in (2, 3)]}
    nap = put(tmp_path / "neighbor-response.json", {"model": "deepseek-v4-1-flash", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(opinion)}}]})
    nvp = put(tmp_path / "neighbor-review.json", opinion)
    np = put(tmp_path / "neighbors.json", {"style": "C06", "result": "pass", "observations": {"test": "fixture only"}, "inputs": ni,
             "coding_plan_blind_records": [{"request": str(nrp), "request_sha256": sha(nrp), "actual_response": str(nap),
                                           "response_sha256": sha(nap), "original_review": str(nvp), "original_review_sha256": sha(nvp)}]})
    bundle = {"style": "C06", "matrix": units, "stability": {"receipt": str(sp), "sha256": sha(sp)}, "neighbors": {"receipt": str(np), "sha256": sha(np)}}
    bp = put(tmp_path / "bundle.json", bundle)
    return bp, cp, bundle


def test_import_uses_real_consumer_without_registering(case, tmp_path):
    bp, cp, _ = case
    st = import_evidence(bp, cp, tmp_path / "imported")
    assert st["admission"]["evidence_verdict"] == "pass"
    assert st["admission"]["paired_reviews_verified"] == 8
    assert st["admission"]["verdict"] == "pending" and st["formal_admission"] is False
    assert st["new_model_calls"] == st["new_generations"] == 0
    assert (tmp_path / "imported/matrix-evidence-state.json").is_file()
    assert not (tmp_path / "imported/state.json").exists()
    assert (tmp_path / "imported/matrix.png").is_file()


@pytest.mark.parametrize("mutation", ["wrong_image", "empty_image", "missing_response", "root_tag_only",
                                    "old_question", "wrong_contract", "missing_QA", "changed_arguments",
                                    "thumbnail_only", "wrong_model", "truncated", "duplicate_trial",
                                    "changed_trial_prompt", "missing_neighbor", "duplicate_question",
                                    "blind_wrong_question", "blind_changed_review", "blind_empty_pairs"])
def test_tampering_rejected_on_consumer_path(case, tmp_path, mutation):
    bp, cp, bundle = case
    u = bundle["matrix"][0]
    if mutation == "wrong_image":
        u["original"] = bundle["matrix"][3]["original"]
    elif mutation == "empty_image":
        Path(u["original"]).write_bytes(b"")
    elif mutation == "missing_response":
        Path(u["independent_response"]).unlink()
    elif mutation == "root_tag_only":
        p = Path(u["root"]); put(p, {"result": "pass", "source": "maintainer_codex_native"}); u["root_sha256"] = sha(p)
    elif mutation == "old_question":
        p = Path(u["question"]); q = read(p); q["subject"] = "old question"; put(p, q); u["question_sha256"] = sha(p)
    elif mutation == "wrong_contract":
        p = Path(u["contract"]); k = read(p); k["recipe"]["positive"] += " changed style"; put(p, k); u["contract_sha256"] = sha(p)
    elif mutation == "missing_QA":
        p = Path(u["root"]); k = read(p); k["qa_checked"]["must_see"].pop(); put(p, k); u["root_sha256"] = sha(p)
    elif mutation == "changed_arguments":
        d = Path(u["contract"]).parent
        k = read(d / "generation-metadata.json"); k["actual_arguments"]["prompt"] = "different actual input"
        put(d / "generation-metadata.json", k); put(d / "tool-arguments-prepared.json", k["actual_arguments"])
    elif mutation == "thumbnail_only":
        p = Path(u["request"]); k = read(p); k["inputs"][0]["native_size"] = [8, 8]; put(p, k); u["request_sha256"] = sha(p)
    elif mutation in ("wrong_model", "truncated"):
        p = Path(u["independent_response"]); k = read(p)
        if mutation == "wrong_model": k["model"] = "claude-sonnet-5"
        else: k["choices"][0]["finish_reason"] = "length"
        put(p, k); u["response_sha256"] = sha(p)
    elif mutation in ("duplicate_trial", "changed_trial_prompt"):
        p = Path(bundle["stability"]["receipt"]); k = read(p)
        if mutation == "duplicate_trial": k["cohort"][2] = copy.deepcopy(k["cohort"][0])
        else:
            m = Path(k["cohort"][2]["image"]).parent / "generation-metadata.json"
            v = read(m); v["actual_arguments"]["prompt"] += " altered"; put(m, v)
        put(p, k); bundle["stability"]["sha256"] = sha(p)
    elif mutation == "missing_neighbor":
        Path(bundle["neighbors"]["receipt"]).write_text("")
    elif mutation == "duplicate_question":
        bundle["matrix"][7] = copy.deepcopy(bundle["matrix"][0])
    elif mutation.startswith("blind_"):
        np = Path(bundle["neighbors"]["receipt"]); neighbor = read(np)
        rec = neighbor["coding_plan_blind_records"][0]
        if mutation == "blind_wrong_question":
            p = Path(rec["request"]); k = read(p); k["prompt"] = "old question"
            put(p, k); rec["request_sha256"] = sha(p)
        elif mutation == "blind_changed_review":
            p = Path(rec["original_review"]); k = read(p); k["overall"] = "fail"
            put(p, k); rec["original_review_sha256"] = sha(p)
        else:
            p = Path(rec["actual_response"]); k = read(p)
            opinion = {"overall": "pass", "pairs": []}
            k["choices"][0]["message"]["content"] = json.dumps(opinion)
            put(p, k); rec["response_sha256"] = sha(p)
            p = Path(rec["original_review"]); put(p, opinion); rec["original_review_sha256"] = sha(p)
        put(np, neighbor); bundle["neighbors"]["sha256"] = sha(np)
    put(bp, bundle)
    with pytest.raises(EvidenceError):
        import_evidence(bp, cp, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_disagreement_and_failed_question_are_preserved(case):
    bp, cp, bundle = case
    # T4 fails; T5 is a documented Root override of an independent false alarm.
    u = bundle["matrix"][3]; p = Path(u["root"]); r = read(p); r.update(result="fail", root_adopted_candidate=False); put(p, r); u["root_sha256"] = sha(p)
    u = bundle["matrix"][4]; p = Path(u["independent_response"]); response = read(p)
    opinion = json.loads(response["choices"][0]["message"]["content"]); opinion["overall"] = opinion["style"] = "fail"
    response["choices"][0]["message"]["content"] = json.dumps(opinion); put(p, response); u["response_sha256"] = sha(p)
    p = Path(u["root"]); root = read(p); root["deepseek_original_overall"] = "fail"; put(p, root); u["root_sha256"] = sha(p)
    put(bp, bundle)
    st = verify(bp, cp)
    assert st["admission"]["evidence_verdict"] == "pass"
    assert st["questions"]["T4"]["result"] == "fail"
    assert st["questions"]["T5"]["reviews"][0]["review"]["overall"] == "fail"
    assert st["questions"]["T5"]["reviews"][1]["review"]["result"] == "pass"


def test_failed_required_T3_cannot_pass(case):
    bp, cp, bundle = case
    u = bundle["matrix"][2]; p = Path(u["root"]); r = read(p); r.update(result="fail", root_adopted_candidate=False); put(p, r); u["root_sha256"] = sha(p); put(bp, bundle)
    # Stability must also reject reuse of this now-failed trial.
    with pytest.raises(EvidenceError): verify(bp, cp)


def test_empty_bundle_and_existing_output_rejected(case, tmp_path):
    bp, cp, _ = case
    out = tmp_path / "exists"; out.mkdir(); (out / "keep").write_text("untouched")
    with pytest.raises(EvidenceError, match="拒绝覆盖"): import_evidence(bp, cp, out)
    assert (out / "keep").read_text() == "untouched"
    bp.write_text("")
    with pytest.raises(EvidenceError): verify(bp, cp)


def test_cli_consumer(case, tmp_path):
    bp, cp, _ = case
    root = Path(__file__).resolve().parents[1]
    p = subprocess.run([sys.executable, str(root / "scripts/sb.py"), "matrix-evidence", str(bp),
                        "--contract", str(cp), "-o", str(tmp_path / "cli")], cwd=root, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["admission"]["evidence_verdict"] == "pass"


def select_T1_stability(bundle):
    """Use three independent T1 fixture images, without changing matrix evidence."""
    sp = Path(bundle["stability"]["receipt"])
    stable = read(sp)
    t1 = bundle["matrix"][0]
    stable["question"] = "T1"
    stable["cohort"][0] = {"image": t1["original"], "sha256": t1["original_sha256"],
                           "root_ruling": t1["root"]}
    args = read(Path(t1["contract"]).parent / "generation-metadata.json")["actual_arguments"]
    for trial in stable["cohort"][1:]:
        image = Path(trial["image"])
        put(image.parent / "generation-metadata.json", {"source_path": str(image), "actual_arguments": args})
        root = read(t1["root"])
        root["original_sha256"] = trial["sha256"]
        put(Path(trial["root_ruling"]), root)
    put(sp, stable)
    bundle["stability"]["sha256"] = sha(sp)
    return sp, stable


def test_explicit_T1_stability_on_actual_cli(case, tmp_path):
    bp, cp, bundle = case
    select_T1_stability(bundle)
    put(bp, bundle)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/sb.py"), "matrix-evidence", str(bp),
                             "--contract", str(cp), "-o", str(tmp_path / "T1-import")],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    state = read(tmp_path / "T1-import/matrix-evidence-state.json")
    assert state["stability_question"] == "T1"
    assert state["admission"]["evidence_verdict"] == "pass"
    assert state["formal_admission"] is False


@pytest.mark.parametrize("mutation", ["empty_question", "wrong_question", "question_type",
                                    "different_selected_question", "wrong_QA", "missing_matrix_image",
                                    "different_parameters", "duplicate_image"])
def test_explicit_stability_mutations_rejected(case, tmp_path, mutation):
    bp, cp, bundle = case
    sp, stable = select_T1_stability(bundle)
    trial = stable["cohort"][1]
    if mutation == "empty_question":
        stable["question"] = ""
    elif mutation == "wrong_question":
        stable["question"] = "T99"
    elif mutation == "question_type":
        stable["question"] = ["T1"]
    elif mutation == "different_selected_question":
        stable["question"] = "T3"
    elif mutation == "wrong_QA":
        p = Path(trial["root_ruling"])
        root = read(p)
        root["qa_checked"] = read(bundle["matrix"][2]["root"])["qa_checked"]
        put(p, root)
    elif mutation == "missing_matrix_image":
        p = tmp_path / "new-first.png"
        Image.new("RGB", (48, 48), (80, 70, 60)).save(p)
        put(p.parent / "generation-metadata.json", read(Path(trial["image"]).parent / "generation-metadata.json"))
        actual = read(p.parent / "generation-metadata.json")
        actual["source_path"] = str(p)
        put(p.parent / "generation-metadata.json", actual)
        root = read(trial["root_ruling"])
        root["original_sha256"] = sha(p)
        rp = put(tmp_path / "new-first-root.json", root)
        stable["cohort"][0] = {"image": str(p), "sha256": sha(p), "root_ruling": str(rp)}
    elif mutation == "different_parameters":
        p = Path(trial["image"]).parent / "generation-metadata.json"
        actual = read(p)
        actual["actual_arguments"]["transparent_background"] = True
        put(p, actual)
    else:
        stable["cohort"][2] = copy.deepcopy(trial)
    put(sp, stable)
    bundle["stability"]["sha256"] = sha(sp)
    put(bp, bundle)
    with pytest.raises(EvidenceError):
        import_evidence(bp, cp, tmp_path / "rejected-T1")
    assert not (tmp_path / "rejected-T1").exists()


def test_T1_stability_does_not_remove_required_T3(case):
    bp, cp, bundle = case
    select_T1_stability(bundle)
    unit = bundle["matrix"][2]
    p = Path(unit["root"])
    root = read(p)
    root.update(result="fail", root_adopted_candidate=False)
    put(p, root)
    unit["root_sha256"] = sha(p)
    put(bp, bundle)
    state = verify(bp, cp)
    assert state["stability_question"] == "T1"
    assert state["admission"]["evidence_verdict"] == "fail"
