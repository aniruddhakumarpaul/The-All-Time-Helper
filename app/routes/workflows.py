"""Authenticated workflow recovery and cancellation surfaces."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response

from app.logic.workflow_orchestrator import pending_workflow_store
from app.logic.workflow_store import public_workflow_snapshot
from app.security import get_current_user


router = APIRouter(prefix="/workflows", tags=["workflows"])
NO_STORE = "private, no-store"
NO_STORE_HEADERS = {"Cache-Control": NO_STORE}


def _workflow_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail="Workflow not found",
            headers=NO_STORE_HEADERS,
        ) from exc


@router.get("")
def list_workflows(
    response: Response,
    current_user: str = Depends(get_current_user),
):
    response.headers["Cache-Control"] = NO_STORE
    snapshots = pending_workflow_store.backend.list_for_owner(current_user)
    return {"workflows": [public_workflow_snapshot(item) for item in snapshots]}


@router.get("/{workflow_id}")
def get_workflow(
    workflow_id: str,
    response: Response,
    current_user: str = Depends(get_current_user),
):
    response.headers["Cache-Control"] = NO_STORE
    snapshot = pending_workflow_store.backend.get(
        _workflow_id(workflow_id), current_user
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="Workflow not found",
            headers=NO_STORE_HEADERS,
        )
    return public_workflow_snapshot(snapshot)


@router.post("/{workflow_id}/cancel")
def cancel_workflow(
    workflow_id: str,
    response: Response,
    current_user: str = Depends(get_current_user),
):
    response.headers["Cache-Control"] = NO_STORE
    cancelled = pending_workflow_store.backend.request_cancel(
        _workflow_id(workflow_id), current_user
    )
    if not cancelled:
        raise HTTPException(
            status_code=404,
            detail="Workflow not found",
            headers=NO_STORE_HEADERS,
        )
    return {"success": True}
