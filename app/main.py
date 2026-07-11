"""FinanceSignal API — FastAPI application entry point."""

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import CORS_ORIGINS, PROJECT_ROOT
from app.routers.system import router as system_router
from app.routers.tickers import router as tickers_router
from app.routers.posts import router as posts_router
from app.routers.subreddits import router as subreddits_router
from app.routers.processes import router as processes_router
from app.routers.market import router as market_router
from app.routers.mentions import router as mentions_router
from app.routers.ticker_tags import router as ticker_tags_router
from app.routers.entities import router as entities_router
from app.routers.reddit_stats import router as reddit_stats_router
from app.routers.fundamentals import router as fundamentals_router
from app.routers.trading import router as trading_router
from app.routers.bots import router as bots_router

app_start_time: float = 0.0

FRONTEND_DIR = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_start_time
    app_start_time = time.time()
    # Discover bots and ensure strategies exist before starting processes
    from app.bot_engine.discovery import discover_bots, ensure_bot_strategies
    bots = discover_bots()
    if bots:
        ensure_bot_strategies(bots)
    from app.process_manager import process_manager
    process_manager.load_jobs()
    await process_manager.auto_start()
    yield


app = FastAPI(
    title="FinanceSignal API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(system_router)
app.include_router(tickers_router)
app.include_router(posts_router)
app.include_router(subreddits_router)
app.include_router(processes_router)
app.include_router(market_router)
app.include_router(mentions_router)
app.include_router(ticker_tags_router)
app.include_router(entities_router)
app.include_router(reddit_stats_router)
app.include_router(fundamentals_router)
app.include_router(trading_router)
app.include_router(bots_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api")
async def api_root():
    return {"name": "FinanceSignal API", "version": "1.0.0"}


# ── SPA static file serving ──────────────────────────────────────────
# Serves the built React frontend when frontend/dist/ exists.
# Must be mounted AFTER all API routers so /api/* routes take priority.

if FRONTEND_DIR.is_dir():
    from starlette.responses import FileResponse

    # Serve static assets (JS, CSS, images) at /assets/...
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIR / "assets"),
        name="static-assets",
    )

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Serve matching static file or fall back to index.html for SPA routing."""
        # Never intercept API paths
        if full_path.startswith("api/") or full_path == "api":
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        file = FRONTEND_DIR / full_path
        if full_path and file.is_file():
            return FileResponse(file)
        return FileResponse(FRONTEND_DIR / "index.html")
