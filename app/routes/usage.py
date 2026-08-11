from typing import Literal

from fastapi import APIRouter, Depends, Response

from app.logic.usage_ledger import get_usage_ledger
from app.security import get_current_user


router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/summary")
async def usage_summary(
    response: Response,
    window: Literal["24h", "7d", "30d"] = "7d",
    current_user: str = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    return get_usage_ledger().summary(current_user, window)
