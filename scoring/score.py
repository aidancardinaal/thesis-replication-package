#!/usr/bin/env python3
"""
Shared scorer for the ASPEC evaluation.

Applies the *same* deterministic procedure to every generated ASPEC — baseline
(single-shot) and pipeline (participant) alike — so that the head-to-head is
fair. It computes only the measures that can be computed without human judgment:

  1. Schema validity   — does the ASPEC conform to aspec.schema.json?
  2. Field completeness — structural presence against the scenario ground truth,
                          per the thesis Measures definition: trigger present,
                          the expected number of action steps, a credential entry
                          per required service, and a resource entry per required
                          resource; the proportion present and non-empty.
  3. Divergence diff    — an element-level comparison of each ASPEC against the
                          ground truth (trigger adapter, step adapters,
                          credentials, resources, per-step parameters). The
                          scorer only *flags* where a value differs; it never
                          judges the *source* of a divergence (system error vs
                          participant choice). Source attribution is the manual
                          rubric pass, recorded separately, using the interaction
                          logs as evidence.

It also extracts dialogue/elicitation counts from the interaction logs (pipeline
only), decomposed by stage.

Specification correctness itself is NOT decided here. The scorer pre-computes the
exact-match form of each rubric dimension (trigger / steps / credentials /
resources) as a starting point, but the semantic-match and adapter-valid modes
require the manual rubric pass. The scorer's job is to remove all the mechanical,
error-prone work from that pass and to make it auditable.

Everything except schema validity is standard-library only. Schema validity
needs `jsonschema`; if it is not importable, the scorer still runs and records
validity as "unavailable" so the rest of the table is produced.

Usage (run from anywhere; paths are resolved relative to this file):
    python scoring/score.py                 # score all baseline + pipeline ASPECs
    python scoring/score.py --baseline-run baseline/outputs/run_XXXX
    python scoring/score.py --out scoring/outputs/run_manual
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
SCORING_DIR = Path(__file__).resolve().parent
PIPELINE_SRC = SCORING_DIR.parent                 # thesis-new-pipeline/
PROJECT_ROOT = PIPELINE_SRC.parent                # Thesis/pipeline/

SCHEMA_PATH = PIPELINE_SRC / "aspec.schema.json"
GROUND_TRUTH_DIR = PIPELINE_SRC / "scenarios" / "ground-truths"
USER_ASPEC_DIR = PIPELINE_SRC / "scenarios" / "user-aspecs"
INTERACTION_LOG_DIR = PIPELINE_SRC / "scenarios" / "interaction-logs"
BASELINE_OUTPUT_DIR = PIPELINE_SRC / "baseline" / "outputs"
DEFAULT_OUT_DIR = SCORING_DIR / "outputs"

PIPELINE_FILE_RE = re.compile(r"P(\d+)-scenario-(\d+)_", re.IGNORECASE)
BASELINE_SAMPLE_RE = re.compile(r"scenario-(\d+)[/\\]sample-(\d+)\.json$", re.IGNORECASE)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #
def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _norm(value: Any) -> str:
    """Order-insensitive, whitespace-trimmed canonical form for value compare."""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(value)


def _values_equal(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


# --------------------------------------------------------------------------- #
# Schema validity
# --------------------------------------------------------------------------- #
def get_validator(schema_path: Path):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return None
    schema = load_json(schema_path)
    return Draft202012Validator(schema)


def schema_validity(aspec: Any, validator) -> tuple[Any, list[str]]:
    """Return (True/False/None, first-5 error summaries). None = validator absent."""
    if validator is None:
        return None, ["jsonschema not installed; validity unavailable"]
    errors = sorted(validator.iter_errors(aspec), key=lambda e: list(e.path))
    if not errors:
        return True, []
    summary = []
    for err in errors[:5]:
        loc = "/".join(str(p) for p in err.path) or "<root>"
        summary.append(f"{loc}: {err.message}")
    return False, summary


# --------------------------------------------------------------------------- #
# Structure extraction (works regardless of dict-key naming)
# --------------------------------------------------------------------------- #
def _trigger_adapter(aspec: dict) -> str | None:
    trig = aspec.get("trigger") or {}
    return trig.get("adapter_id")


def _step_adapters(aspec: dict) -> list[str]:
    return [s.get("adapter_id") for s in aspec.get("steps", []) if isinstance(s, dict)]


def _credential_auth_types(aspec: dict) -> set[str]:
    creds = aspec.get("credentials") or {}
    return {c.get("auth_type") for c in creds.values() if isinstance(c, dict) and c.get("auth_type")}


def _resource_ids(aspec: dict) -> set[str]:
    res = aspec.get("resources") or {}
    return {r.get("id") for r in res.values() if isinstance(r, dict) and r.get("id")}


def _resource_names(aspec: dict) -> set[str]:
    res = aspec.get("resources") or {}
    return {r.get("name") for r in res.values() if isinstance(r, dict) and r.get("name")}


# --------------------------------------------------------------------------- #
# Field completeness (structural presence vs ground truth)
# --------------------------------------------------------------------------- #
def field_completeness(aspec: dict, gt: dict) -> dict:
    exp_steps = len(gt.get("steps", []))
    exp_creds = len(gt.get("credentials") or {})
    exp_res = len(gt.get("resources") or {})
    total_exp = 1 + exp_steps + exp_creds + exp_res

    trigger_filled = 1 if _trigger_adapter(aspec) else 0
    steps_filled = min(sum(1 for a in _step_adapters(aspec) if a), exp_steps)
    creds_filled = min(len(_credential_auth_types(aspec)), exp_creds)
    res_filled = min(len(_resource_ids(aspec) | _resource_names(aspec)), exp_res)

    present = trigger_filled + steps_filled + creds_filled + res_filled
    score = round(present / total_exp, 4) if total_exp else 0.0
    return {
        "trigger": f"{trigger_filled}/1",
        "steps": f"{steps_filled}/{exp_steps}",
        "credentials": f"{creds_filled}/{exp_creds}",
        "resources": f"{res_filled}/{exp_res}",
        "score": score,
    }


# --------------------------------------------------------------------------- #
# Divergence diff vs ground truth (flags only; no source attribution)
# --------------------------------------------------------------------------- #
def _param_diffs(gt_step: dict, cand_step: dict) -> list[dict]:
    gt_params = gt_step.get("configured_parameters", {}) or {}
    cand_params = cand_step.get("configured_parameters", {}) or {}
    diffs = []
    for key in sorted(set(gt_params) | set(cand_params)):
        in_gt, in_cand = key in gt_params, key in cand_params
        if in_gt and in_cand:
            if not _values_equal(gt_params[key], cand_params[key]):
                diffs.append({"param": key, "status": "differ",
                              "gt": gt_params[key], "candidate": cand_params[key]})
        elif in_gt:
            diffs.append({"param": key, "status": "missing_in_candidate",
                          "gt": gt_params[key], "candidate": None})
        else:
            diffs.append({"param": key, "status": "extra_in_candidate",
                          "gt": None, "candidate": cand_params[key]})
    return diffs


def _pair_steps_by_adapter(gt_steps: list[dict], cand_steps: list[dict]) -> list[tuple]:
    """Pair candidate steps to GT steps by adapter_id, preserving order for dupes."""
    pairs = []
    remaining = list(cand_steps)
    for gt_step in gt_steps:
        match = None
        for i, cand in enumerate(remaining):
            if cand.get("adapter_id") == gt_step.get("adapter_id"):
                match = remaining.pop(i)
                break
        pairs.append((gt_step, match))
    for leftover in remaining:  # candidate steps with no GT counterpart
        pairs.append((None, leftover))
    return pairs


def divergence_diff(aspec: dict, gt: dict) -> dict:
    out: dict[str, Any] = {}

    # Trigger
    out["trigger"] = {
        "adapter_gt": _trigger_adapter(gt),
        "adapter_candidate": _trigger_adapter(aspec),
        "adapter_match": _trigger_adapter(gt) == _trigger_adapter(aspec),
        "param_diffs": _param_diffs(gt.get("trigger", {}), aspec.get("trigger", {})),
    }

    # Steps (paired by adapter)
    step_records = []
    for gt_step, cand_step in _pair_steps_by_adapter(gt.get("steps", []), aspec.get("steps", [])):
        rec = {
            "adapter_gt": gt_step.get("adapter_id") if gt_step else None,
            "adapter_candidate": cand_step.get("adapter_id") if cand_step else None,
        }
        if gt_step and cand_step:
            rec["param_diffs"] = _param_diffs(gt_step, cand_step)
        elif gt_step and not cand_step:
            rec["status"] = "step_missing_in_candidate"
        else:
            rec["status"] = "extra_step_in_candidate"
        step_records.append(rec)
    out["steps"] = step_records

    # Credentials (by auth_type set)
    gt_auth, cand_auth = _credential_auth_types(gt), _credential_auth_types(aspec)
    out["credentials"] = {
        "gt": sorted(gt_auth),
        "candidate": sorted(cand_auth),
        "match": gt_auth == cand_auth,
        "missing": sorted(gt_auth - cand_auth),
        "extra": sorted(cand_auth - gt_auth),
    }

    # Resources (by id, with names for readability)
    gt_ids, cand_ids = _resource_ids(gt), _resource_ids(aspec)
    out["resources"] = {
        "gt_ids": sorted(gt_ids),
        "candidate_ids": sorted(cand_ids),
        "gt_names": sorted(_resource_names(gt)),
        "candidate_names": sorted(_resource_names(aspec)),
        "match": gt_ids == cand_ids,
        "missing": sorted(gt_ids - cand_ids),
        "extra": sorted(cand_ids - gt_ids),
    }
    return out


def _count_param_divergences(diff: dict) -> int:
    n = len(diff["trigger"]["param_diffs"])
    for s in diff["steps"]:
        n += len(s.get("param_diffs", []))
        if s.get("status"):
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Exact-match rubric dimensions (starting point for the manual pass)
# --------------------------------------------------------------------------- #
def exact_match_dimensions(diff: dict) -> dict:
    trigger_ok = diff["trigger"]["adapter_match"] and not diff["trigger"]["param_diffs"]
    steps_ok = all(
        s.get("adapter_candidate") == s.get("adapter_gt")
        and not s.get("param_diffs")
        and not s.get("status")
        for s in diff["steps"]
    ) and bool(diff["steps"])
    return {
        "trigger_exact": trigger_ok,
        "steps_exact": steps_ok,
        "credentials_exact": diff["credentials"]["match"],
        "resources_exact": diff["resources"]["match"],
    }


# --------------------------------------------------------------------------- #
# Scoring one ASPEC
# --------------------------------------------------------------------------- #
def score_aspec(aspec: dict, gt: dict, validator, *, condition: str,
                scenario: int, unit_id: str, source_file: str) -> dict:
    valid, errors = schema_validity(aspec, validator)
    completeness = field_completeness(aspec, gt)
    diff = divergence_diff(aspec, gt)
    dims = exact_match_dimensions(diff)
    return {
        "condition": condition,
        "scenario": scenario,
        "unit_id": unit_id,
        "schema_valid": valid,
        "schema_errors": errors,
        "completeness": completeness,
        "exact_dimensions": dims,
        "n_param_divergences": _count_param_divergences(diff),
        "structure": {
            "trigger_adapter": _trigger_adapter(aspec),
            "step_adapters": _step_adapters(aspec),
            "n_steps": len(aspec.get("steps", [])),
        },
        "divergences": diff,
        "source_file": source_file,
    }


# --------------------------------------------------------------------------- #
# Corpus discovery
# --------------------------------------------------------------------------- #
def latest_baseline_run() -> Path | None:
    if not BASELINE_OUTPUT_DIR.exists():
        return None
    runs = sorted(p for p in BASELINE_OUTPUT_DIR.glob("run_*") if p.is_dir())
    return runs[-1] if runs else None


def discover_pipeline() -> list[dict]:
    items = []
    for path in sorted(USER_ASPEC_DIR.glob("P*-scenario-*.json")):  # excludes old/
        m = PIPELINE_FILE_RE.search(path.name)
        if not m:
            continue
        participant, scenario = int(m.group(1)), int(m.group(2))
        items.append({"path": path, "condition": "pipeline", "scenario": scenario,
                      "unit_id": f"P{participant}-S{scenario}"})
    return items


def discover_baseline(run_dir: Path) -> list[dict]:
    items = []
    for path in sorted(run_dir.glob("scenario-*/sample-*.json")):
        rel = path.relative_to(run_dir).as_posix()
        m = BASELINE_SAMPLE_RE.search(rel)
        if not m:
            continue
        scenario, sample = int(m.group(1)), int(m.group(2))
        items.append({"path": path, "condition": "baseline", "scenario": scenario,
                      "unit_id": f"base-S{scenario}-s{sample:02d}"})
    return items


# --------------------------------------------------------------------------- #
# Elicitation counts from interaction logs (pipeline only)
# --------------------------------------------------------------------------- #
def elicitation_rows() -> list[dict]:
    rows = []
    if not INTERACTION_LOG_DIR.exists():
        return rows
    for path in sorted(INTERACTION_LOG_DIR.glob("P*-scenario-*.json")):
        m = PIPELINE_FILE_RE.search(path.name)
        if not m:
            continue
        participant, scenario = int(m.group(1)), int(m.group(2))
        log = load_json(path)
        stages: dict[str, int] = {}
        for it in log.get("interactions", []):
            stage = it.get("stage", "unknown")
            stages[stage] = stages.get(stage, 0) + 1
        rows.append({
            "participant": f"P{participant}",
            "scenario": scenario,
            "clarifying": stages.get("clarifying", 0),
            "configuring_resources": stages.get("configuring_resources", 0),
            "configuring_parameters": stages.get("configuring_parameters", 0),
            "total_elicited": sum(stages.values()),
            "source_file": path.name,
        })
    return rows


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run(baseline_run: Path | None, out_dir: Path) -> None:
    validator = get_validator(SCHEMA_PATH)
    if validator is None:
        print("WARNING: jsonschema not installed — schema validity will be 'unavailable'.\n"
              "         Run with the pipeline venv to get validity, e.g. "
              "venv/bin/python scoring/score.py\n")

    # Ground truths
    ground_truths: dict[int, dict] = {}
    for path in sorted(GROUND_TRUTH_DIR.glob("scenario-*-ground-truth.json")):
        m = re.search(r"scenario-(\d+)-ground-truth", path.name)
        if m:
            ground_truths[int(m.group(1))] = load_json(path)
    if not ground_truths:
        sys.exit(f"No ground truths found in {GROUND_TRUTH_DIR}")

    # Corpus
    baseline_run = baseline_run or latest_baseline_run()
    corpus = discover_pipeline()
    if baseline_run:
        corpus += discover_baseline(baseline_run)
    else:
        print("WARNING: no baseline run found; scoring pipeline outputs only.\n")
    if not corpus:
        sys.exit("No ASPECs found to score.")

    out_dir = out_dir / f"score_{_utc_stamp()}"
    per_dir = out_dir / "per-output"
    per_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for item in corpus:
        scenario = item["scenario"]
        gt = ground_truths.get(scenario)
        if gt is None:
            print(f"  skip {item['unit_id']}: no ground truth for scenario {scenario}")
            continue
        aspec = load_json(item["path"])
        res = score_aspec(
            aspec, gt, validator,
            condition=item["condition"], scenario=scenario,
            unit_id=item["unit_id"],
            source_file=str(item["path"].relative_to(PROJECT_ROOT)),
        )
        results.append(res)
        with open(per_dir / f"{item['unit_id']}.json", "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2, ensure_ascii=False)

    results.sort(key=lambda r: (r["scenario"], r["condition"], r["unit_id"]))

    # scores.csv — the master deterministic table (feeds T2 / structural T3)
    scores_csv = out_dir / "scores.csv"
    with open(scores_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "condition", "unit_id", "schema_valid",
                    "completeness", "comp_trigger", "comp_steps", "comp_creds",
                    "comp_resources", "trigger_exact", "steps_exact",
                    "credentials_exact", "resources_exact", "n_steps",
                    "trigger_adapter", "n_param_divergences", "source_file"])
        for r in results:
            c, d = r["completeness"], r["exact_dimensions"]
            w.writerow([r["scenario"], r["condition"], r["unit_id"], r["schema_valid"],
                        c["score"], c["trigger"], c["steps"], c["credentials"],
                        c["resources"], d["trigger_exact"], d["steps_exact"],
                        d["credentials_exact"], d["resources_exact"],
                        r["structure"]["n_steps"], r["structure"]["trigger_adapter"],
                        r["n_param_divergences"], r["source_file"]])

    # divergences.json — full diffs for the manual attribution pass (feeds T_attr)
    with open(out_dir / "divergences.json", "w", encoding="utf-8") as fh:
        json.dump([{"unit_id": r["unit_id"], "scenario": r["scenario"],
                    "condition": r["condition"], "divergences": r["divergences"]}
                   for r in results], fh, indent=2, ensure_ascii=False)

    # elicitation.csv — dialogue measure, pipeline only (feeds T4)
    elic = elicitation_rows()
    elic_csv = out_dir / "elicitation.csv"
    with open(elic_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["participant", "scenario", "clarifying",
                                           "configuring_resources",
                                           "configuring_parameters",
                                           "total_elicited", "source_file"])
        w.writeheader()
        for row in sorted(elic, key=lambda x: (x["participant"], x["scenario"])):
            w.writerow(row)

    # Console summary
    print(f"Scored {len(results)} ASPECs  ->  {out_dir}")
    print(f"  baseline run: {baseline_run.relative_to(PROJECT_ROOT) if baseline_run else 'none'}\n")
    print(f"{'scenario':>8} {'condition':<9} {'unit_id':<16} {'valid':<11} "
          f"{'compl':>6}  {'T':<1}{'S':<1}{'C':<1}{'R':<1} {'pdiv':>4}")
    for r in results:
        d = r["exact_dimensions"]
        flags = "".join("Y" if d[k] else "." for k in
                        ("trigger_exact", "steps_exact", "credentials_exact", "resources_exact"))
        valid = {True: "valid", False: "INVALID", None: "n/a"}[r["schema_valid"]]
        print(f"{r['scenario']:>8} {r['condition']:<9} {r['unit_id']:<16} {valid:<11} "
              f"{r['completeness']['score']:>6} {flags} {r['n_param_divergences']:>4}")
    print("\n  T/S/C/R = exact-match trigger/steps/credentials/resources vs ground truth")
    print("  pdiv    = number of flagged parameter divergences (for manual attribution)")
    print(f"\n  elicitation.csv: {len(elic)} pipeline sessions")
    print("  Outputs: scores.csv, divergences.json, elicitation.csv, per-output/*.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="Score ASPEC evaluation outputs.")
    ap.add_argument("--baseline-run", type=str, default=None,
                    help="Path to a baseline run dir (default: latest under baseline/outputs).")
    ap.add_argument("--out", type=str, default=str(DEFAULT_OUT_DIR),
                    help="Output base directory (default: scoring/outputs).")
    args = ap.parse_args()
    run_dir = Path(args.baseline_run).resolve() if args.baseline_run else None
    run(run_dir, Path(args.out).resolve())


if __name__ == "__main__":
    main()