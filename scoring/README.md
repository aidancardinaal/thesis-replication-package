# Shared scorer

`score.py` applies one deterministic scoring procedure to **every** generated
ASPEC — baseline (single-shot) and pipeline (participant) alike — so the
head-to-head is fair. It does the mechanical, error-prone work and leaves the
judgment calls (specification correctness, divergence attribution) to a
documented manual pass.

It scores the 12 baseline samples (latest run under `../baseline/outputs/`) and
the 8 participant ASPECs (`../scenarios/user-aspecs/`, excluding `old/`) against
the four ground truths (`../scenarios/ground-truths/`).

## Run

```bash
# from thesis-new-pipeline/  (or anywhere; paths self-resolve)
../venv/bin/python scoring/score.py          # venv has jsonschema -> schema validity populated
python3 scoring/score.py                      # stdlib only -> validity recorded as "n/a"
python3 scoring/score.py --baseline-run baseline/outputs/run_XXXX
```

Schema validity needs `jsonschema`; everything else is standard-library only, so
the scorer always runs even without the venv.

## Outputs (timestamped dir under `outputs/`, gitignored)

| File | Feeds | Contents |
|---|---|---|
| `scores.csv` | T2, structural T3 | one row per ASPEC: schema validity, completeness, exact-match dimensions, parameter-divergence count |
| `divergences.json` | T_attr (manual pass) | full element-level diff of each ASPEC vs ground truth |
| `elicitation.csv` | T4 | per-session question counts by stage (pipeline only) |
| `per-output/<unit>.json` | audit | the complete scored record for one ASPEC |

## Workflow (three scripts, in order)

```
score.py          -> scores.csv, divergences.json, elicitation.csv, per-output/
make_worksheet.py -> attribution_worksheet.csv  (you fill points + decision)
aggregate.py      -> results_per_unit.csv, T2/T3/T_attribution/T4 tables
```

1. **`score.py`** — deterministic measures + divergence flags (no judgment).
2. **`make_worksheet.py`** — turns the flags into a worksheet; you mark `points`
   and `decision` by hand using the log evidence.
3. **`aggregate.py`** — reads the filled worksheet + the deterministic scores and
   produces the Results numbers. It computes most of specification correctness
   deterministically and needs the worksheet only for Semantic / Adapter-valid
   `differ` items and for the source attribution. If the worksheet is still
   incomplete it prints exactly how many `points` / `decision` cells remain and
   marks the affected correctness figures PROVISIONAL.

`aggregate.py` reports two correctness figures: `correctness` (the pre-registered
headline; an Exact parameter the participant changed scores 0) and
`correctness_sys` (secondary, pipeline only; excludes `participant_divergence`
items). The denominator (scorable items per ASPEC) is documented in the script
header.

## What each measure means

**Schema validity** — conformance to `aspec.schema.json` (Draft 2020-12).
Recorded as `valid` / `INVALID` / `n/a`.

**Field completeness** — structural presence against the scenario ground truth,
matching the thesis Measures definition. Expected slots = 1 trigger + one per
ground-truth step + one per required credential + one per required resource.
Score = proportion of those slots present and non-empty in the candidate
(candidate counts are capped at the expected count so over-production cannot push
the score above 1).

**Exact-match dimensions (T/S/C/R)** — for each rubric dimension, whether the
candidate matches the ground truth *exactly*: trigger adapter + parameters, every
step adapter + parameters, the set of credential `auth_type`s, the set of
resource `id`s. This is only the **exact-match** scoring mode. The
**semantic-match** and **adapter-valid** modes (free-text fields, implementation
choices) require the manual rubric pass — the scorer does not attempt them.

**Parameter divergences (`pdiv`)** — count of flagged parameter differences vs
ground truth (`differ` / `missing_in_candidate` / `extra_in_candidate`). These
are *flags only*. The scorer never decides whether a divergence is a **system
error** or a **participant choice** — see below.

**Elicitation counts** — number of resolved question–answer exchanges per session
from the interaction logs, split into `clarifying` / `configuring_resources` /
`configuring_parameters`. Note: within-turn reprompts on vague answers were not
logged, so this is resolved exchanges, not raw message turns.

## The correctness workflow (two steps, on purpose)

1. **Deterministic (this script):** flag every divergence from ground truth.
2. **Manual attribution pass (separate, recorded):** for each flagged divergence,
   classify the source using the interaction log as evidence —
   - **match** (no real divergence),
   - **system error** (the participant expressed X, the system produced not-X),
   - **participant divergence** (the system faithfully produced what the
     participant expressed, which differs from the canonical scenario).

   Only system errors count against specification correctness. Participant
   divergences are reported separately as input variance. Implementation-choice
   parameters are scored by adapter-validity, not exact match, per the
   pre-registered rubric.

Keeping the source attribution out of the script is deliberate: it cannot be done
mechanically (it needs the dialogue), and folding it in would hide a judgment
call inside a number.

## Attribution worksheet (`make_worksheet.py`)

Turns `divergences.json` into the manual pass's input:

```bash
python scoring/make_worksheet.py                 # uses the latest score run
python scoring/make_worksheet.py --run scoring/outputs/score_XXXX
```

It writes `attribution_worksheet.csv` (canonical fill-in) and
`attribution_worksheet.md` (readable in Obsidian) into the score run dir, with
one row per flagged divergence, pre-filled with:

- **scoring_mode** — parsed from the pre-registered rubric's per-scenario
  parameter classification (Exact / Semantic / Adapter-valid).
- **log_evidence** — the matching interaction-log question/answer (pipeline
  units), or "(single-shot; no dialogue)" for baseline.

and three blank columns to fill:

- **points** — the pre-registered rubric award (Exact: 1 only on exact match;
  Adapter-valid: 1 if catalogue-valid regardless of GT; Semantic: 1 if
  equivalent). This produces the **headline** correctness score.
- **decision** — the separate, qualitative source attribution
  (pipeline: `match` / `system_error` / `participant_divergence`;
  baseline: `match` / `model_error` / `adapter_valid_ok`).
- **notes**.

`points` and `decision` are independent on purpose. An Exact-mode parameter the
participant deliberately changed (e.g. they said "Sheet 1" where the ground truth
has "Sheet1") scores `points=0` under the pre-registered rubric **and**
`decision=participant_divergence`. The headline rubric score is not adjusted; the
attribution is reported separately as error analysis, so the pipeline is not
silently credited or penalised — both numbers are shown.

Matched (non-divergent) scorable items are not listed; they are awarded 1
automatically. Each unit's header reports how many classified parameters matched.

## Design choices that matter for validity

- **Match by value, not by key.** The `credentials`/`resources` dict keys are
  arbitrary and differ across outputs (`gmail_cred` vs `gmailOAuth2` vs `gmail`),
  so credentials are matched by `auth_type` and resources by `id`/`name`.
- **Steps are paired by `adapter_id`** (order-preserving for duplicates) before
  parameters are compared.
- **Connections are excluded** — they are deterministic from step order, per the
  rubric, and carry no independent information here.
- **Completeness is count-based presence, not identity.** Whether the *right*
  resource/credential/step was chosen is a correctness question, handled by the
  divergence diff and the rubric, not by completeness.

## Known observations from the current corpus

- Schema validity (20/20 valid) and field completeness (uniformly 1.0) are
  **saturated**: every output is well-formed and structurally complete. Both are
  floors, not discriminators. The signal lives in the exact-match dimensions and
  the parameter divergences.
- Exact-match on the **trigger** is confounded by participant personalisation:
  the baseline often matches the trigger exactly *because* it did not personalise,
  while participants who added filters or changed the poll frequency diverge.
  This is precisely why divergence source must be attributed by hand.
- Temperature-0 baseline sampling is **near- but not fully deterministic** (e.g.
  scenario 3 sample 1 modelled the Gmail label as a resource while samples 2–3 did
  not), which is why three samples per scenario are retained.