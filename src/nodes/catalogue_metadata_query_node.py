"""GOV-C2-119 — inner workflow step 1: catalogue_metadata_query.

Deterministic ingest + normalization of the supplied already-retrieved per-source catalogue metadata
lookups, determining each per-source availability status (available / metadata_missing / data_unavailable)
against the seeded dataset taxonomy + metadata schema. Sets ``queried_count``. **0 valid lookups (rejected
input, non-JSON text, or all rows missing entry_id) routes to the out-of-scope safe answer** — the agent
never fabricates an availability answer for data it did not receive. The staff free text is never interpreted
semantically, so prompt-like text in a supplied field cannot influence the availability determination.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import OpenDatasetAvailabilityService
from src.utils.audit import emit_trace_event


class CatalogueMetadataQueryNode(FunctionNode):
    """Normalize supplied per-source catalogue lookups + determine each availability status; set count."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Lookups arrive already validated + provenance-resolved by pre_process (S-1): each `source` is a
        # grounded citation `src:<sha8>` or None (a forged / non-allow-listed source was dropped at S-1). We
        # do not re-run provenance here — normalize trusts that single upstream resolution.
        slots = json.loads(state.get("validated_input") or state.get("user_input") or "{}")
        if not isinstance(slots, dict):
            slots = {}
        canonical = json.dumps(slots, ensure_ascii=False)
        entries = slots.get("catalogue_entries") if isinstance(slots.get("catalogue_entries"), list) else []

        if state.get("error_code") or not entries:
            emit_trace_event(
                "catalogue_metadata_query.skip", {"reason": state.get("error_code") or "no_entries"}, state
            )
            return {
                "validated_input": canonical,
                "normalized_entries": "[]",
                "queried_count": 0,
                "error_code": state.get("error_code") or "NO_METADATA",
                "status": AgentStatus.SUCCESS.value,
            }

        normalized = OpenDatasetAvailabilityService.normalize(entries)
        if not normalized:
            emit_trace_event("catalogue_metadata_query.skip", {"reason": "all_malformed"}, state)
            return {
                "validated_input": canonical,
                "normalized_entries": "[]",
                "queried_count": 0,
                "error_code": "NO_METADATA",
                "status": AgentStatus.SUCCESS.value,
            }

        distribution: dict[str, int] = {}
        for e in normalized:
            distribution[e["availability_status"]] = distribution.get(e["availability_status"], 0) + 1
        emit_trace_event(
            "catalogue_metadata_query.complete",
            {"supplied": len(entries), "normalized": len(normalized), "availability_distribution": distribution},
            state,
        )
        return {
            "validated_input": canonical,
            "normalized_entries": json.dumps(normalized, ensure_ascii=False),
            "queried_count": len(normalized),
            "status": AgentStatus.SUCCESS.value,
        }
