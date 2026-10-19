"""FastAPI Application Factory for Ledgerly."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.database import engine, admin_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for connection pool lifecycle."""
    yield
    # Cleanup connection pools on shutdown
    await engine.dispose()
    await admin_engine.dispose()


def create_application() -> FastAPI:
    """Initialize and configure the FastAPI application."""
    app = FastAPI(
        title="Ledgerly API",
        description="Multi-tenant SaaS Accounting Platform API",
        version="0.1.0",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from app.services.posting_gateway import AccountingError

    @app.exception_handler(AccountingError)
    async def accounting_error_handler(request: Request, exc: AccountingError):
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc)},
        )

    # Mount API routes
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(api_router)  # Allow /health at root as well


    # Serve Demonstration Frontend at root
    import os
    from starlette.responses import HTMLResponse

    frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "index.html"))

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_demo_ui():
        if os.path.exists(frontend_path):
            with open(frontend_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(
            content="<html><body><h1>Ledgerly Core Accounting Spine API</h1><p>Visit <a href='/docs'>/docs</a> for OpenAPI specification.</p></body></html>"
        )

    return app


app = create_application()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
