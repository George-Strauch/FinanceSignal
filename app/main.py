"""FinanceSignal API — FastAPI application entry point."""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.routers.system import router as system_router
from app.routers.tickers import router as tickers_router

app_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_start_time
    app_start_time = time.time()
    yield


app = FastAPI(
    title="FinanceSignal API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(system_router)
app.include_router(tickers_router)

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
