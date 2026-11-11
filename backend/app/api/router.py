from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.media import router as media_router
from app.api.routes.models import router as models_router
from app.api.routes.projects import router as projects_router
from app.api.routes.tools import router as tools_router
from app.api.routes.workflows import router as workflows_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(projects_router)
api_router.include_router(media_router)
api_router.include_router(jobs_router)
api_router.include_router(models_router)
api_router.include_router(tools_router)
api_router.include_router(workflows_router)
