"""GOV-C2-119 — inner workflow step 2: availability_reconcile.

**The core of the Agent value.** Deterministically reconciles availability across the permitted sources per
dataset type: distinguishes "metadata missing" (entry present, key metadata absent) from "data unavailable"
(not registered / not obtainable), detects cross-source disagreements (publisher / update_date / licence /
format), flags single-source lookups and potential staleness, and synthesizes ``coverage_caveats`` +
``retrieval_provenance`` per record. Skips (no-op) on rejected / 0-queried input, after emitting a skip audit
event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import OpenDatasetAvailabilityService
from src.utils.audit import emit_trace_event


class AvailabilityReconcileNode(FunctionNode):
    """Reconcile availability across permitted sources; synthesize coverage caveats + provenance."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("queried_count", 0) == 0:
            emit_trace_event("availability_reconcile.skip", {"reason": state.get("error_code") or "no_lookups"}, state)
            return {}

        normalized = json.loads(state.get("normalized_entries") or "[]")
        reconciled = OpenDatasetAvailabilityService.reconcile(normalized)
        caveat_count = sum(len(r["coverage_caveats"]) for r in reconciled)
        disagreements = sum(
            1 for r in reconciled if any(c.startswith("source_disagreement") for c in r["coverage_caveats"])
        )
        emit_trace_event(
            "availability_reconcile.complete",
            {"records": len(reconciled), "caveat_count": caveat_count, "source_disagreements": disagreements},
            state,
        )
        return {"reconciled_records": json.dumps(reconciled, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
