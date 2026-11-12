from hmac import compare_digest

from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings

router = APIRouter(prefix="/v1/auth", tags=["authentication"])


@router.get("/session")
def get_session_status(request: Request) -> dict:
    """Allow the Studio to discover authentication mode without exposing the token."""
    token = settings.studio_api_token
    if token:
        authorization = request.headers.get("authorization", "")
        scheme, separator, supplied = authorization.partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not compare_digest(supplied, token)
        ):
            raise HTTPException(
                status_code=401,
                detail="A valid bearer token is required to access this private Studio API.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return {
        "authenticated": True,
        "auth_required": bool(token),
        "authentication": "bearer_token" if token else "local_development",
    }
