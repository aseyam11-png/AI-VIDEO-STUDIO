from fastapi import APIRouter

from app.api.routes.jobs import router as jobs_router
from app.api.routes.media import router as media_router
from app.api.routes.models import router as models_router
from app.api.routes.projects import router as projects_router

api_router = APIRouter()
api_router.include_router(projects_router)
api_router.include_router(media_router)
api_router.include_router(jobs_router)
api_router.include_router(models_router)
