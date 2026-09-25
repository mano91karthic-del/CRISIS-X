import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.datasets import router as datasets_router
from app.api.digital_twin import router as digital_twin_router
from app.api.eo_change import router as eo_change_router
from app.api.exposure import router as exposure_router
from app.api.hazards import router as hazards_router
from app.api.projects import router as projects_router
from app.api.risk import router as risk_router
from app.api.routing import router as routing_router
from app.api.scenarios import router as scenarios_router
from app.api.terrain_packages import router as terrain_packages_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("crisis_x.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("CRISIS-X API starting up (env=%s)", settings.app_env)
    yield
    logger.info("CRISIS-X API shutting down")


app = FastAPI(
    title="CRISIS-X API",
    description="Disaster Digital Twin & Response Intelligence Platform — backend API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router)
app.include_router(datasets_router)
app.include_router(terrain_packages_router)
app.include_router(hazards_router)
app.include_router(eo_change_router)
app.include_router(exposure_router)
app.include_router(risk_router)
app.include_router(routing_router)
app.include_router(digital_twin_router)
app.include_router(scenarios_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "crisis-x-api", "env": settings.app_env}


@app.get("/")
async def root() -> dict:
    return {"message": "CRISIS-X API — see /docs for the interactive API reference."}
