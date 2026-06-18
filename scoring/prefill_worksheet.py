#!/usr/bin/env python3
"""
Pre-fill ONLY the mechanical, rule-determined cells of the attribution worksheet.

This applies NO judgment. It fills exactly the cells whose value follows from a
rule, and leaves every genuine judgment (semantic equivalence, source
attribution for the pipeline) blank for the researcher. Every cell it writes is
marked in `notes` with the rule that produced it, so the manual vs. automatic
split stays auditable.

Rules applied:
  POINTS
    - Exact mode + any mismatch (differ / set_mismatch / missing / adapter_mismatch)
        -> 0   (rubric: exact match required, no partial credit)
    - Adapter-valid mode + differ
        -> 1 if the candidate value is valid against the catalogue parameter
             definition (enum membership / boolean), else 0
    - Semantic mode -> LEFT BLANK (researcher judges equivalence)
  DECISION
    - baseline + Exact mismatch        -> model_error   (no user; exact value wrong)
    - baseline + Adapter-valid valid   -> adapter_valid_ok
    - baseline + Adapter-valid invalid -> model_error
    - baseline + Semantic              -> LEFT BLANK (researcher judges)
    - pipeline (any)                   -> LEFT BLANK (needs the interaction log)

Run AFTER make_worksheet.py and BEFORE filling the rest by hand.
    python scoring/prefill_worksheet.py            # latest run
    python scoring/prefill_worksheet.py --run scoring/outputs/score_XXXX
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

SCORING_DIR = Path(__file__).resolve().parent
PIPELINE_SRC = SCORING_DIR.parent
OUTPUTS_DIR = SCORING_DIR / "outputs"
CATALOGUE_PATH = PIPELINE_SRC / "action_catalogue.json"


def latest_run() -> Path | None:
    runs = sorted(p for p in OUTPUTS_DIR.glob("score_*") if p.is_dir())
    return runs[-1] if runs else None


def build_enum_map(catalogue_path: Path) -> dict[str, dict]:
    """param_name -> {'type':..., 'options':[...]} (first definition wins)."""
    cat = json.load(open(catalogue_path, encoding="utf-8"))
    out: dict[str, dict] = {}

    def walk(o):
        if isinstance(o, dict):
            params = o.get("parameters")
            if isinstance(params, dict):
                for pn, pd in params.items():
                    if isinstance(pd, dict) and pn not in out:
                        out[pn] = {"type": pd.get("type"),
                                   "options": pd.get("options") or pd.get("enum") or []}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(cat)
    return out


def adapter_valid(element: str, candidate: str, enum_map: dict) -> bool | None:
    """True/False if determinable from the catalogue, else None."""
    spec = enum_map.get(element) or enum_map.get(element.split(".")[0])
    if not spec:
        return None
    val = (candidate or "").strip().strip('"')
    if spec["type"] == "enum" and spec["options"]:
        return val in spec["options"]
    if spec["type"] == "boolean":
        return val.lower() in ("true", "false")
    return None


def note(row: dict, msg: str) -> None:
    existing = row.get("notes", "").strip()
    row["notes"] = f"{existing} | {msg}".strip(" |") if existing else msg


def run(run_dir: Path) -> None:
    ws_path = run_dir / "attribution_worksheet.csv"
    if not ws_path.exists():
        raise SystemExit(f"No worksheet at {ws_path}. Run make_worksheet.py first.")
    enum_map = build_enum_map(CATALOGUE_PATH)

    with open(ws_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        rows = list(reader)

    n_points = n_dec = 0
    blank_points: list[str] = []
    blank_decision: list[str] = []

    for r in rows:
        base_mode = r["scoring_mode"].split(" ")[0]      # Exact / Semantic / Adapter-valid
        status = r["status"]
        cond = r["condition"]
        is_mismatch = status in ("differ", "set_mismatch", "missing_in_candidate",
                                 "adapter_mismatch")
        valid: bool | None = None

        # --- POINTS ---
        if not r.get("points", "").strip():
            if base_mode == "Exact" and is_mismatch:
                r["points"] = "0"; note(r, "auto: exact mismatch = 0"); n_points += 1
            elif base_mode == "Adapter-valid" and status == "differ":
                valid = adapter_valid(r["element"], r["candidate"], enum_map)
                if valid is True:
                    r["points"] = "1"; note(r, "auto: catalogue-valid"); n_points += 1
                elif valid is False:
                    r["points"] = "0"; note(r, "auto: not catalogue-valid"); n_points += 1
                else:
                    blank_points.append(r["unit_id"] + "/" + r["element"])
            else:  # Semantic
                blank_points.append(r["unit_id"] + "/" + r["element"])

        # --- DECISION (baseline only is mechanical) ---
        if not r.get("decision", "").strip():
            if cond == "baseline":
                if base_mode == "Exact" and is_mismatch:
                    r["decision"] = "model_error"
                    note(r, "auto: baseline exact mismatch"); n_dec += 1
                elif base_mode == "Adapter-valid" and status == "differ":
                    if valid is None:
                        valid = adapter_valid(r["element"], r["candidate"], enum_map)
                    if valid is True:
                        r["decision"] = "adapter_valid_ok"; n_dec += 1
                    elif valid is False:
                        r["decision"] = "model_error"; n_dec += 1
                    else:
                        blank_decision.append(r["unit_id"] + "/" + r["element"])
                else:  # baseline Semantic
                    blank_decision.append(r["unit_id"] + "/" + r["element"])
            else:  # pipeline -> always researcher judgment
                blank_decision.append(r["unit_id"] + "/" + r["element"])

    with open(ws_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"Pre-filled (mechanical only): {ws_path.name}")
    print(f"  points  filled: {n_points}   |  still blank (judgment): {len(blank_points)}")
    print(f"  decision filled: {n_dec}   |  still blank (judgment): {len(blank_decision)}")
    print(f"\n  Researcher still to score (points): {len(blank_points)} Semantic items")
    print(f"  Researcher still to attribute (decision): {len(blank_decision)} items "
          f"(all pipeline rows + baseline Semantic)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=str, default=None)
    args = ap.parse_args()
    run_dir = Path(args.run).resolve() if args.run else latest_run()
    if not run_dir:
        raise SystemExit("No score run found.")
    run(run_dir)


if __name__ == "__main__":
    main()