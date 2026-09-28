"""GOV-C2-119 — inner workflow step 4: data_owner_gate (DataOwnerVerificationGate).

Deterministic human-in-the-loop gate. It does **not** execute anything, publish data, or certify fitness —
it flags that the availability brief requires the **data-owner's** verification of fitness / licence /
downstream use before the metadata is relied upon, records that obligation + the review status into the
brief, and sets ``human_review_required``. Skips (no-op) on the rejected / 0-queried safe-answer branch (no
gate needed) after emitting a skip audit event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event


class DataOwnerGateNode(FunctionNode):
    """Flag data-owner verification of fitness/licence/downstream-use; set human_review_required."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report = json.loads(state.get("result") or "{}")
        if (
            state.get("error_code")
            or state.get("queried_count", 0) == 0
            or report.get("status_kind") != "availability_brief"
        ):
            emit_trace_event("data_owner_gate.skip", {"reason": state.get("error_code") or "no_brief"}, state)
            return {
                "human_review_required": False,
                "review_status": "not_required",
                "status": AgentStatus.SUCCESS.value,
            }

        # Every availability record needs the data-owner to verify fitness/licence/downstream use before it
        # is relied upon; records that are not fully available are always flagged for attention. The agent
        # presents availability metadata; it never certifies or publishes.
        material: list[dict[str, Any]] = []
        for rec in report.get("availability_records", []):
            material.append(
                {
                    "item_id": rec["item_id"],
                    "dataset_type": rec["dataset_type"],
                    "availability_status": rec["availability_status"],
                    "reason": "Data-owner must verify fitness / licence / downstream use before relying on this "
                    "availability metadata; the agent presents metadata only and does not certify",
                }
            )

        required = bool(material)
        review = {
            "required": required,
            "status": "pending_data_owner_verification" if required else "not_required",
            "note": "Fitness / licence / downstream-use verification is the data-owner's authority. This "
            "agent produces cited availability metadata only (needs-review); it does not certify, "
            "publish, or contact residents.",
            "material_items": material,
        }
        report["human_review"] = review
        emit_trace_event(
            "data_owner_gate.complete", {"review_required": required, "material_item_count": len(material)}, state
        )
        return {
            "result": json.dumps(report, ensure_ascii=False),
            "human_review_required": required,
            "review_status": review["status"],
            "status": AgentStatus.SUCCESS.value,
        }
