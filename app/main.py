from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import router
from app.api.support import router as support_router
from app.config import Settings
from app.db import build_engine
from app.services.customer import CustomerError


ERRORS = {
    "conflict": (409, "Request cannot be executed again"),
    "unauthorized": (401, "Authentication required"),
    "not_found": (404, "Resource not found"),
    "service_unavailable": (503, "Database unavailable"),
}


def error_response(code: str):
    status, message = ERRORS[code]
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        status_code=status,
        content={"detail": {"code": code, "message": message}},
        headers=headers,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = build_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Customer Support Backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.include_router(router)
    app.include_router(support_router)

    @app.exception_handler(CustomerError)
    async def customer_error(request, exc):
        return error_response(exc.code)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return error_response("service_unavailable")

    return app
