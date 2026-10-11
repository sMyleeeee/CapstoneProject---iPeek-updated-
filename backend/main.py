import sys
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import init_db
from config import CORS_ORIGINS
from routers import admin_routes, ai_routes, auth_routes, kiosk_routes, research_routes

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="iPeek: Centralized Digital Repository API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    await init_db()


app.include_router(auth_routes.router)
app.include_router(research_routes.router)
app.include_router(admin_routes.router)
app.include_router(ai_routes.router)
app.include_router(kiosk_routes.router)


@app.get("/")
async def root():
    return {"message": "iPeek API is running"}