"""
FastAPI application for Polymarket ML Data Collector.

This API provides monitoring endpoints for:
- System health
- Collection statistics
- Market data
- Task status
- Data quality metrics
- Executor management
- Strategy configuration
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

from src.api.routes import health, stats, markets, tasks, data_quality, monitoring, database, categorization
from src.api.routes import executor, strategies, executor_config, executor_ws, csgo, grid
from src.csgo.engine.api import router as csgo_engine_router

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
app.include_router(categorization.router, prefix="/api", tags=["Categorization"])

# Executor routers
app.include_router(executor.router, prefix="/api", tags=["Executor"])
app.include_router(strategies.router, prefix="/api", tags=["Strategies"])
app.include_router(executor_config.router, prefix="/api", tags=["Executor Config"])
app.include_router(executor_ws.router, prefix="/api", tags=["Executor WebSocket"])
app.include_router(csgo.router, prefix="/api", tags=["CS:GO Strategy"])
app.include_router(csgo_engine_router, prefix="/api", tags=["CS:GO Engine"])
app.include_router(grid.router, prefix="/api", tags=["GRID Integration"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Polymarket ML Data Collector",
        "version": "1.0.0",
        "docs": "/docs",
    }
