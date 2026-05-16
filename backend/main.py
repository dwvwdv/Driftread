from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from migrate import run_migrations
from routers import admin, articles, feeds, recommendations

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    yield


app = FastAPI(
    title="Driftread API",
    description="RSS 推薦平台 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS_ORIGINS defaults to "*" (open) — restrict to specific origins in production.
# Note: credentials (cookies) cannot be used with wildcard origins per the CORS spec.
_cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(feeds.router, prefix="/api")
app.include_router(articles.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")
app.include_router(admin.router, prefix="/api")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
