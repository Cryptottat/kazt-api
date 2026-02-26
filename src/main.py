import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

from src.routes import auth, rules, templates
from src.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Kazt API starting up...")
    yield
    logger.info("Kazt API shutting down...")


app = FastAPI(
    title="Kazt API",
    description="ACE Rule Builder API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(rules.router, prefix="/api/rules", tags=["rules"])
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "kazt-api", "version": "0.1.0"}


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "data": None,
            "message": "Internal server error",
            "error": "INTERNAL_ERROR",
        },
    )
