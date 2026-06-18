# Single-shot baseline

This folder implements the **baseline condition** of the ASPEC evaluation: the
same language model the conversational pipeline uses, prompted **once** to produce
a full ASPEC, with no dialogue and no staged decomposition. It exists to isolate
the contribution of the *interaction mechanism* from the contribution of the
*domain-specific language* (the ASPEC schema + action catalogue), which are held
constant across both conditions.

## What it does

For each scenario, `run_baseline.py` builds one prompt containing:

1. the **scenario card** — the same task information a participant received
   (`scenario_cards.json`, taken from `../scenarios/participant-briefs.md`);
2. the **complete action catalogue** (`../action_catalogue.json`);
3. the **full ASPEC JSON Schema** (`../aspec.schema.json`);
4. the **full user vault** (`../uservault.json`) — the credentials and resources
   the user has connected;

and asks the model to return a single ASPEC JSON object. It draws several samples
per scenario, parses each response, validates it against the schema, and writes
the outputs plus a run manifest and a CSV.

## Design choices (these matter for validity)

- **Scenario card as input, not a researcher-authored description.** The card is
  the common statement of intent that both the participant and the baseline start
  from. Feeding the baseline a richer, hand-written description would confound
  "single-shot vs. dialogue" with "clean input vs. messy input." The card already
  contains the concrete details participants could ask the researcher about
  (names, addresses, column names, label IDs).
- **Free generation, not schema-constrained decoding.** Schema validity is one of
  the evaluation measures. Forcing the model to emit schema-conformant JSON would
  make that measure trivially 100%. The model generates freely; validity is
  checked *after* generation.
- **Minimal prompt (a lower bound).** No few-shot examples, no chain-of-thought,
  no field-by-field guidance. A more engineered prompt could do better, so results
  should be read as a conservative lower bound on single-shot performance.
- **Multiple samples per scenario.** The model is non-deterministic, so a single
  draw is a noisy estimate. The default is 3 samples. At `--temperature 0` (the
  pipeline default) variance is expected to be small; raise the temperature to
  characterise sampling variability.
- **Same model config as the pipeline.** Azure deployment `gpt-5.4-mini`,
  `temperature=0`, credentials from the project `.env` — identical to
  `ajora_automation_reasoning_agent_v4.get_llm`.
- **The user vault is a shared substrate, not pipeline machinery.** In the
  pipeline condition, concrete resource identifiers (`documentId`, Notion
  `databaseId`, `folderToWatch`, sheet columns) and every `credential_ref` are
  resolved from the vault, not typed by the user; the ground truth holds those
  exact IDs. Denying the baseline the vault would make it score 0 on the
  credentials and resources dimensions for a reason unrelated to dialogue, so the
  vault is given to both conditions, exactly like the schema and catalogue. The
  **full** vault is supplied (not the scenario-relevant slice): two vault
  spreadsheets share a `resource_type`, so the baseline must disambiguate the
  right one from the name on the card — the same disambiguation the pipeline does
  with the user. The residual asymmetry is *who* disambiguates (the model in one
  shot vs. the user across turns), not *what information* is available.

## What it does NOT do

- **No rubric scoring.** Schema validity is recorded as a generation-time sanity
  check only. Field completeness and specification correctness are applied by the
  shared scoring process **identically** to baseline and participant outputs — the
  rubric must not be baseline-specific. This script only produces the baseline
  ASPECs to be scored.
- **No usability/human dimension.** The baseline has no user, so it speaks only to
  output quality (schema validity, completeness, correctness). It cannot capture
  the pipeline's primary value — enabling a non-technical user to supply the intent
  at all. Frame it as supporting evidence, not the centrepiece.

## Usage

Run from the pipeline source dir (`thesis-new-pipeline/`), with the project
`.env` present at `../.env` (same one the pipeline uses):

```bash
python baseline/run_baseline.py                  # all scenarios, 3 samples, temp 0
python baseline/run_baseline.py --scenarios 1 3  # only scenarios 1 and 3
python baseline/run_baseline.py --samples 5 --temperature 0.7
python baseline/run_baseline.py --dry-run        # assemble prompts, no API calls
```

Requires the same dependencies as the pipeline: `langchain-openai`,
`langchain-core`, `python-dotenv`, `jsonschema`. `--dry-run` needs only the
standard library and is the way to verify paths and prompts before spending
tokens.

## Output

Each run writes a timestamped directory under `outputs/` (gitignored):

```
outputs/run_<UTC>/
  manifest.json              # run config + per-sample results
  results.csv                # one row per sample (parse_ok, schema_valid, ...)
  scenario-<N>/
    prompt.txt               # exact system + human prompt sent
    sample-01.json           # parsed ASPEC (if parseable)
    sample-01.raw.txt        # raw model response
    ...
```

The per-scenario `prompt.txt` and `manifest.json` make each run reproducible and
auditable.