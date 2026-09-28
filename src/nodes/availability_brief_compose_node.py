"""GOV-C2-119 — inner workflow step 3: availability_brief_compose.

Composes the **AvailabilityBrief** deliverable: a municipality-level availability summary, and a per-record
availability entry (dataset type, availability_status, publisher / update_date / licence / format /
endpoint_ref, coverage_caveats, retrieval_provenance, needs-review mark), each cited to its authorized
source. Records are ordered by availability priority (data_unavailable / metadata_missing first, so gaps
surface up front). The brief is cited / needs-review only — it never certifies fitness/licence, publishes
data, or contacts a resident. On the 0-queried / rejected branch it emits the out-of-scope safe answer.

Output is whitelist-by-construction: only the explicitly allow-listed availability fields reach the brief;
an arbitrary caller field carrying PII is never copied into a record.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import OpenDatasetAvailabilityService
from src.utils.audit import emit_trace_event

_OUT_OF_SCOPE = (
    "可用性を判定できる標準オープンデータのカタログメタデータが入力に見つかりませんでした。"
    "catalogue_entries 配列に entry_id・dataset_type・source（認可済みカタログ）・"
    "metadata_present/data_available・publisher/update_date/licence/format/endpoint 等を含む JSON を"
    "ご指定いただくか、対象自治体・データセット種別を明確にしてください。"
)

# whitelist-by-construction: only these fields from a reconciled record reach the brief.
_RECORD_FIELDS = (
    "item_id",
    "dataset_type",
    "dataset_type_label",
    "availability_status",
    "publisher",
    "update_date",
    "licence",
    "format",
    "endpoint_ref",
    "coverage_caveats",
    "retrieval_provenance",
    "source_count_for_type",
    "status_kind",
    "citation",
)


class AvailabilityBriefComposeNode(FunctionNode):
    """Compose the AvailabilityBrief deliverable with citations (or safe answer on 0-queried)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        reconciled = json.loads(state.get("reconciled_records") or "[]")
        if state.get("error_code") or not reconciled:
            emit_trace_event(
                "availability_brief_compose.safe", {"reason": state.get("error_code") or "no_records"}, state
            )
            report: dict[str, Any] = {
                "status_kind": "out_of_scope",
                "message": _OUT_OF_SCOPE,
                "availability_summary": {},
                "availability_records": [],
                "citations": [],
            }
            return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

        records: list[dict[str, Any]] = []
        citations: list[dict[str, str]] = []
        for r in reconciled:
            records.append({k: r.get(k) for k in _RECORD_FIELDS})
            citations.append({"item_id": r["item_id"], "source": r["citation"]})
        records.sort(key=lambda rec: (-OpenDatasetAvailabilityService.priority_rank(rec), rec["item_id"]))

        summary = OpenDatasetAvailabilityService.availability_summary(reconciled)
        report = {
            "status_kind": "availability_brief",
            "municipality": self._municipality(state),
            "availability_summary": summary,
            "availability_records": records,
            "citations": citations,
        }
        emit_trace_event(
            "availability_brief_compose.complete",
            {
                "record_count": len(records),
                "caveat_count": sum(len(rec["coverage_caveats"]) for rec in records),
                "citation_count": len(citations),
            },
            state,
        )
        return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

    @staticmethod
    def _municipality(state: dict[str, Any]) -> Any:
        slots = json.loads(state.get("validated_input") or "{}")
        return slots.get("municipality")
