"""Provider adapter for github.com/hemangnagar/model-evidence, run as a Node.js subprocess.

Predictions + labels in, metrics out. Writes a contract-1.0 bundle and a policy to a temp dir and runs
`node dist/run-evidence.mjs bundle.json policy.json --out verdict.json --quiet` from the pinned clone
under .upstream/model-evidence (see scripts/setup_upstream.sh). Exit code 3 is an input error.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = Path(os.environ.get("BENCH_MODEL_EVIDENCE_DIR", ROOT / ".upstream" / "model-evidence"))
CLI = UPSTREAM / "dist" / "run-evidence.mjs"
DEFAULT_POLICY = {"accuracy": 0.8, "precision": 0.7, "recall": 0.7, "fpr": 0.3}


def _binary_list(v, name: str) -> list[int]:
    if not isinstance(v, list) or not v:
        raise ValueError(f"payload.{name} must be a non-empty list of 0/1")
    out = []
    for x in v:
        if x in (0, 1, True, False) or x in ("0", "1"):
            out.append(int(x))
        else:
            raise ValueError(f"payload.{name} must contain only 0/1 values")
    return out


def invoke(payload: dict) -> dict:
    if not CLI.exists():
        raise RuntimeError(f"model-evidence not found at {CLI}; run scripts/setup_upstream.sh")
    preds = _binary_list(payload.get("predictions"), "predictions")
    labels = _binary_list(payload.get("labels"), "labels")
    if len(preds) != len(labels):
        raise ValueError("predictions and labels must have the same length")
    thresholds = {**DEFAULT_POLICY, **{k: float(v) for k, v in (payload.get("thresholds") or {}).items() if k in DEFAULT_POLICY}}
    bundle = {"name": "broker-bench", "split": "test",
              "runs": [{"id": "seed-0", "seed": 0, "rows": [{"sample_id": str(i), "y_true": y, "y_pred": p} for i, (y, p) in enumerate(zip(labels, preds))]}]}
    policy = {**thresholds, "confidence": False, "minRuns": 2, "maxSpread": 0.05, "selectedRun": "seed-0"}
    with tempfile.TemporaryDirectory() as d:
        bp, pp, vp = Path(d, "bundle.json"), Path(d, "policy.json"), Path(d, "verdict.json")
        bp.write_text(json.dumps(bundle))
        pp.write_text(json.dumps(policy))
        proc = subprocess.run(["node", str(CLI), str(bp), str(pp), "--out", str(vp), "--quiet"], capture_output=True, text=True, timeout=60)
        if proc.returncode == 3 or not vp.exists():
            raise ValueError(f"model-evidence rejected the input: {(proc.stderr or proc.stdout).strip()[:300]}")
        verdict = json.loads(vp.read_text())
    view = verdict["views"]["default"]
    metrics = view["runs"][0]["metrics"]
    return {
        "accuracy": metrics["accuracy"]["value"], "precision": metrics["precision"]["value"], "recall": metrics["recall"]["value"],
        "fpr": metrics["fpr"]["value"],
        "intervals_95": {k: metrics[k]["interval"] for k in ("accuracy", "precision", "recall", "fpr")},
        "confusion": {k: metrics[k] for k in ("tp", "tn", "fp", "fn", "n")},
        "gates": {g["key"]: {"target": g["target"], "status": g["status"]} for g in view["gates"]},
        "performance": view["performance"], "stability": view["stability"], "reproducibility": view["reproducibility"],
        "overall": verdict["overall"], "engine_version": verdict["engine_version"],
    }
