from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.config import Settings, get_settings


def require_internal_tools(
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if not settings.enable_internal_tools:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


InternalToolsEnabled = Annotated[None, Depends(require_internal_tools)]
