"""Owner-only read and decision API for internally created approvals."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.logic.human_approval import (
    HumanApprovalConflict,
    human_approval_store,
    public_approval_snapshot,
)
from app.security import get_current_user


router = APIRouter(prefix="/approvals", tags=["approvals"])
NO_STORE = "private, no-store"
NO_STORE_HEADERS = {"Cache-Control": NO_STORE}


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class PrivateApprovalResponsesMiddleware:
    """Set no-store on every approval response, including handled errors."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = str(scope.get("path") or "")
        if scope.get("type") != "http" or not (path == "/approvals" or path.startswith("/approvals/")):
            await self.app(scope, receive, send)
            return

        async def send_private(message):
            if message.get("type") == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", NO_STORE.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_private)


def _approval_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail="Approval not found",
            headers=NO_STORE_HEADERS,
        ) from exc


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Approval not found", headers=NO_STORE_HEADERS)


def _changed() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "approval_changed"},
        headers=NO_STORE_HEADERS,
    )


@router.get("")
def list_approvals(
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: str = Depends(get_current_user),
):
    response.headers["Cache-Control"] = NO_STORE
    records = human_approval_store.list_for_owner(current_user, limit=limit)
    return {"approvals": [public_approval_snapshot(record) for record in records]}


@router.get("/{approval_id}")
def get_approval(
    approval_id: str,
    response: Response,
    current_user: str = Depends(get_current_user),
):
    response.headers["Cache-Control"] = NO_STORE
    record = human_approval_store.get(_approval_id(approval_id), current_user)
    if record is None:
        raise _not_found()
    return public_approval_snapshot(record)


@router.post("/{approval_id}/approve")
def approve(
    approval_id: str,
    request: ApprovalDecisionRequest,
    response: Response,
    current_user: str = Depends(get_current_user),
):
    response.headers["Cache-Control"] = NO_STORE
    try:
        record = human_approval_store.approve(
            _approval_id(approval_id), current_user, request.expected_version,
        )
    except HumanApprovalConflict as exc:
        raise _changed() from exc
    if record is None:
        raise _not_found()
    return public_approval_snapshot(record)


@router.post("/{approval_id}/reject")
def reject(
    approval_id: str,
    request: ApprovalDecisionRequest,
    response: Response,
    current_user: str = Depends(get_current_user),
):
    response.headers["Cache-Control"] = NO_STORE
    try:
        record = human_approval_store.reject(
            _approval_id(approval_id), current_user, request.expected_version,
        )
    except HumanApprovalConflict as exc:
        raise _changed() from exc
    if record is None:
        raise _not_found()
    return public_approval_snapshot(record)
