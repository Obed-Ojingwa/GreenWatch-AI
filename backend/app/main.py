from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes import files, investigations, runs
from app.core.config import get_settings, production_problems
from app.core.errors import DomainError
from app.db.session import SessionLocal, engine, init_dev_db
from app.services.agent import mark_stale_runs


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    problems = production_problems(s)
    if problems:
        raise RuntimeError("Refusing to start in production: " + " ".join(problems))
    if engine.url.get_backend_name() == "sqlite" or s.auto_create_tables:
        init_dev_db()  # otherwise apply db/schema.sql in Supabase once (it also enables RLS)
    db = SessionLocal()
    try:
        mark_stale_runs(db, s.stale_run_minutes)  # background tasks die on restarts
    except Exception:  # noqa: BLE001  (never block startup on housekeeping)
        pass
    finally:
        db.close()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="GreenWatch AI", version="0.1.0", lifespan=lifespan)
    app.state.limiter = runs.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.frontend_origin.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "X-Demo-Key"],
    )

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content={"error": type(exc).__name__, "detail": exc.detail})

    @app.get("/health")
    def health():
        return {"status": "ok", "environment": s.environment}

    app.include_router(investigations.router)
    app.include_router(runs.router)
    app.include_router(files.router)
    return app


app = create_app()
