#!/usr/bin/env python3
"""
Scope API — FastAPI wrapper around jpt.db.
Run from the Scope/ directory:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import alerts, chat, members, tickers


app = FastAPI(
    title="Scope Political Intelligence API",
    description="Real-time alerts on congressional trading, insider activity, lobbying, and regulatory proposals.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(alerts.router,  prefix="/alerts",  tags=["Alerts"])
app.include_router(chat.router,    prefix="/chat",    tags=["Chat"])
app.include_router(members.router, prefix="/members", tags=["Members"])
app.include_router(tickers.router, prefix="/tickers", tags=["Tickers"])


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "service": "Scope API"}
