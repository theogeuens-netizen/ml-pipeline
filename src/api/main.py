"""
FastAPI application for Polymarket ML Data Collector.

This API provides monitoring endpoints for:
- System health
- Collection statistics
- Market data
- Task status
- Data quality metrics
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

from src.api.routes import health, stats, markets, tasks, data_quality, monitoring, database

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Polymarket ML API")
    yield
    logger.info("Shutting down Polymarket ML API")


app = FastAPI(
    title="Polymarket ML Data Collector",
    description="Data collection and monitoring API for Polymarket ML trading system",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(stats.router, prefix="/api", tags=["Stats"])
app.include_router(markets.router, prefix="/api", tags=["Markets"])
app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
app.include_router(data_quality.router, prefix="/api", tags=["Data Quality"])
app.include_router(monitoring.router, prefix="/api", tags=["Monitoring"])
app.include_router(database.router, prefix="/api", tags=["Database"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Polymarket ML Data Collector",
        "version": "1.0.0",
        "docs": "/docs",
    }
