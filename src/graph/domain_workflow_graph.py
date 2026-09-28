"""GOV-C2-119 — inner domain workflow graph (Cat 2).

Instantiated by AvailabilityRetrieveWorkflowGraphNode.get_subgraph() in graph.py. Linear topology with
per-node skip guards (the portable Cat 2 form; conditional edges don't propagate across the subgraph
boundary):

    START → catalogue_metadata_query → availability_reconcile → availability_brief_compose → data_owner_gate → END

On rejected / 0-lookup input, catalogue_metadata_query sets queried_count=0 (+error_code);
availability_reconcile and data_owner_gate no-op and availability_brief_compose emits the out-of-scope safe
answer — no fabricated brief.
"""

from __future__ import annotations
from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState

from src.nodes.availability_brief_compose_node import AvailabilityBriefComposeNode
from src.nodes.availability_reconcile_node import AvailabilityReconcileNode
from src.nodes.catalogue_metadata_query_node import CatalogueMetadataQueryNode
from src.nodes.data_owner_gate_node import DataOwnerGateNode
from src.schemas.state import State


class AvailabilityRetrieveWorkflow(BaseGraph):
    """Inner graph: catalogue_metadata_query → availability_reconcile → availability_brief_compose → data_owner_gate."""

    @property
    def name(self) -> str:
        return "AvailabilityRetrieveWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract.
        self._nodes["catalogue_metadata_query"] = CatalogueMetadataQueryNode()
        self._nodes["availability_reconcile"] = AvailabilityReconcileNode()
        self._nodes["availability_brief_compose"] = AvailabilityBriefComposeNode()
        self._nodes["data_owner_gate"] = DataOwnerGateNode()

    def add_edges(self) -> None:
        # Static linear backbone; the 0-lookup / rejected skip is handled by per-node guards.
        self._sg.add_edge(START, "catalogue_metadata_query")
        self._sg.add_edge("catalogue_metadata_query", "availability_reconcile")
        self._sg.add_edge("availability_reconcile", "availability_brief_compose")
        self._sg.add_edge("availability_brief_compose", "data_owner_gate")
        self._sg.add_edge("data_owner_gate", END)

    def route(self, state: AgentState) -> str:
        """Required by the BaseGraph ABC. Linear topology → not wired to a conditional edge."""
        if state.get("error_code") or state.get("queried_count", 0) == 0:
            return "availability_brief_compose"
        return "availability_reconcile"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "queried_count": state.get("queried_count", 0),
            "human_review_required": state.get("human_review_required", False),
            "error_code": state.get("error_code"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
