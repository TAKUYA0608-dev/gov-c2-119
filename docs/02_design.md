# GOV-C2-119 — Template Design Specification

Municipal Standard Open-Dataset Availability Retriever (Cat 2, GraphNode-in-main).

## Position in AgentCore Architecture

- **Agent Class**: `MunicipalStandardOpenDatasetAvailabilityRetrieverAgent` (module-level alias of `Graph`)
- **L1 Base**: AgentBaseGraph (L1 direct — Cat 2 GraphNode-in-main; **not** AutonomousBaseGraph). The
  read-only ToolCallingAgent catalogue-metadata pattern is a design reference only; the workflow is
  implemented directly on AgentBaseGraph (2026-05-18 L2-deprecation ruling). ToolCallingAgent is not a valid
  L1 Base enumeration value, so the Base is written as `Other(read-only ToolCallingAgent catalogue-metadata
  pattern)`; `AgentBaseGraph` appears only on the inheritance line.
- **Category**: Cat 2 — orchestrates a fixed multi-step workflow to produce one job-to-be-done deliverable
  (an AvailabilityBrief for a standard open dataset type × municipality).
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); complex fields are JSON strings (ADR-005)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only — no `config` param)
  - Graph: composition (`register_nodes()` for node substitution; domain complexity behind a `GraphNode`)
- **LLM**: none. The template is **fully deterministic** (schema-defined catalogue lookup normalization +
  set-membership + cross-source reconciliation + keyed clause/caveat composition against a seeded source
  allowlist + dataset taxonomy + metadata schema). There is no model in `config/agent.yaml`, no LLM
  dependency in `pyproject.toml`, and no LLM call anywhere in `src/`. "pre-LLM" in the S-2 discussion below
  therefore means "before any downstream node reads the staff free text / caller free-text metadata".

## Architecture Overview

### Node Configuration (outer 5-slot backbone)

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema/session/trust setup | user_input | caller_trust_level, session_id | InitializeNode (default) |
| pre_process | `QueryIngest` + `RequestContainment` — S-1 normalisation (NFKC, control-strip, size cap) + S-2 pre-LLM containment/source-allowlist handling. **injection/oversize → degraded `SUCCESS + error_code`, offending body discarded (never `status=ERROR`)**. Field-level input hygiene (credential/My-Number/email/phone redaction on every string written to State); **resident-PII display fields dropped**; identifiers (`entry_id`/`dataset_ref`) **UNCONDITIONALLY tokenized to opaque, non-reversible surrogates** (a bare name is opaque like any value, no surrogate→raw rejoin map kept); **provenance `source` resolved to a citation ONLY if it names an authorized catalogue system of record (privacy-tokenized `src:<sha8>`), else dropped to `None`** — S-3 then blocks; the staff `query` free text is contained as quoted-data (never semantically executed); `municipality`/`scope`/`period` hygiened | user_input | validated_input, input_format, enriched_context, (error_code) | PreProcessNode (FunctionNode) |
| main | `AvailabilityRetrieveWorkflowGraphNode` — wraps inner `AvailabilityRetrieveWorkflow` (composition criterion #9) | validated_input | result, queried_count, human_review_required, (error_code), status | GraphNode (subgraph) |
| post_process | `OutputSanitise` — S-3 output gate: **fail-closed per-record citation completeness** (any availability record lacking a verifiable authorized-source citation → whole brief `needs_review` degrade, brief body withheld, `error_code=CITATION_INCOMPLETE`) + resident-name/company/phone/email/credential/My-Number re-redaction + DRAFT disclaimer, S-4 no-persist audit | result | formatted_output, disclaimer, audit_logged, (error_code) | PostProcessNode (FunctionNode) |
| finalize | build response envelope | formatted_output | output, status | FinalizeNode (default) |

### Inner workflow (`src/graph/domain_workflow_graph.py` — BaseGraph, linear + per-node skip guard)

```
START → catalogue_metadata_query → availability_reconcile → availability_brief_compose → data_owner_gate → END
```

| Inner Node | Responsibility | Skip guard |
|------|---------------|-----------|
| catalogue_metadata_query | Deterministic ingest + normalize of the supplied already-retrieved per-source catalogue metadata lookups against the seeded source allowlist + dataset taxonomy; determine each per-source availability signal (`available` / `metadata_missing` / `data_unavailable`) from `metadata_present` / `data_available` / licence / endpoint / update_date presence; set `queried_count`. **0 valid lookups → `error_code=NO_METADATA` → out-of-scope safe answer**. Staff free text is never interpreted semantically | — (first node; emits `.skip` on rejected/no-lookup input) |
| availability_reconcile | **Multi-source cited reconciliation (the core Agent value)**: group the normalized per-source lookups by `dataset_type`, reconcile availability across the permitted sources, **distinguish "metadata missing" (entry present, key metadata absent) vs "data unavailable" (not registered / not obtainable)**, detect cross-source disagreements (publisher/update_date/licence/format), synthesize `coverage_caveats` + `retrieval_provenance`. Conflicts/gaps/single-source → caveat + needs-review | no-op `return {}` (after `.skip` emit) on `error_code` / `queried_count == 0` |
| availability_brief_compose | Compose the AvailabilityBrief deliverable: per record — dataset_type, availability_status, publisher/update_date/licence/format/endpoint_ref, coverage_caveats, retrieval_provenance, needs-review mark, and the source citation; a municipality-level availability summary; ordered by availability priority (data_unavailable/metadata_missing first). On 0-lookup/rejected → out-of-scope safe answer | emits safe answer on `error_code` / no records |
| data_owner_gate | Deterministic **DataOwnerVerificationGate**: mark `human_review_required=True` + `review_status="pending_data_owner_verification"`, record the data-owner sign-off obligation (fitness / licence / downstream-use verification is the data-owner's authority, never the agent's) into the brief. The agent **never** certifies fitness/licence or publishes | no-op `return {}` (after `.skip` emit) on `error_code` / `queried_count == 0` (safe answer needs no gate) |

`AvailabilityRetrieveWorkflowGraphNode.get_subgraph()` caches the compiled inner workflow on the **class
attribute** (`AvailabilityRetrieveWorkflowGraphNode._subgraph`, not `self` — avoids mutable node-instance
state per §9; built once; `BaseGraph.invoke()` `_ensure_compiled` is idempotent). `extract_input()` passes
`validated_input` into the inner graph; `merge_output()` surfaces `result / queried_count /
human_review_required / error_code / status` — with **`error_code` OUTER-first** (`state.get("error_code")
or sub_result.get("error_code")`) so a pre-stage rejection survives to the terminal S-4 audit (the inner
workflow runs on the discarded body and would otherwise overwrite it with `NO_METADATA`).

## Security Model (S-1 … S-5)

- **S-1 (input normalisation + field hygiene)**: NFKC + control-char strip + size cap; every string written
  into `validated_input` is passed through credential/My-Number/email/phone redaction; identifiers
  (`entry_id`/`dataset_ref`) are tokenized to opaque surrogates; provenance resolved **exactly once** here.
  Injection detection is **not** attributed to S-1 (handled by the S-2 pre-LLM containment + S-3 output gate).
- **S-2 (pre-LLM containment + authorized-source handling)**: the source allowlist is enforced — a caller
  `source` becomes a grounded citation only when it names an authorized catalogue system of record, and a
  non-allow-listed / unverifiable source is dropped to `None` (never queried as authoritative). Resident-PII
  display fields are dropped; opaque IDs are non-reversible one-way hashes with **no surrogate→raw rejoin
  map in graph state**. The staff `query` free text is contained as quoted-data / schema constraint — it is
  never interpreted semantically. **Injection containment is pre-LLM**: prompt-injection markers or oversize
  input degrade to a safe out-of-scope answer *without any semantic execution of the offending text*.
  - **Degraded contract (SDK 1.0.0)**: an S-2 rejection is surfaced as **`status=SUCCESS` + `error_code`**
    (`INJECTION_REJECTED` / `INPUT_TOO_LONG` / `INPUT_REJECTED`) with the offending body discarded — it is
    **never `status=ERROR`** (which would short-circuit `route()` straight to `finalize`, skipping
    `post_process` and thus the disclaimer / S-3 redaction / S-4 audit). `post_process` therefore always
    runs and always delivers the out-of-scope safe answer + disclaimer + audit. `_extra_security_gate_input`
    MUST NOT raise and MUST `return dict(state)`; `execute()` re-checks the same conditions because the
    local stub framework does not invoke the `@final` hook.
- **S-3 (output gate, fail-closed)**: enforce per-record citation completeness — a grounded AvailabilityBrief
  in which any availability record lacks a verifiable authorized-`source` citation is **never presented**; it
  degrades to `needs_review` with the brief body withheld (`error_code=CITATION_INCOMPLETE`, still SUCCESS).
  Re-redact any leaked secret/contact/name pattern (defense-in-depth). Append the mandatory DRAFT advisory
  disclaimer. `_extra_security_gate_output` receives the `execute()` result delta and MAY raise to block an
  output missing the disclaimer.
- **S-4 (audit, no-persist)**: every node `execute()` path — including every skip/0-count/degraded branch —
  emits a count-only domain trace event via `src.utils.audit.emit_trace_event`; payloads carry counts /
  availability-status distribution / dataset-type keys / error codes only (no resident name, staff free
  text, or raw caller provenance). The raw record and any full text / PII are not retained.
- **S-5 (rate limit / abuse)**: enforced at the platform entry point; the agent is read-only and performs
  no external write / publication / resident contact.

## Read-only / non-execution boundary

The agent **never** determines eligibility, certifies data quality, publishes/releases data, contacts a
resident, or makes an emergency-routing decision. All output is **candidate / needs-review** cited
availability metadata, and the final verification of fitness / licence / downstream use is always the
data-owner's (human), gated by the DataOwnerVerificationGate.

## Import Isolation Confirmation

- Template does not import agenticstar-platform SDK (Level 0); import targets are `framework/` and `shared/`
  only. Deterministic domain logic in `src/services/service.py` has no framework imports and no LLM.

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph (L1 Base)** | Fixed multi-step retrieval workflow with a bounded deliverable — not an autonomous think→act loop |
| Composition pattern | Standalone FunctionNode-in-main | GraphNode-in-main (subgraph) | **GraphNode-in-main** | Cat 2 domain complexity confined to the inner `AvailabilityRetrieveWorkflow` behind a `GraphNode` |
| Provenance vs privacy | conflate | separate code paths | **separate** | `opaque_id` (privacy tokenize, unconditional) is distinct from `resolve_provenance` (authorized-source citation); a value merely *shaped* like a surrogate is re-hashed / dropped |
| Degraded reject | status=ERROR | SUCCESS + error_code | **SUCCESS + error_code** | ERROR short-circuits `post_process`; degraded SUCCESS guarantees disclaimer + S-3 + S-4 always run |

## Open Items (Stage ③ implementation plan)

The design MR ships `docs/02` + `src/schemas/state.py` (+ the `docs/01` reconciliation note) only. The
Stage ③ implementation MR adds: the six node implementations (pre_process, the four inner nodes,
post_process), the inner/outer graph wiring (`get_subgraph` class-level caching + `merge_output` outer-first
error_code), the deterministic `OpenDatasetAvailabilityService` (seeded source allowlist + dataset taxonomy
+ metadata schema + availability-status/reconciliation + provenance/opaque-id helpers), `src/utils/audit.py`
(S-4 shim), the `MunicipalStandardOpenDatasetAvailabilityRetrieverAgent = Graph` registry alias, and the
unit / integration / real-invoke tests (including the forged-surrogate, unauthorized-source, PII-tokenised,
and citation-fail-closed regressions). Seeded source allowlist / dataset taxonomy / metadata schema are
CoE-calibratable via a change-controlled engineer MR + specialist review — they are not runtime-editable
operational actions.
