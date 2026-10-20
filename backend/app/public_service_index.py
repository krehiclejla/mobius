"""Which ids name a public app service, readable without a query per request.

The anonymous service door must refuse an over-limit caller, and answer a
probe of a made-up id, without a database lookup each time. It reads this
snapshot instead: every live id (stable service id, transition alias, or a
legacy slug) whose accepted contract is public, with that service's rate
bucket and allowance.

Every committed change to an app or a service alias discards the snapshot,
so an applied, moved, removed, or re-accepted service (including a changed
allowance) counts from its next request. The API process is the only writer
of those rows, so no other invalidation is needed.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import app_services, commit_watch, models
from app.manifest_contract import PUBLIC_SERVICE_REQUESTS_PER_MINUTE


PROBE_BUCKET = "unknown"

# Advanced by every committed service change. A snapshot built from the
# generation it read stays current until the next change, including one that
# commits from another thread while the snapshot is being built.
_generation = 0
_snapshot: tuple[int, dict[str, tuple[str, int]]] | None = None


def bucket(app, service: dict) -> tuple[str, int]:
  """The rate bucket and per-minute allowance one request is counted in."""
  if app is None:
    return PROBE_BUCKET, app_services.public_requests_per_minute({})
  return str(app.id), app_services.public_requests_per_minute(service)


def lookup(db: Session, service_id: str) -> tuple[str, int] | None:
  """The bucket and allowance of the public service ``service_id`` names."""
  global _snapshot
  generation = _generation
  if _snapshot is None or _snapshot[0] != generation:
    _snapshot = (generation, _build(db))
  return _snapshot[1].get(service_id)


def _build(db: Session) -> dict[str, tuple[str, int]]:
  # Mirrors routes.app_services._service_app's precedence: a stable service id
  # wins over a transition alias, which wins over a legacy slug.
  public: dict[int, tuple[str, int]] = {}
  # Plain columns, not App entities: no stored source is loaded, and the
  # request's session keeps no partially loaded app.
  apps = (
    db.query(
      models.App.id, models.App.slug, models.App.service_id,
      models.App.capability_contract,
    )
    .filter(models.App.deleted_at.is_(None))
    .all()
  )
  for app in apps:
    try:
      public[app.id] = bucket(app, app_services.service_contract(app, access="public"))
    except app_services.ServiceBoundaryError:
      # Public, but its accepted declaration is unreadable: keep it under its
      # own bucket so the door resolves it and answers 503, as the other
      # service doors do, instead of treating it as an unknown id.
      public[app.id] = (str(app.id), PUBLIC_SERVICE_REQUESTS_PER_MINUTE)
    except HTTPException:
      continue  # Not a public service.
  index: dict[str, tuple[str, int]] = {}
  for app in apps:
    if app.service_id is None and app.id in public:
      index[app.slug] = public[app.id]
  aliases = db.query(models.AppServiceAlias.service_id, models.AppServiceAlias.app_id)
  for alias_id, app_id in aliases.all():
    if app_id in public:
      index[alias_id] = public[app_id]
  for app in apps:
    if app.service_id is not None and app.id in public:
      index[app.service_id] = public[app.id]
  return index


def _services_changed() -> None:
  global _generation
  _generation += 1


commit_watch.on_committed_change(
  "public_services",
  {models.App.__tablename__, models.AppServiceAlias.__tablename__},
  _services_changed,
  inserts=True,
)
