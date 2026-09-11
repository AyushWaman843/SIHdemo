"""Compatibility entry point for the CYPHER v2 FastAPI server."""

from backend.app.main import app

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host="127.0.0.1",
        port=int(__import__("os").getenv("PORT", "5000")),
        reload=False,
    )
