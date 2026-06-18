#!/usr/bin/env python3
"""
Attribution-worksheet generator (Phase 2 enabler).

Turns the scorer's `divergences.json` into a worksheet with one row per flagged
divergence, pre-filled with:
  - the pre-registered scoring mode (Exact / Semantic / Adapter-valid) parsed
    from the specification-correctness rubric;
  - the relevant interaction-log excerpt (pipeline units only), so the source of
    a divergence can be judged from what the participant actually said;

and three BLANK columns for the manual pass to fill:
  - decision  : pipeline -> match | system_error | participant_divergence
                baseline -> match | model_error | adapter_valid_ok
  - points    : 0 / 1 (per the scoring mode)
  - notes

Matched (non-divergent) scorable items are NOT listed: they are awarded 1
automatically. The worksheet only contains the items that need judgment. A
per-unit header reports how many classified parameters matched automatically so
the denominator is known.

The scorer flags divergences; this script attaches evidence and mode; the human
decides source and points. No step folds a judgment call into a number.

Usage:
    python scoring/make_worksheet.py                 # uses latest score run
    python scoring/make_worksheet.py --run scoring/outputs/score_XXXX
    python scoring/make_worksheet.py --rubric /path/to/rubric.md
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

SCORING_DIR = Path(__file__).resolve().parent
PIPELINE_SRC = SCORING_DIR.parent
PROJECT_ROOT = PIPELINE_SRC.parent          # Thesis/pipeline
THESIS_DIR = PROJECT_ROOT.parent            # Thesis
OUTPUTS_DIR = SCORING_DIR / "outputs"
INTERACTION_LOG_DIR = PIPELINE_SRC / "scenarios" / "interaction-logs"
DEFAULT_RUBRIC = (THESIS_DIR / "Report" / "Method & Results" / "Experiment"
                  / "specification-correctness-rubric.md")

UNIT_PIPE_RE = re.compile(r"^P(\d+)-S(\d+)$")
VALID_MODES = {"Exact", "Semantic", "Adapter-valid"}


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def compact(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------- #
# Rubric parsing -> {scenario: {param_key: mode}}
# --------------------------------------------------------------------------- #
def parse_rubric(path: Path) -> dict[int, dict[str, str]]:
    classification: dict[int, dict[str, str]] = {}
    if not path.exists():
        return classification
    current: int | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        header = re.match(r"^###\s+Scenario\s+(\d+)", line)
        if header:
            current = int(header.group(1))
            classification[current] = {}
            continue
        if current is None or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        param = cells[1].strip("` ").strip()
        mode = cells[2].strip()
        if mode in VALID_MODES and param:
            classification[current][param] = mode
    return classification


def lookup_mode(classification: dict[str, str], element: str) -> str:
    if element in classification:
        return classification[element]
    # nested: rubric may classify `filters.sender` while diff reports `filters`
    children = {m for k, m in classification.items() if k.startswith(element + ".")}
    if len(children) == 1:
        return next(iter(children))
    if children:
        return "Mixed (see rubric)"
    # diff reports a nested key the rubric classifies under its parent
    if "." in element and element.split(".")[0] in classification:
        return classification[element.split(".")[0]]
    return "(unclassified)"


# --------------------------------------------------------------------------- #
# Interaction-log evidence index
# --------------------------------------------------------------------------- #
def load_log_index(participant: int, scenario: int) -> dict | None:
    matches = list(INTERACTION_LOG_DIR.glob(f"P{participant}-scenario-{scenario}_*.json"))
    if not matches:
        return None
    log = load_json(sorted(matches)[0])
    by_param: dict[str, list[str]] = {}
    by_stage: dict[str, list[str]] = {}
    for it in log.get("interactions", []):
        stage = it.get("stage", "?")
        q = (it.get("question") or "").strip()
        a = (it.get("answer") or "").strip()
        snippet = f'[{stage}] Q: "{q}" -> A: "{a}"'
        by_stage.setdefault(stage, []).append(snippet)
        pset = it.get("parameter_set")
        if isinstance(pset, dict) and pset.get("param_name"):
            by_param.setdefault(pset["param_name"], []).append(snippet)
    return {"by_param": by_param, "by_stage": by_stage}


def evidence_for(index: dict | None, *, location: str, element: str) -> str:
    if index is None:
        return "(single-shot; no dialogue)"
    if location == "credentials":
        return "(auto-resolved from vault by auth_type; not user-set)"
    if location == "resources":
        hits = index["by_stage"].get("configuring_resources", [])
        return " || ".join(hits) if hits else "(no resource-selection turn logged)"
    if element in ("adapter_id",):
        hits = index["by_stage"].get("clarifying", [])
        return "(adapter derived from description) " + " || ".join(hits[:2])
    # parameter element: match by param_name, else by leading segment
    hits = index["by_param"].get(element) or index["by_param"].get(element.split(".")[0])
    if hits:
        return " || ".join(hits)
    return "(not user-set; default/inferred/system origin)"


# --------------------------------------------------------------------------- #
# Enumerate divergence rows from one unit's diff
# --------------------------------------------------------------------------- #
def rows_for_unit(entry: dict, classification: dict[str, str], index: dict | None) -> list[dict]:
    d = entry["divergences"]
    rows: list[dict] = []

    def add(location, element, status, gt, cand, *, mode=None):
        rows.append({
            "location": location, "element": element, "status": status,
            "scoring_mode": mode or lookup_mode(classification, element),
            "ground_truth": compact(gt), "candidate": compact(cand),
            "log_evidence": evidence_for(index, location=location, element=element),
        })

    # Trigger
    t = d["trigger"]
    if not t["adapter_match"]:
        add("trigger", "adapter_id", "adapter_mismatch",
            t["adapter_gt"], t["adapter_candidate"], mode="Exact (adapter_id)")
    for pd in t["param_diffs"]:
        add("trigger", pd["param"], pd["status"], pd["gt"], pd["candidate"])

    # Steps
    for i, s in enumerate(d["steps"]):
        loc = f"step[{i}] {s.get('adapter_candidate') or s.get('adapter_gt')}"
        if s.get("status"):
            add(loc, "adapter_id", s["status"],
                s.get("adapter_gt"), s.get("adapter_candidate"), mode="Exact (adapter_id)")
            continue
        if s.get("adapter_gt") != s.get("adapter_candidate"):
            add(loc, "adapter_id", "adapter_mismatch",
                s.get("adapter_gt"), s.get("adapter_candidate"), mode="Exact (adapter_id)")
        for pd in s.get("param_diffs", []):
            add(loc, pd["param"], pd["status"], pd["gt"], pd["candidate"])

    # Credentials (Dimension 3, always Exact)
    c = d["credentials"]
    if not c["match"]:
        add("credentials", "auth_type_set", "set_mismatch", c["gt"], c["candidate"],
            mode="Exact (Dim 3)")
        rows[-1]["log_evidence"] += f"  [missing={c['missing']} extra={c['extra']}]"

    # Resources (Dimension 4, always Exact)
    r = d["resources"]
    if not r["match"]:
        add("resources", "resource_ids", "set_mismatch",
            {"ids": r["gt_ids"], "names": r["gt_names"]},
            {"ids": r["candidate_ids"], "names": r["candidate_names"]},
            mode="Exact (Dim 4)")
        rows[-1]["log_evidence"] += f"  [missing={r['missing']} extra={r['extra']}]"

    return rows


def diverged_classified_params(rows: list[dict], classification: dict[str, str]) -> int:
    seen = set()
    for row in rows:
        el = row["element"]
        if el in classification or el.split(".")[0] in classification:
            seen.add(el)
    return len(seen)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def latest_run() -> Path | None:
    runs = sorted(p for p in OUTPUTS_DIR.glob("score_*") if p.is_dir())
    return runs[-1] if runs else None


def run(run_dir: Path, rubric_path: Path) -> None:
    divergences = load_json(run_dir / "divergences.json")
    classification = parse_rubric(rubric_path)
    if not classification:
        print(f"WARNING: rubric not parsed from {rubric_path}; scoring_mode left blank.\n")

    divergences.sort(key=lambda e: (e["scenario"], e["condition"], e["unit_id"]))

    csv_rows: list[dict] = []
    md_lines: list[str] = [
        "# Specification-correctness attribution worksheet",
        "",
        "One row per flagged divergence. Fill **decision**, **points**, **notes**.",
        "",
        "- pipeline decision: `match` | `system_error` | `participant_divergence`",
        "- baseline decision: `match` | `model_error` | `adapter_valid_ok`",
        "- points: `1` if awarded per the scoring mode, else `0`",
        "  (Adapter-valid: 1 if the value is catalogue-valid, regardless of GT;"
        " Exact: 1 only on exact match; Semantic: 1 if semantically equivalent)",
        "",
    ]

    for entry in divergences:
        scen = entry["scenario"]
        unit = entry["unit_id"]
        cls = classification.get(scen, {})
        index = None
        m = UNIT_PIPE_RE.match(unit)
        if m:
            index = load_log_index(int(m.group(1)), int(m.group(2)))

        rows = rows_for_unit(entry, cls, index)
        total_cls = len(cls)
        diverged_cls = diverged_classified_params(rows, cls)
        matched_cls = max(total_cls - diverged_cls, 0)

        md_lines.append(f"## {unit}  (scenario {scen}, {entry['condition']})")
        md_lines.append(
            f"_classified params: {total_cls} | auto-matched (awarded 1): "
            f"{matched_cls} | to judge below: {len(rows)}_")
        md_lines.append("")
        if not rows:
            md_lines.append("_No divergences flagged._\n")
        else:
            md_lines.append("| location | element | status | mode | ground_truth | "
                            "candidate | evidence | decision | pts |")
            md_lines.append("|---|---|---|---|---|---|---|---|---|")
        for row in rows:
            csv_rows.append({"scenario": scen, "condition": entry["condition"],
                             "unit_id": unit, **row,
                             "decision": "", "points": "", "notes": ""})
            ev = row["log_evidence"]
            ev_short = (ev[:90] + "…") if len(ev) > 90 else ev
            md_lines.append(
                f"| {row['location']} | `{row['element']}` | {row['status']} | "
                f"{row['scoring_mode']} | {row['ground_truth'][:40]} | "
                f"{row['candidate'][:40]} | {ev_short} |  |  |")
        md_lines.append("")

    # CSV (canonical fill-in)
    csv_path = run_dir / "attribution_worksheet.csv"
    fields = ["scenario", "condition", "unit_id", "location", "element", "status",
              "scoring_mode", "ground_truth", "candidate", "log_evidence",
              "decision", "points", "notes"]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(csv_rows)

    md_path = run_dir / "attribution_worksheet.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    n_pipe = sum(1 for r in csv_rows if r["condition"] == "pipeline")
    n_base = sum(1 for r in csv_rows if r["condition"] == "baseline")
    print(f"Worksheet written for run: {run_dir.relative_to(PROJECT_ROOT)}")
    print(f"  rows to judge: {len(csv_rows)}  (pipeline {n_pipe}, baseline {n_base})")
    print(f"  rubric scenarios parsed: {sorted(classification)}")
    print(f"  -> {csv_path.name}  (fill decision/points/notes)")
    print(f"  -> {md_path.name}   (readable in Obsidian)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the attribution worksheet.")
    ap.add_argument("--run", type=str, default=None,
                    help="Score run dir (default: latest under scoring/outputs).")
    ap.add_argument("--rubric", type=str, default=str(DEFAULT_RUBRIC),
                    help="Path to the specification-correctness rubric markdown.")
    args = ap.parse_args()
    run_dir = Path(args.run).resolve() if args.run else latest_run()
    if not run_dir or not (run_dir / "divergences.json").exists():
        raise SystemExit("No score run with divergences.json found. Run score.py first.")
    run(run_dir, Path(args.rubric).resolve())


if __name__ == "__main__":
    main()