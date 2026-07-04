from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "environment": settings.environment,
        "region": settings.region,
        "tmdb_configured": settings.tmdb_configured,
    }
