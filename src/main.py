"""
Kazt API -- FastAPI application entry point.
ACE Rule Builder backend service.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import config
from src.routes import auth, rules, templates, deploy, chain
from src.utils.logger import logger
from src.database import init_database, close_database, get_pool
from src.cache import init_redis, close_redis, get_redis
from src.services.solana_service import solana_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle. Each init is graceful -- one failure doesn't block the rest."""
    logger.info("Kazt API starting up...")

    # Database (graceful -- works without DB via in-memory fallback)
    try:
        await init_database()
        logger.info("Database connected.")
    except Exception as e:
        logger.warning(f"Database init failed (will run without persistence): {e}")

    # Redis (graceful -- works without Redis via no-cache fallback)
    try:
        await init_redis()
        logger.info("Redis connected.")
    except Exception as e:
        logger.warning(f"Redis init failed (will run without cache): {e}")

    logger.info("Kazt API ready.")
    yield

    # Shutdown
    logger.info("Kazt API shutting down...")
    try:
        await solana_service.close()
    except Exception as e:
        logger.warning(f"Solana service cleanup error: {e}")
    try:
        await close_redis()
    except Exception as e:
        logger.warning(f"Redis cleanup error: {e}")
    try:
        await close_database()
    except Exception as e:
        logger.warning(f"Database cleanup error: {e}")
    logger.info("Kazt API stopped.")


app = FastAPI(
    title="Kazt API",
    description="ACE Rule Builder API -- visual rule design, simulation, and deployment for Solana protocols.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS -- use config instead of direct os.getenv
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in config.CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(rules.router, prefix="/api/rules", tags=["rules"])
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])
app.include_router(deploy.router, prefix="/api/deploy", tags=["deploy"])
app.include_router(chain.router, prefix="/api/chain", tags=["chain"])


@app.get("/health")
async def health():
    """Health check with dependency status. Used by load balancers and monitoring."""
    db_connected = get_pool() is not None
    redis_connected = get_redis() is not None

    return {
        "status": "ok",
        "service": "kazt-api",
        "version": "0.1.0",
        "dependencies": {
            "database": "connected" if db_connected else "disconnected",
            "redis": "connected" if redis_connected else "disconnected",
        },
    }


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=config.PORT,
        reload=config.DEBUG,
    )
