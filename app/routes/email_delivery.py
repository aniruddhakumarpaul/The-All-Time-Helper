from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.contracts.email_draft import EmailDraft
from app.logic.capability_policy import (
    CapabilityContext,
    CapabilityDeniedError,
    CapabilitySource,
)
from app.security import get_current_user
from app.services.email_delivery_service import (
    EmailAuthorizationError,
    EmailValidationError,
    email_delivery_service,
)

router = APIRouter(prefix="/email", tags=["email"])


EmailDraftPayload = EmailDraft


class SendDraftRequest(BaseModel):
    draft: EmailDraftPayload
    admin_key: str
    request_id: Optional[str] = None


@router.post("/send-draft")
def send_approved_email_draft(
    req: SendDraftRequest,
    current_user: str = Depends(get_current_user),
):
    try:
        context = CapabilityContext(
            owner=current_user,
            source=CapabilitySource.HTTP,
            request_authorization_verified=email_delivery_service.is_authorized(req.admin_key),
        )
        result = email_delivery_service.send_approved_email(
            draft=req.draft,
            owner=current_user,
            admin_key=req.admin_key,
            request_id=req.request_id,
            capability_context=context,
        )
    except (CapabilityDeniedError, EmailAuthorizationError) as exc:
        raise HTTPException(status_code=403, detail="Invalid admin key") from exc
    except EmailValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": result.success,
        "status": result.status,
        "request_id": result.request_id,
        "mode": result.mode,
        "duplicate": result.duplicate,
    }
