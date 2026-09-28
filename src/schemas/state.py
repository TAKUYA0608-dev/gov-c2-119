"""GOV-C2-119 — Agent state (Municipal Standard Open-Dataset Availability Retriever, Cat 2).

ADR-005: State is a flat TypedDict — never a validation/BaseModel instance. Complex fields are stored
as JSON strings (``NotRequired[str]`` + ``# JSON:``); nodes ``json.dumps`` on write / ``json.loads`` on read.

Read-only / advisory: the agent receives a staff question + target municipality + permitted catalogue
sources (allow-list) and a set of already-retrieved per-source catalogue metadata lookups, queries the
allow-listed catalogue/API metadata **read-only**, reconciles availability across the permitted sources,
and produces an **AvailabilityBrief** deliverable — it never determines eligibility, certifies data quality,
publishes/releases data, contacts a resident, or makes an emergency-routing decision. The output is cited /
needs-review only; the final verification of fitness / licence / downstream use is the data-owner's (human).

All agent-specific fields are NotRequired (populated progressively; absent at empty-start invoke).
"""

from __future__ import annotations


from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for the standard open-dataset availability retrieval + cited synthesis workflow."""

    # ── pre_process (QueryIngest + RequestContainment; S-1 + S-2 pre-LLM) ──
    validated_input: str  # JSON: {catalogue_entries[], query, municipality, dataset_type, scope, period}
    input_format: str  # "json" | "text" | "empty" | "rejected"
    enriched_context: str  # JSON: {source, channel} (read-only caller context)

    # ── inner workflow (catalogue_metadata_query → availability_reconcile → availability_brief_compose → data_owner_gate) ─
    normalized_entries: str  # JSON: [{item_id, dataset_type, availability_status, publisher, ..., source}]
    queried_count: int  # per-source catalogue metadata lookups normalized (0 → out-of-scope safe answer)
    reconciled_records: (
        str  # JSON: [{item_id, dataset_type, availability_status, coverage_caveats[], retrieval_provenance, citation}]
    )
    result: str  # JSON: assembled AvailabilityBrief (incl. human_review)
    human_review_required: bool  # True once the DataOwnerVerificationGate flags data-owner sign-off
    review_status: str  # "pending_data_owner_verification" | "not_required"

    # ── post_process (OutputSanitise — S-3 gate + S-4 audit) ──────────────────
    formatted_output: str  # JSON: final response envelope (brief + disclaimer)
    disclaimer: str  # mandatory DRAFT / advisory-only disclaimer
    audit_logged: bool  # True once the terminal audit event is emitted

    # ── degraded-path signalling (SUCCESS + error_code, never status=ERROR) ───
    # INPUT_REJECTED | INJECTION_REJECTED | INPUT_TOO_LONG | NO_METADATA | CITATION_INCOMPLETE
    error_code: str
    error_message: str  # operator-facing detail
