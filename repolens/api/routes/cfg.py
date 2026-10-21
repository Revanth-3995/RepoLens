"""Control-flow graph and local data-flow endpoints (Phase 2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from repolens.api.deps import get_comprehension_service
from repolens.core.models import DataFlowResult, FunctionCFG
from repolens.services.comprehension_service import ComprehensionService

router = APIRouter()


@router.get(
    "/symbols/{symbol_id}/cfg",
    response_model=FunctionCFG,
    summary="Per-function control-flow graph",
    description=(
        "Builds the CFG on demand from the current source of the symbol's file."
        " Branch, loop, break/continue, return/raise, try/try*, with and match"
        " semantics are documented in docs/phase-2-plan.md. Exception edges are"
        " conservative; return/break/continue do not route through finally."
        " Results are bounded and report truncation and source changes."
    ),
)
def get_function_cfg(
    symbol_id: str, svc: ComprehensionService = Depends(get_comprehension_service)
) -> FunctionCFG:
    return svc.cfg_for_symbol(symbol_id)


@router.get(
    "/symbols/{symbol_id}/dataflow",
    response_model=DataFlowResult,
    summary="Bounded local reaching definitions and def-use links",
    description=(
        "Intraprocedural may-analysis only. It does not follow calls, aliases,"
        " attributes, or module globals. 'undefined_uses' means no locally"
        " reaching definition, not a runtime error."
    ),
)
def get_function_dataflow(
    symbol_id: str, svc: ComprehensionService = Depends(get_comprehension_service)
) -> DataFlowResult:
    return svc.dataflow_for_symbol(symbol_id)
