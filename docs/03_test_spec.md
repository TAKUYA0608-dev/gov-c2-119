# GOV-C2-119 — Test Specification

## Strategy

Three layers, all deterministic (no LLM, no network):

- **unit** — `tests/unit/test_nodes.py`: the deterministic `OpenDatasetAvailabilityService` (privacy
  tokenize vs provenance resolution, normalize, per-source availability status, cross-source reconciliation +
  coverage caveats + staleness, availability summary) and each node in isolation (S-1/S-2 hygiene + degrade,
  inner-node skip guards with S-4 emit, S-3 fail-closed + disclaimer gate).
- **unit (graph + real invoke)** — `tests/unit/test_graph.py`: outer GraphNode wiring (alias, class-level
  subgraph cache, extract/merge, outer-first error_code), inner-workflow route/registration, and
  **end-to-end through the real `Graph().invoke()`** for grounded / out-of-scope / injection-degrade /
  oversize-degrade / missing-provenance / unsafe-source / forged-surrogate / PII-tokenised /
  no-space-name-bypass / data-unavailable-distinction paths.
- **integration** — `tests/integration/test_end_to_end.py`: multi-source portfolio reconciliation +
  grounding, mixed cited/uncited fail-closed, forged-surrogate re-hash, empty → out-of-scope.

## Security / degraded contract (SDK 1.0.0)

Injection / oversize / empty are **never `status=ERROR`** — they degrade to `status=SUCCESS + error_code`
(`INJECTION_REJECTED` / `INPUT_TOO_LONG` / `INPUT_REJECTED`), the offending body is discarded, and
`post_process` still runs so the disclaimer + S-3 redaction + S-4 audit are always delivered. Injection
containment is **pre-LLM** (staff free text is contained as quoted-data; there is no LLM anyway — the
template is fully deterministic). Provenance is resolved **exactly once** at S-1; a forged / non-allow-listed
`source` is dropped there → no citation → S-3 fail-closes to `needs_review`.

## Local result (local SDK stub)

- Core suites (`tests/unit/test_graph.py`, `tests/unit/test_nodes.py`, `tests/integration/`): **98 passed,
  1 skipped** (server import skipped when the platform module is unavailable in a local stub env),
  **coverage = 94%** (`--cov=src`, target ≥ 80%).
- Full `tests/` run: **100 passed, 3 skipped, 3 known env-diff failures** (`test_pb_invoke_order`,
  `test_framework_compliance_tc06_tc07::tc06/tc07`). These three assert framework-level `@final` enforcement
  that the local SDK stub shim does not implement; they **pass under the real SDK in CI** (the wheel-era `run-tests`
  job installs `agenticstar-agentcore`) and are the unchanged scaffold conditional-stub / compliance files
  (byte-identical to the shipped scaffold and to the reference a sibling template).

## Key security test cases (real `Graph().invoke()`)

| # | Case | Expectation |
|---|------|-------------|
| TC-01 | Grounded standard-dataset availability (authorized catalogue source) | `status=SUCCESS`, `status_kind=availability_brief`, per-record `availability_status` + cited `src:<sha8>`, `human_review.required=True`, DRAFT disclaimer |
| TC-02 | NL text / empty | out-of-scope safe answer, `citations=[]`, disclaimer present |
| TC-03 | Injection payload | degraded `SUCCESS` (never ERROR), post ran (`PostProcessNode` in `node_history`), out-of-scope envelope, marker body absent, terminal S-4 audit carries `error_code=INJECTION_REJECTED` |
| TC-04 | Oversize (> 200 000 chars) | degraded `SUCCESS`, S-4 audit carries `error_code=INPUT_TOO_LONG` |
| TC-05 | Missing provenance | S-3 fail-closed → `needs_review`, brief body withheld, S-4 `error_code=CITATION_INCOMPLETE` |
| TC-06 | Unverifiable / non-allow-listed source (name, `unknown`, fabricated) | `needs_review`, raw source never in output |
| TC-07 | Forged surrogate source (`src:1a2b3c4d` / `item:deadbeef` / `ds:deadbeef`) | `needs_review`, `citations=[]`, forged value never in output |
| TC-08 | PII / no-space-name `entry_id` (`Alice` / `TaroYamada`) | tokenized `item:<sha8>`, name never in output, referential integrity across record ↔ citations |
| TC-09 | Municipality / query free text carrying name / phone / email | redacted — none appear in a grounded output |
| TC-10 | Unknown caller field carrying PII | whitelist-by-construction — never reaches output |
| TC-11 | Mixed cited + uncited portfolio | whole grounded brief fails-closed to `needs_review` |
| TC-12 | Not-registered dataset (`metadata_present=false`) | `availability_status=data_unavailable` (distinct from `metadata_missing`) + caveat — the core Agent-value distinction |
| TC-13 | Same dataset type across 2 sources with disagreeing publisher/format | multi-source reconciliation surfaces a `source_disagreement` caveat |

## Framework compliance / proof-of-boundary

TC-05 (S-4 no duplicate lifecycle events) / TC-06 / TC-07 (`@final` S-2/S-3 gates not overridden) /
PB-1…PB-6 are the unchanged scaffold `test_framework_compliance_tc06_tc07.py` +
`tests/proof_of_boundary/*` (pass under the real SDK in CI). PB-7 (HITL interrupt) is **Auto-waived —
non-HITL** (`config/agent.yaml` does not set `hitl.enabled: true`); the conditional stub remains.

## Reproduce

```bash
source .venv/bin/activate
python -m pytest tests/unit/test_graph.py tests/unit/test_nodes.py tests/integration/ -q --cov=src --cov-report=term
ruff check src tests/unit/test_nodes.py tests/unit/test_graph.py tests/integration/
python scripts/check_trust_level.py src/
python scripts/check_cat_consistency.py
python scripts/check_dep_pinning.py
```
