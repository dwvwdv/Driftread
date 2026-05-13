from __future__ import annotations
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import admin, articles, feeds, recommendations

app = FastAPI(
    title="Driftread API",
    description="RSS 推薦平台 API",
    version="1.0.0",
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
