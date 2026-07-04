"""Uniform error shape (docs/API.md §0): ``{"detail": ..., "code": ...}``.

FastAPI's default ``HTTPException`` only carries ``detail``. Routers that
need to surface a machine-readable ``code`` alongside the human message
raise ``MishkaHTTPException`` instead; ``main.py`` registers the exception
handler below so the JSON body matches the documented shape exactly.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


class MishkaHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MishkaHTTPException)
    async def _mishka_http_exception_handler(
        request: Request, exc: MishkaHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )
