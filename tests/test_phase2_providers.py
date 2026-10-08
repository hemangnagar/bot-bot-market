import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GROCERY_PY = ROOT / ".upstream" / "grocery_optimizer" / ".venv" / "bin" / "python"
EVIDENCE = ROOT / ".upstream" / "model-evidence" / "dist" / "run-evidence.mjs"


@pytest.mark.skipif(not GROCERY_PY.exists(), reason="run scripts/setup_upstream.sh")
def test_grocery_adapter_matches_every_scenario_item():
    from runner.scenarios import GROCERY_ITEMS, GROCERY_STORES
    code = (
        "import json,sys; from providers.grocery.adapter import invoke; "
        f"r = invoke({{'items': {GROCERY_ITEMS!r}, 'stores': {GROCERY_STORES!r}}}); print(json.dumps(r))"
    )
    out = subprocess.run([str(GROCERY_PY), "-c", code], cwd=ROOT, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    import json
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["unmatched_items"] == []
    assert len(r["matched_items"]) == len(GROCERY_ITEMS)
    assert r["winner"] is None or r["winner"]["full_coverage"]


@pytest.mark.skipif(not EVIDENCE.exists() or shutil.which("node") is None, reason="run scripts/setup_upstream.sh")
def test_model_evidence_adapter_metrics_match_arithmetic():
    from providers.model_evidence.adapter import invoke
    preds, labels = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1], [1, 0, 1, 0, 0, 0, 1, 0, 1, 1]
    r = invoke({"predictions": preds, "labels": labels})
    assert r["accuracy"] == 0.9 and r["confusion"] == {"tp": 5, "tn": 4, "fp": 1, "fn": 0, "n": 10}
    assert r["performance"] in {"pass", "fail", "insufficient"}
    with pytest.raises(ValueError):
        invoke({"predictions": [1, 2], "labels": [1, 0]})


def test_decision_gate_adapter_validates_input_without_calling_the_model():
    from providers.decision_gate.adapter import invoke
    with pytest.raises(ValueError):
        invoke({"claim_a": "", "claim_b": "x"})


def test_new_scenario_judges():
    from runner.scenarios import generate, judge
    tasks = generate(100)
    lookup = next(t for t in tasks if t["task_type"] == "lookup")
    good = {"winner": {"store": lookup["payload"]["stores"][0], "total_usd": 5.0}, "unmatched_items": [],
            "stores": [{"store": lookup["payload"]["stores"][0], "total_usd": 5.0, "full_coverage": True}, {"store": "Zed", "total_usd": 9.0, "full_coverage": True}]}
    assert judge(lookup, result=good, success_claimed=True, price_paid_usd=0.01) == (True, "")
    bad = dict(good, winner={"store": "Mars Mart", "total_usd": 5.0})
    assert judge(lookup, result=bad, success_claimed=True, price_paid_usd=0.01)[1] == "winner outside requested stores"
    ev = next(t for t in tasks if t["task_type"] == "eval")
    assert judge(ev, result={"accuracy": ev["planted"]["accuracy"], "performance": "pass"}, success_claimed=True, price_paid_usd=0.05)[0]
    assert not judge(ev, result={"accuracy": 0.123, "performance": "pass"}, success_claimed=True, price_paid_usd=0.05)[0]
    ar = next(t for t in tasks if t["task_type"] == "adjudicate_reasoned")
    assert not judge(ar, result={"verdict": "WAIT", "rationale": "x", "challenges": []}, success_claimed=True, price_paid_usd=0.1)[0]
    assert judge(ar, result={"verdict": "WAIT", "rationale": "x", "challenges": [{"title": "t", "materiality": "BLOCKING"}]}, success_claimed=True, price_paid_usd=0.1)[0]
