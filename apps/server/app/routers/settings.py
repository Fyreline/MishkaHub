"""Household settings — docs/API.md §Phase 2/6 "Settings & admin".

GET/PUT /api/settings/subscriptions   read/write the Subscription table
GET     /api/providers?region=GB      proxy TMDBClient's provider catalogue
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.tmdb import TMDBClient, TMDBError
from ..db import get_session
from ..errors import MishkaHTTPException
from ..models import Subscription

router = APIRouter(tags=["settings"])


def _serialize_subscription(sub: Subscription) -> dict:
    return {
        "provider_id": sub.provider_id,
        "provider_name": sub.provider_name,
        "logo": TMDBClient.poster_url(sub.logo_path, size="small") if sub.logo_path else None,
        "monthly_cost_pence": sub.monthly_cost_pence,
        "active": bool(sub.active),
    }


@router.get("/settings/subscriptions")
async def get_subscriptions(session: Session = Depends(get_session)) -> dict:
    subs = session.scalars(select(Subscription).where(Subscription.active == 1)).all()
    return {"subscriptions": [_serialize_subscription(s) for s in subs]}


class SubscriptionIn(BaseModel):
    provider_id: int
    monthly_cost_pence: int | None = None


class PutSubscriptionsBody(BaseModel):
    subscriptions: list[SubscriptionIn]


@router.put("/settings/subscriptions")
async def put_subscriptions(
    body: PutSubscriptionsBody,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Replace the household subscription list.

    Names/logos are resolved from the TMDB provider catalogue when
    available (docs/API.md: "echoes with names/logos resolved from the
    provider catalogue"); if TMDB is unconfigured or the lookup fails, the
    provider_id is kept with a placeholder name rather than erroring the
    whole request, since the local subscriptions table is still the source
    of truth for "which services do we pay for."
    """
    tmdb: TMDBClient = request.app.state.tmdb

    catalogue_by_id: dict[int, dict] = {}
    try:
        catalogue = await tmdb.watch_providers_catalogue()
        catalogue_by_id = {p["provider_id"]: p for p in catalogue}
    except TMDBError:
        catalogue_by_id = {}

    # Replace: deactivate everything not in the new list, upsert the rest.
    existing = {s.provider_id: s for s in session.scalars(select(Subscription)).all()}
    incoming_ids = {s.provider_id for s in body.subscriptions}

    for provider_id, sub in existing.items():
        if provider_id not in incoming_ids:
            sub.active = 0

    for item in body.subscriptions:
        info = catalogue_by_id.get(item.provider_id, {})
        row = existing.get(item.provider_id)
        fallback_name = row.provider_name if row is not None else f"Provider {item.provider_id}"
        name = info.get("provider_name") or fallback_name
        logo_path = info.get("logo_path")

        if row is None:
            row = Subscription(
                provider_id=item.provider_id,
                provider_name=name,
                logo_path=logo_path,
                monthly_cost_pence=item.monthly_cost_pence,
                active=1,
            )
            session.add(row)
        else:
            row.provider_name = name
            row.logo_path = logo_path or row.logo_path
            row.monthly_cost_pence = item.monthly_cost_pence
            row.active = 1

    session.commit()

    subs = session.scalars(select(Subscription).where(Subscription.active == 1)).all()
    return {"subscriptions": [_serialize_subscription(s) for s in subs]}


@router.get("/providers")
async def get_providers(
    request: Request,
    region: str = Query(default="GB"),
) -> dict:
    tmdb: TMDBClient = request.app.state.tmdb
    try:
        catalogue = await tmdb.watch_providers_catalogue(region=region)
    except TMDBError as exc:
        raise MishkaHTTPException(
            status_code=502, detail=str(exc), code="tmdb_upstream_error"
        ) from exc

    providers = [
        {
            "provider_id": p.get("provider_id"),
            "provider_name": p.get("provider_name"),
            "logo": TMDBClient.poster_url(p.get("logo_path"), size="small"),
            "display_priority": p.get("display_priorities", {}).get(region)
            if isinstance(p.get("display_priorities"), dict)
            else p.get("display_priority"),
        }
        for p in catalogue
    ]
    return {"region": region, "count": len(providers), "providers": providers}
