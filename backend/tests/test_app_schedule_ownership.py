"""Owner timezone and schedule provenance across app updates."""

from app import app_cron
from app.app_cron import ScheduleChoice


def test_owner_timezone_is_recorded_validated_and_readable(client, auth):
  assert client.get("/api/owner/timezone", headers=auth).json() == {
    "timezone": None,
  }

  saved = client.put(
    "/api/owner/timezone", json={"timezone": "Asia/Tokyo"}, headers=auth,
  )
  rejected = client.put(
    "/api/owner/timezone", json={"timezone": "Not/AZone"}, headers=auth,
  )

  assert saved.status_code == 200, saved.text
  assert rejected.status_code == 400
  assert client.get("/api/owner/timezone", headers=auth).json() == {
    "timezone": "Asia/Tokyo",
  }


def test_owner_timezone_requires_the_owner(client):
  response = client.put("/api/owner/timezone", json={"timezone": "UTC"})
  assert response.status_code == 401


def _owner_daily(app_id: int, default: str = "30 5 * * *") -> None:
  app_cron.record_schedule_choice(app_id, ScheduleChoice(
    source="owner", cron="15 7 * * *", job="fetch.sh",
    timezone="Asia/Tokyo", manifest_default=default,
  ))


def test_owner_daily_time_survives_an_app_retiming_its_daily_default():
  _owner_daily(9101)

  kept = app_cron.owner_schedule_to_keep(9101, "0 6 * * *", "fetch.sh")

  assert kept == ScheduleChoice(
    source="owner", cron="15 7 * * *", job="fetch.sh",
    timezone="Asia/Tokyo", manifest_default="30 5 * * *",
  )


def test_changed_schedule_contract_releases_the_owner_choice():
  _owner_daily(9102)

  assert app_cron.owner_schedule_to_keep(
    9102, "*/10 * * * *", "fetch.sh",
  ) is None
  assert app_cron.owner_schedule_to_keep(
    9102, "30 5 * * *", "refresh.sh",
  ) is None


def test_non_daily_owner_choice_survives_only_an_unchanged_default():
  app_cron.record_schedule_choice(9103, ScheduleChoice(
    source="owner", cron="0 */2 * * *", job="job.sh",
    manifest_default="0 * * * *",
  ))

  assert app_cron.owner_schedule_to_keep(9103, "0 * * * *", "job.sh")
  assert app_cron.owner_schedule_to_keep(9103, "*/30 * * * *", "job.sh") is None


def test_manifest_default_is_never_mistaken_for_an_owner_choice():
  app_cron.record_schedule_choice(9104, ScheduleChoice(
    source="manifest", cron="30 5 * * *", job="fetch.sh",
    timezone="Asia/Tokyo", manifest_default="30 5 * * *",
  ))
  _write_zone_declaration(9104, "memory", "Asia/Tokyo", "30 5 * * *")

  assert app_cron.owner_schedule_to_keep(9104, "30 5 * * *", "fetch.sh") is None


def _write_zone_declaration(app_id, slug, zone, zone_cron):
  state = app_cron.schedule_state_dir(app_id)
  state.mkdir(parents=True, exist_ok=True)
  escaped = zone_cron.replace(" ", "\\ ").replace("*", "\\*")
  (state / "init-cron.sh").write_text(
    "#!/bin/sh\n"
    f'ENTRY="* * * * * API_BASE_URL=http://localhost:8000 python3 '
    f"/app/scripts/app-job-runner.py --scheduled --wall-clock {zone} "
    f'{escaped} {app_id} /data/apps/{slug}/fetch.sh"\n'
    "exit 0\n"
    f'\nSCHEDULE_TZ="{zone}"\nSCHEDULE_SOURCE="{zone_cron}"\n'
  )


def test_zone_declaration_from_before_provenance_is_the_owner_choice():
  """Before provenance, updates always reset defaults to server time, so a
  surviving timezone-owned declaration was set through the schedule route."""
  _write_zone_declaration(9105, "memory", "Asia/Tokyo", "30 5 * * *")

  kept = app_cron.owner_schedule_to_keep(9105, "30 5 * * *", "fetch.sh")

  assert kept == ScheduleChoice(
    source="owner", cron="30 5 * * *", job="fetch.sh",
    timezone="Asia/Tokyo",
  )


def test_server_time_declaration_without_provenance_stays_a_default():
  state = app_cron.schedule_state_dir(9106)
  state.mkdir(parents=True, exist_ok=True)
  (state / "init-cron.sh").write_text(
    '#!/bin/sh\nENTRY="0 6 * * * API_BASE_URL=http://localhost:8000 '
    'python3 /app/scripts/app-job-runner.py --scheduled 9106 '
    '/data/apps/reflection/fetch.sh"\n'
  )

  assert app_cron.owner_schedule_to_keep(9106, "0 6 * * *", "fetch.sh") is None
