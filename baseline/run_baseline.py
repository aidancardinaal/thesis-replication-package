"""
Single-shot baseline for the ASPEC evaluation.

This is the baseline condition described in the thesis evaluation design. For each
scenario, the same language model used by the conversational pipeline is prompted
*once* with:

  - the scenario card (the same task information the participant received),
  - the complete action catalogue, and
  - the full ASPEC JSON Schema,

and is asked to return a single ASPEC JSON object. There is no dialogue, no
clarification, and no decomposition into stages. The prompt is deliberately
minimal (no few-shot examples, no field-by-field guidance), so the result is a
conservative lower bound on what single-shot generation can achieve.

Because the schema and catalogue are identical to the pipeline condition, a
difference in output quality reflects the interaction mechanism (iterative
dialogue vs. a single prompt) rather than differences in available actions or
structural constraints. See baseline/README.md for the full rationale and the
known limitations of this comparison.

Design choices that matter for validity:
  - The model is generated *freely* (not schema-constrained). Schema validity is
    one of the measures, so forcing schema-conformant decoding would make that
    measure trivially 100%. The output is parsed and validated after the fact.
  - Several samples are drawn per scenario (default 3) so the comparison is not
    made against a single, possibly lucky or unlucky, draw. At temperature 0
    (the pipeline default) variance is expected to be small; raise --temperature
    to characterise sampling variability.

Scoring (schema validity, field completeness, specification correctness) is NOT
done here. The rubric is applied identically to baseline and participant outputs
by the shared scoring process; this script only produces the baseline ASPECs and
records schema validity as a generation-time sanity check.

Usage (run from the pipeline source dir, with the project .env loaded):

    python baseline/run_baseline.py                 # all scenarios, 3 samples, temp 0
    python baseline/run_baseline.py --scenarios 1 3 # only scenarios 1 and 3
    python baseline/run_baseline.py --samples 5 --temperature 0.7
    python baseline/run_baseline.py --dry-run       # assemble prompts, no API calls
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

# Resolve paths relative to this file so the script runs from anywhere.
BASELINE_DIR = Path(__file__).resolve().parent
PIPELINE_SRC = BASELINE_DIR.parent          # thesis-new-pipeline/
PROJECT_ROOT = PIPELINE_SRC.parent          # pipeline/ (holds .env)

SCHEMA_PATH = PIPELINE_SRC / "aspec.schema.json"
CATALOGUE_PATH = PIPELINE_SRC / "action_catalogue.json"
USERVAULT_PATH = PIPELINE_SRC / "uservault.json"
CARDS_PATH = BASELINE_DIR / "scenario_cards.json"
GROUND_TRUTH_DIR = PIPELINE_SRC / "scenarios" / "ground-truths"
DEFAULT_OUT_DIR = BASELINE_DIR / "outputs"

# Same Azure deployment the pipeline uses (see ajora_automation_reasoning_agent_v4.get_llm).
DEFAULT_MODEL = "gpt-5.4-mini"

# Make the pipeline's call-safety helper importable.
sys.path.insert(0, str(PIPELINE_SRC))
from llm_safety import safe_invoke, ContentPolicyError  # noqa: E402

SYSTEM_PROMPT = (
    "You are a workflow-automation specification generator. Given a description of a "
    "desired automation, you output a single ASPEC JSON object that conforms to the "
    "provided ASPEC JSON Schema and that uses only the triggers, actions, and adapters "
    "defined in the provided action catalogue. For credential and resource fields, use the "
    "entries in the provided user vault (the credentials and resources the user has "
    "connected), referencing their identifiers rather than inventing them. Respond with the "
    "JSON object only, with no surrounding text, explanation, or code fences."
)


# ── IO helpers ──────────────────────────────────────────────────────────────

def load_json(path: Path) -> Any:
    with open(path, "r") as f:
        return json.load(f)


def load_env() -> None:
    """Load the project .env (same file the pipeline uses). No-op if unavailable."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / ".env")


def build_human_prompt(card: str, schema: dict, catalogue: dict, vault: dict) -> str:
    return (
        f"{card}\n\n"
        f"=== ASPEC JSON SCHEMA ===\n{json.dumps(schema, indent=2)}\n\n"
        f"=== ACTION CATALOGUE ===\n{json.dumps(catalogue, indent=2)}\n\n"
        f"=== USER VAULT (connected credentials and resources) ===\n"
        f"{json.dumps(vault, indent=2)}\n"
    )


# ── Output parsing & validation ─────────────────────────────────────────────

def extract_json(text: str) -> Optional[dict]:
    """Best-effort extraction of a single JSON object from a model response."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        t = t.strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(t[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def validate_aspec(aspec: dict, schema: dict) -> tuple[bool, Optional[str]]:
    """Validate against the ASPEC schema. Returns (is_valid, first_errors_or_None)."""
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(aspec), key=lambda e: list(e.path))
    if not errors:
        return True, None
    summary = "; ".join(
        f"{list(e.path) or '<root>'}: {e.message}" for e in errors[:5]
    )
    if len(errors) > 5:
        summary += f"  (+{len(errors) - 5} more)"
    return False, summary


# ── Generation ──────────────────────────────────────────────────────────────

def build_llm(model: str, temperature: float):
    from langchain_openai import AzureChatOpenAI

    api_key = os.getenv("AZURE_API_KEY")
    if not api_key:
        raise SystemExit(
            "AZURE_API_KEY not set. Ensure the project .env (pipeline/.env) is present "
            "and contains AZURE_API_KEY, AZURE_ENDPOINT, and optionally AZURE_API_VERSION."
        )
    return AzureChatOpenAI(
        azure_deployment=model,
        azure_endpoint=os.getenv("AZURE_ENDPOINT"),
        api_key=api_key,
        api_version=os.getenv("AZURE_API_VERSION", "2025-01-01-preview"),
        temperature=temperature,
    )


def generate_once(llm, system_prompt: str, human_prompt: str) -> str:
    from langchain_core.messages import SystemMessage, HumanMessage

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
    response = safe_invoke(llm, messages)
    return getattr(response, "content", str(response))


# ── Run ─────────────────────────────────────────────────────────────────────

def run(scenarios: list[int], samples: int, temperature: float, model: str,
        out_dir: Path, dry_run: bool) -> None:
    schema = load_json(SCHEMA_PATH)
    catalogue = load_json(CATALOGUE_PATH)
    vault = load_json(USERVAULT_PATH)
    cards = {c["scenario_id"]: c for c in load_json(CARDS_PATH)["cards"]}

    missing = [s for s in scenarios if s not in cards]
    if missing:
        raise SystemExit(f"No scenario card defined for: {missing}")

    run_stamp = _utc_now().strftime("%Y-%m-%dT%H-%M-%SZ")
    run_dir = out_dir / (f"dryrun_{run_stamp}" if dry_run else f"run_{run_stamp}")
    run_dir.mkdir(parents=True, exist_ok=True)

    llm = None if dry_run else build_llm(model, temperature)

    results: list[dict] = []
    print(f"\nBaseline run -> {run_dir}")
    print(f"model={model} temperature={temperature} samples={samples} "
          f"scenarios={scenarios} dry_run={dry_run}\n")

    for sid in scenarios:
        card = cards[sid]
        human_prompt = build_human_prompt(card["card"], schema, catalogue, vault)
        scen_dir = run_dir / f"scenario-{sid}"
        scen_dir.mkdir(exist_ok=True)
        (scen_dir / "prompt.txt").write_text(
            f"=== SYSTEM ===\n{SYSTEM_PROMPT}\n\n=== HUMAN ===\n{human_prompt}"
        )

        print(f"Scenario {sid} ({card['title']}):")
        for i in range(1, samples + 1):
            row = {
                "scenario_id": sid,
                "title": card["title"],
                "sample": i,
                "timestamp": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "model": model,
                "temperature": temperature,
                "parse_ok": False,
                "schema_valid": False,
                "schema_error": "",
                "trigger_adapter": "",
                "n_steps": "",
                "output_file": "",
                "error": "",
            }

            if dry_run:
                row["error"] = "dry-run (no API call)"
                results.append(row)
                print(f"  sample {i:02d}: dry-run, prompt assembled")
                continue

            try:
                raw = generate_once(llm, SYSTEM_PROMPT, human_prompt)
            except ContentPolicyError as exc:
                row["error"] = f"content_policy: {exc}"
                results.append(row)
                print(f"  sample {i:02d}: BLOCKED by content filter")
                continue
            except Exception as exc:  # noqa: BLE001 - record and continue the batch
                row["error"] = f"{type(exc).__name__}: {exc}"
                results.append(row)
                print(f"  sample {i:02d}: ERROR {type(exc).__name__}")
                continue

            (scen_dir / f"sample-{i:02d}.raw.txt").write_text(raw)
            aspec = extract_json(raw)
            if aspec is None:
                row["error"] = "json_parse_failed"
                results.append(row)
                print(f"  sample {i:02d}: parse FAILED (not valid JSON)")
                continue

            row["parse_ok"] = True
            out_file = scen_dir / f"sample-{i:02d}.json"
            out_file.write_text(json.dumps(aspec, indent=2))
            row["output_file"] = str(out_file.relative_to(run_dir))
            row["trigger_adapter"] = (aspec.get("trigger") or {}).get("adapter_id", "")
            steps = aspec.get("steps")
            row["n_steps"] = len(steps) if isinstance(steps, list) else ""

            valid, err = validate_aspec(aspec, schema)
            row["schema_valid"] = valid
            row["schema_error"] = err or ""
            results.append(row)
            print(f"  sample {i:02d}: parsed, schema_valid={valid}"
                  + (f"  [{err}]" if not valid else ""))
        print()

    # Persist run artifacts.
    manifest = {
        "run_stamp": run_stamp,
        "dry_run": dry_run,
        "model": model,
        "temperature": temperature,
        "samples_per_scenario": samples,
        "scenarios": scenarios,
        "schema_path": str(SCHEMA_PATH.relative_to(PROJECT_ROOT)),
        "catalogue_path": str(CATALOGUE_PATH.relative_to(PROJECT_ROOT)),
        "uservault_path": str(USERVAULT_PATH.relative_to(PROJECT_ROOT)),
        "cards_path": str(CARDS_PATH.relative_to(PROJECT_ROOT)),
        "system_prompt": SYSTEM_PROMPT,
        "results": results,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    csv_path = run_dir / "results.csv"
    fields = ["scenario_id", "title", "sample", "timestamp", "model", "temperature",
              "parse_ok", "schema_valid", "schema_error", "trigger_adapter", "n_steps",
              "output_file", "error"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    _print_summary(results, dry_run)
    print(f"Artifacts: {run_dir}\n  manifest.json\n  results.csv")


def _print_summary(results: list[dict], dry_run: bool) -> None:
    print("=" * 56)
    print("Summary")
    print("=" * 56)
    if dry_run:
        print(f"{len(results)} prompts assembled (no generation).")
        return
    by_scenario: dict[int, list[dict]] = {}
    for r in results:
        by_scenario.setdefault(r["scenario_id"], []).append(r)
    for sid in sorted(by_scenario):
        rows = by_scenario[sid]
        valid = sum(1 for r in rows if r["schema_valid"])
        parsed = sum(1 for r in rows if r["parse_ok"])
        print(f"  Scenario {sid}: {valid}/{len(rows)} schema-valid, "
              f"{parsed}/{len(rows)} parsed")
    total = len(results)
    total_valid = sum(1 for r in results if r["schema_valid"])
    print(f"  TOTAL: {total_valid}/{total} schema-valid")
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Single-shot ASPEC baseline generator.")
    p.add_argument("--scenarios", type=int, nargs="+", default=[1, 2, 3, 4],
                   help="Scenario IDs to run (default: 1 2 3 4).")
    p.add_argument("--samples", type=int, default=3,
                   help="Single-shot samples per scenario (default: 3).")
    p.add_argument("--temperature", type=float, default=0.0,
                   help="Sampling temperature (default: 0.0, matching the pipeline).")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Azure deployment name (default: {DEFAULT_MODEL}).")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR,
                   help="Output directory (default: baseline/outputs).")
    p.add_argument("--dry-run", action="store_true",
                   help="Assemble and save prompts without calling the API.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.dry_run:
        load_env()
    run(
        scenarios=args.scenarios,
        samples=args.samples,
        temperature=args.temperature,
        model=args.model,
        out_dir=args.out,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()