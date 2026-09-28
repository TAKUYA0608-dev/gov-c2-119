# GOV-C2-119 — Integration: full outer Graph().invoke() across a multi-source availability portfolio

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph

_SUCCESS = AgentStatus.SUCCESS.value


def _invoke(user_input: str):
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    return Graph().invoke(user_input, ctx=ctx)


def test_multi_source_portfolio_reconciled_and_grounded():
    payload = {
        "municipality": "品川区",
        "catalogue_entries": [
            # evacuation_areas across two authorized sources — publisher disagrees (reconciliation caveat)
            {"entry_id": "e1", "dataset_type": "evacuation_areas", "metadata_present": True,
             "data_available": True, "publisher": "品川区", "update_date": "2026-03-01",
             "licence": "CC-BY-4.0", "format": "csv", "endpoint": "https://x", "source": "govcatalog:eva1"},
            {"entry_id": "e2", "dataset_type": "evacuation_areas", "metadata_present": True,
             "data_available": True, "publisher": "品川区役所", "update_date": "2026-03-01",
             "licence": "CC-BY-4.0", "format": "geojson", "endpoint": "https://y", "source": "ckan:eva2"},
            # tourism: registered but missing licence → metadata_missing
            {"entry_id": "e3", "dataset_type": "tourism", "metadata_present": True, "data_available": True,
             "update_date": "2026-02-01", "endpoint": "https://z", "source": "data_go_jp:tour1"},
            # events: not registered → data_unavailable
            {"entry_id": "e4", "dataset_type": "events", "metadata_present": False, "source": "govcatalog:ev1"},
        ],
    }
    out = _invoke(json.dumps(payload, ensure_ascii=False))
    assert out["status"] == _SUCCESS
    env = json.loads(out["output"])
    assert env["status_kind"] == "availability_brief"
    assert len(env["availability_records"]) == 4 and len(env["citations"]) == 4
    # least-obtainable (data_unavailable) ranked first
    assert env["availability_records"][0]["availability_status"] == "data_unavailable"
    summary = env["availability_summary"]
    assert summary["total_records"] == 4
    assert summary["availability_distribution"]["metadata_missing"] == 1
    assert summary["availability_distribution"]["data_unavailable"] == 1
    # multi-source reconciliation surfaced a cross-source disagreement (publisher / format differ)
    assert summary["records_with_source_disagreement"]
    assert env["human_review"]["required"] is True
    assert "参考" in env["disclaimer"]


def test_mixed_cited_and_uncited_blocks_whole_brief():
    """Per-record citation completeness: one uncited record fails-closed the whole grounded brief."""
    payload = {"catalogue_entries": [
        {"entry_id": "e1", "dataset_type": "tourism", "metadata_present": True, "data_available": True,
         "licence": "CC", "update_date": "2026-01-01", "endpoint": "x", "source": "govcatalog:t1"},  # cited
        {"entry_id": "e2", "dataset_type": "events", "metadata_present": True, "data_available": True,
         "licence": "CC", "update_date": "2026-01-01", "endpoint": "y"},                              # uncited
    ]}
    out = _invoke(json.dumps(payload))
    env = json.loads(out["output"])
    assert env["status_kind"] == "needs_review"
    assert env["availability_records"] == [] and env["citations"] == []


def test_forged_entry_id_surrogate_rehashed():
    # ★ a caller value SHAPED like an internal surrogate (item:deadbeef) is re-hashed at S-1 (no syntactic
    # passthrough), so it can never forge an internal join key / reference another record.
    payload = {"catalogue_entries": [
        {"entry_id": "item:deadbeef", "dataset_type": "tourism", "metadata_present": True,
         "data_available": True, "licence": "CC", "update_date": "2026-01-01", "endpoint": "x",
         "source": "govcatalog:t1"},
    ]}
    out = _invoke(json.dumps(payload))
    env = json.loads(out["output"])
    assert env["status_kind"] == "availability_brief"
    tok = env["availability_records"][0]["item_id"]
    assert tok.startswith("item:") and tok != "item:deadbeef"   # re-hashed, not passthrough
    assert "item:deadbeef" not in out["output"]


def test_empty_object_is_out_of_scope():
    out = _invoke(json.dumps({"catalogue_entries": []}))
    env = json.loads(out["output"])
    assert env["status_kind"] == "out_of_scope" and env["citations"] == []
