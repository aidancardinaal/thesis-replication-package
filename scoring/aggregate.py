#!/usr/bin/env python3
"""
Aggregator (Phase 4): turns the filled attribution worksheet into the Results
numbers.

Inputs (all inside one score run dir, plus the rubric and ground truths):
  - scores.csv               : schema validity + field completeness (deterministic)
  - per-output/*.json        : the element-level diff per ASPEC (deterministic)
  - attribution_worksheet.csv: the manual pass (points + decision), filled by hand
  - elicitation.csv          : dialogue measure (pipeline only)
  - ground-truths/*.json     : the reference ASPECs (denominator + populated params)
  - rubric markdown          : per-scenario parameter classification (scoring modes)

Outputs (written next to the inputs in the run dir):
  - results_per_unit.csv             : one row per ASPEC (all measures)
  - T2_output_quality.csv            : schema validity / completeness / correctness
                                       per scenario x condition
  - T3_correctness_by_dimension.csv  : correctness per rubric dimension
  - T_attribution.csv                : divergence source counts per condition/scenario
  - T4_elicitation.csv               : elicitation summary per scenario (pipeline)

How specification correctness is computed (operationalisation of the rubric):
  Scorable items per ASPEC =
    trigger adapter_id (Exact)
    + each ground-truth-populated, rubric-classified trigger parameter
    + step count (Exact) + step order (Exact)
    + per ground-truth step: adapter_id (Exact) + each populated classified parameter
    + per ground-truth credential: present with matching auth_type (Exact)
    + per ground-truth resource: present with matching id (Exact)
  Per item, awarded:
    - deterministic Exact items   -> 1 if it matches the ground truth, else 0
    - classified parameters       -> 1 if it did not diverge from the ground truth;
                                     if it diverged: missing -> 0; extra -> not scored;
                                     differ + Exact -> 0;
                                     differ + Semantic/Adapter-valid -> the manual
                                     `points` from the worksheet (1 = equivalent /
                                     catalogue-valid). Rubric note honoured: a wrong
                                     step adapter excludes that step's parameters.
  Correctness = sum(awarded) / sum(possible), per dimension and overall (0-1).

  Two correctness figures are reported, per the method:
    - correctness        : the pre-registered headline (no source adjustment;
                           an Exact parameter the participant changed scores 0).
    - correctness_sys    : the secondary, system-error-conditional view that
                           EXCLUDES items marked `participant_divergence`
                           (numerator and denominator). Pipeline only.

This script makes NO judgments. If the worksheet still has unmarked Semantic /
Adapter-valid `differ` rows, it reports them as `n_unscored`, computes a clearly
flagged PROVISIONAL correctness over the scored items only, and prints how many
rows still need attention.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

SCORING_DIR = Path(__file__).resolve().parent
PIPELINE_SRC = SCORING_DIR.parent
PROJECT_ROOT = PIPELINE_SRC.parent
THESIS_DIR = PROJECT_ROOT.parent
OUTPUTS_DIR = SCORING_DIR / "outputs"
GROUND_TRUTH_DIR = PIPELINE_SRC / "scenarios" / "ground-truths"
DEFAULT_RUBRIC = (THESIS_DIR / "Report" / "Method & Results" / "Experiment"
                  / "specification-correctness-rubric.md")

VALID_MODES = {"Exact", "Semantic", "Adapter-valid"}
DIMENSIONS = ["Trigger", "Steps", "Credentials", "Resources"]
PIPE_DECISIONS = {"match", "system_error", "participant_divergence"}
BASE_DECISIONS = {"match", "model_error", "adapter_valid_ok"}


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def norm_decision(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", (value or "").strip().lower())


def mean(xs: list[float]) -> float | str:
    return round(sum(xs) / len(xs), 4) if xs else ""


# --------------------------------------------------------------------------- #
# Rubric parser (keeps role / step index / adapter, not just param -> mode)
# --------------------------------------------------------------------------- #
def parse_rubric_full(path: Path) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    if not path.exists():
        return out
    current: int | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        h = re.match(r"^###\s+Scenario\s+(\d+)", line)
        if h:
            current = int(h.group(1))
            out[current] = []
            continue
        if current is None or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        step_cell, param, mode = cells[0], cells[1].strip("` ").strip(), cells[2].strip()
        if mode not in VALID_MODES or not param:
            continue
        adapter_m = re.search(r"`([^`]+)`", step_cell)
        adapter = adapter_m.group(1) if adapter_m else None
        if step_cell.lower().startswith("trigger"):
            role, idx = "trigger", None
        else:
            sm = re.search(r"step\s+(\d+)", step_cell, re.IGNORECASE)
            role, idx = "step", (int(sm.group(1)) - 1 if sm else None)
        out[current].append({"role": role, "step_index": idx,
                             "adapter_id": adapter, "param": param, "mode": mode})
    return out


# --------------------------------------------------------------------------- #
# Worksheet index
# --------------------------------------------------------------------------- #
def parse_location(loc: str) -> tuple[str, int | None]:
    if loc == "trigger":
        return "trigger", None
    if loc.startswith("step["):
        return "step", int(loc[loc.index("[") + 1: loc.index("]")])
    if loc in ("credentials", "resources"):
        return loc, None
    return "other", None


def load_worksheet(path: Path) -> dict[str, dict[tuple, dict]]:
    """unit_id -> {(role, step_index, element): row}"""
    index: dict[str, dict[tuple, dict]] = defaultdict(dict)
    if not path.exists():
        return index
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            role, idx = parse_location(row["location"])
            index[row["unit_id"]][(role, idx, row["element"])] = row
    return index


def ws_get(ws_unit: dict, role: str, idx: int | None, element: str) -> dict | None:
    row = ws_unit.get((role, idx, element))
    if row is None and "." in element:                     # nested -> parent
        row = ws_unit.get((role, idx, element.split(".")[0]))
    return row


# --------------------------------------------------------------------------- #
# Per-parameter award
# --------------------------------------------------------------------------- #
def find_param_diff(param_diffs: list[dict], param: str) -> dict | None:
    for pd in param_diffs:
        if pd["param"] == param or pd["param"] == param.split(".")[0]:
            return pd
    return None


def score_param(param: str, mode: str, param_diffs: list[dict],
                ws_row: dict | None) -> dict:
    """Return {awarded: 0/1/None, scored: bool, decision: str}."""
    decision = norm_decision(ws_row["decision"]) if ws_row else "match"
    pd = find_param_diff(param_diffs, param)
    if pd is None:                                          # matched
        return {"awarded": 1, "scored": True, "decision": "match"}
    status = pd["status"]
    if status == "extra_in_candidate":
        return {"awarded": None, "scored": False, "decision": decision}   # not scored
    if status == "missing_in_candidate":
        return {"awarded": 0, "scored": True, "decision": decision}
    # differ
    if mode == "Exact":
        return {"awarded": 0, "scored": True, "decision": decision}
    pts = (ws_row or {}).get("points", "").strip()
    if pts in ("0", "1"):
        return {"awarded": int(pts), "scored": True, "decision": decision}
    return {"awarded": None, "scored": False, "decision": decision}        # needs input


# --------------------------------------------------------------------------- #
# Score one ASPEC
# --------------------------------------------------------------------------- #
def score_unit(unit_id: str, condition: str, scenario: int, gt: dict,
               diff: dict, structure: dict, cls: list[dict],
               ws_unit: dict) -> dict:
    # items: list of {dim, awarded, scored, decision}
    items: list[dict] = []

    def add(dim, awarded, scored, decision):
        items.append({"dim": dim, "awarded": awarded, "scored": scored,
                      "decision": decision})

    # ---- Trigger ----
    t = diff["trigger"]
    dec = norm_decision(ws_get(ws_unit, "trigger", None, "adapter_id")["decision"]) \
        if ws_get(ws_unit, "trigger", None, "adapter_id") else "match"
    add("Trigger", 1 if t["adapter_match"] else 0, True,
        "match" if t["adapter_match"] else dec)
    gt_trig_params = gt.get("trigger", {}).get("configured_parameters", {})
    for c in [c for c in cls if c["role"] == "trigger"]:
        p = c["param"]
        if not (p in gt_trig_params or p.split(".")[0] in gt_trig_params):
            continue                                        # not populated in GT
        r = score_param(p, c["mode"], t["param_diffs"],
                        ws_get(ws_unit, "trigger", None, p))
        add("Trigger", r["awarded"], r["scored"], r["decision"])

    # ---- Steps ----
    gt_steps = gt.get("steps", [])
    gt_seq = [s.get("adapter_id") for s in gt_steps]
    cand_seq = structure.get("step_adapters", [])
    add("Steps", 1 if len(cand_seq) == len(gt_seq) else 0, True, "match")          # count
    add("Steps", 1 if cand_seq == gt_seq else 0, True, "match")                    # order
    diff_steps = diff.get("steps", [])
    for i, gt_step in enumerate(gt_steps):
        dstep = diff_steps[i] if i < len(diff_steps) else {}
        adapter_ok = (not dstep.get("status")) and \
            dstep.get("adapter_gt") == dstep.get("adapter_candidate")
        loc_dec_row = ws_get(ws_unit, "step", i, "adapter_id")
        add("Steps", 1 if adapter_ok else 0, True,
            "match" if adapter_ok else (norm_decision(loc_dec_row["decision"])
                                        if loc_dec_row else "match"))
        if not adapter_ok:
            continue                                        # rubric: skip params on wrong adapter
        gt_step_params = gt_step.get("configured_parameters", {})
        for c in [c for c in cls if c["role"] == "step" and c["step_index"] == i]:
            p = c["param"]
            if not (p in gt_step_params or p.split(".")[0] in gt_step_params):
                continue
            r = score_param(p, c["mode"], dstep.get("param_diffs", []),
                            ws_get(ws_unit, "step", i, p))
            add("Steps", r["awarded"], r["scored"], r["decision"])

    # ---- Credentials (per GT auth_type) ----
    cred = diff["credentials"]
    cred_row = ws_get(ws_unit, "credentials", None, "auth_type_set")
    cred_dec = norm_decision(cred_row["decision"]) if cred_row else "match"
    for auth in cred["gt"]:
        ok = auth in cred["candidate"]
        add("Credentials", 1 if ok else 0, True, "match" if ok else cred_dec)

    # ---- Resources (per GT id) ----
    res = diff["resources"]
    if res["gt_ids"]:
        res_row = ws_get(ws_unit, "resources", None, "resource_ids")
        res_dec = norm_decision(res_row["decision"]) if res_row else "match"
        for rid in res["gt_ids"]:
            ok = rid in res["candidate_ids"]
            add("Resources", 1 if ok else 0, True, "match" if ok else res_dec)

    # ---- aggregate ----
    out = {"unit_id": unit_id, "condition": condition, "scenario": scenario}
    n_unscored = sum(1 for it in items if not it["scored"])
    out["n_items"] = len(items)
    out["n_unscored"] = n_unscored

    def corr(subset: list[dict], *, exclude_part_div: bool) -> Any:
        poss = aw = 0
        for it in subset:
            if not it["scored"] or it["awarded"] is None:
                continue
            if exclude_part_div and it["decision"] == "participant_divergence":
                continue
            poss += 1
            aw += it["awarded"]
        return round(aw / poss, 4) if poss else ""

    for dim in DIMENSIONS:
        out[f"corr_{dim.lower()}"] = corr([it for it in items if it["dim"] == dim],
                                          exclude_part_div=False)
    out["correctness"] = corr(items, exclude_part_div=False)
    out["correctness_sys"] = corr(items, exclude_part_div=True) \
        if condition == "pipeline" else out.get("correctness")
    out["provisional"] = n_unscored > 0

    # decision tally over worksheet rows of this unit
    tally: dict[str, int] = defaultdict(int)
    for (role, idx, element), row in ws_unit.items():
        tally[norm_decision(row["decision"]) or "unmarked"] += 1
    out["decisions"] = dict(tally)
    return out


# --------------------------------------------------------------------------- #
# Load deterministic scores.csv (schema validity, completeness)
# --------------------------------------------------------------------------- #
def load_scores(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows[r["unit_id"]] = r
    return rows


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def latest_run() -> Path | None:
    runs = sorted(p for p in OUTPUTS_DIR.glob("score_*") if p.is_dir())
    return runs[-1] if runs else None


def run(run_dir: Path, rubric_path: Path) -> None:
    classification = parse_rubric_full(rubric_path)
    if not classification:
        print(f"WARNING: rubric not parsed from {rubric_path}.\n")
    ground_truths = {int(re.search(r"scenario-(\d+)", p.name).group(1)): load_json(p)
                     for p in GROUND_TRUTH_DIR.glob("scenario-*-ground-truth.json")}
    scores = load_scores(run_dir / "scores.csv")
    ws = load_worksheet(run_dir / "attribution_worksheet.csv")
    if not ws:
        print("WARNING: attribution_worksheet.csv not found or empty — correctness "
              "will be deterministic-only and Semantic/Adapter-valid differs unscored.\n")

    per_unit: list[dict] = []
    for unit_id, srow in scores.items():
        per_path = run_dir / "per-output" / f"{unit_id}.json"
        if not per_path.exists():
            continue
        rec = load_json(per_path)
        scenario = rec["scenario"]
        gt = ground_truths.get(scenario)
        if gt is None:
            continue
        scored = score_unit(unit_id, rec["condition"], scenario, gt,
                            rec["divergences"], rec["structure"],
                            classification.get(scenario, []), ws.get(unit_id, {}))
        scored["schema_valid"] = srow.get("schema_valid")
        scored["completeness"] = float(srow["completeness"]) if srow.get("completeness") else ""
        per_unit.append(scored)

    per_unit.sort(key=lambda r: (r["scenario"], r["condition"], r["unit_id"]))

    # ---- results_per_unit.csv ----
    with open(run_dir / "results_per_unit.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "condition", "unit_id", "schema_valid", "completeness",
                    "corr_trigger", "corr_steps", "corr_credentials", "corr_resources",
                    "correctness", "correctness_sys", "n_items", "n_unscored",
                    "provisional"])
        for r in per_unit:
            w.writerow([r["scenario"], r["condition"], r["unit_id"], r["schema_valid"],
                        r["completeness"], r["corr_trigger"], r["corr_steps"],
                        r["corr_credentials"], r["corr_resources"], r["correctness"],
                        r["correctness_sys"], r["n_items"], r["n_unscored"],
                        r["provisional"]])

    # ---- T2 / T3 aggregated per scenario x condition ----
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in per_unit:
        groups[(r["scenario"], r["condition"])].append(r)

    def valid_frac(rs):
        vals = [1 if str(r["schema_valid"]) == "True" else 0 for r in rs]
        return f"{sum(vals)}/{len(vals)}" if vals else ""

    with open(run_dir / "T2_output_quality.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "condition", "n", "schema_valid",
                    "completeness_mean", "correctness_mean", "correctness_sys_mean"])
        for (scen, cond) in sorted(groups):
            rs = groups[(scen, cond)]
            w.writerow([scen, cond, len(rs), valid_frac(rs),
                        mean([r["completeness"] for r in rs if r["completeness"] != ""]),
                        mean([r["correctness"] for r in rs if r["correctness"] != ""]),
                        mean([r["correctness_sys"] for r in rs if r["correctness_sys"] != ""])])

    with open(run_dir / "T3_correctness_by_dimension.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "condition", "n"] + [f"corr_{d.lower()}" for d in DIMENSIONS])
        for (scen, cond) in sorted(groups):
            rs = groups[(scen, cond)]
            row = [scen, cond, len(rs)]
            for d in DIMENSIONS:
                row.append(mean([r[f"corr_{d.lower()}"] for r in rs
                                 if r[f"corr_{d.lower()}"] != ""]))
            w.writerow(row)

    # ---- T_attribution.csv ----
    attr: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in per_unit:
        for dec, n in r["decisions"].items():
            attr[(r["scenario"], r["condition"])][dec] += n
    all_decs = sorted({d for v in attr.values() for d in v})
    with open(run_dir / "T_attribution.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "condition"] + all_decs)
        for (scen, cond) in sorted(attr):
            w.writerow([scen, cond] + [attr[(scen, cond)].get(d, 0) for d in all_decs])

    # ---- T4 elicitation summary (pass-through aggregate) ----
    elic_path = run_dir / "elicitation.csv"
    if elic_path.exists():
        rows = list(csv.DictReader(open(elic_path, newline="", encoding="utf-8")))
        by_scen: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_scen[r["scenario"]].append(r)
        with open(run_dir / "T4_elicitation.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["scenario", "n_sessions", "clarifying_mean",
                        "resources_mean", "parameters_mean", "total_mean"])
            for scen in sorted(by_scen):
                rs = by_scen[scen]
                w.writerow([scen, len(rs),
                            mean([float(r["clarifying"]) for r in rs]),
                            mean([float(r["configuring_resources"]) for r in rs]),
                            mean([float(r["configuring_parameters"]) for r in rs]),
                            mean([float(r["total_elicited"]) for r in rs])])

    # ---- console ----
    total_unscored = sum(r["n_unscored"] for r in per_unit)
    unmarked = sum(n for r in per_unit for d, n in r["decisions"].items() if d in ("", "unmarked"))
    print(f"Aggregated {len(per_unit)} ASPECs -> {run_dir.relative_to(PROJECT_ROOT)}")
    print("  Wrote: results_per_unit.csv, T2_output_quality.csv,")
    print("         T3_correctness_by_dimension.csv, T_attribution.csv, T4_elicitation.csv")
    if total_unscored:
        print(f"\n  PROVISIONAL: {total_unscored} Semantic/Adapter-valid 'differ' items "
              f"still need `points` in the worksheet.")
    if unmarked:
        print(f"  {unmarked} worksheet rows still have an empty `decision` "
              f"(attribution incomplete).")
    if not total_unscored and not unmarked:
        print("\n  Worksheet complete: correctness figures are final.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate scored ASPECs into Results tables.")
    ap.add_argument("--run", type=str, default=None,
                    help="Score run dir (default: latest under scoring/outputs).")
    ap.add_argument("--rubric", type=str, default=str(DEFAULT_RUBRIC))
    args = ap.parse_args()
    run_dir = Path(args.run).resolve() if args.run else latest_run()
    if not run_dir or not (run_dir / "scores.csv").exists():
        raise SystemExit("No score run found. Run score.py (and make_worksheet.py) first.")
    run(run_dir, Path(args.rubric).resolve())


if __name__ == "__main__":
    main()
