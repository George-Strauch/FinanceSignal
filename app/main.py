"""FinanceSignal API — FastAPI application entry point."""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.routers.system import router as system_router
from app.routers.tickers import router as tickers_router
from app.routers.posts import router as posts_router
from app.routers.subreddits import router as subreddits_router
from app.routers.processes import router as processes_router
from app.routers.market import router as market_router
from app.routers.mentions import router as mentions_router
from app.routers.ticker_tags import router as ticker_tags_router

app_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_start_time
    app_start_time = time.time()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
@app.get("/api")
async def root():
    return {"name": "FinanceSignal API", "version": "1.0.0"}
