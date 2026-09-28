"""An id beyond the database's 64-bit integer range is invalid input, not a crash."""

import pytest

HUGE = "99999999999999999999"


@pytest.mark.parametrize("path", [
  f"/api/apps/{HUGE}",
  f"/api/apps/{HUGE}/events",
  f"/api/storage/apps/{HUGE}/notes.json",
  f"/api/storage/apps-list/{HUGE}/",
  f"/api/github/contributions/{HUGE}/review-status",
])
def test_out_of_range_id_is_a_client_error(client, auth, path):
  response = client.get(path, headers=auth)

  assert response.status_code == 422, response.text
  assert response.json() == {"detail": "A number in this request is too large."}


def test_in_range_missing_id_is_still_not_found(client, auth):
  assert client.get("/api/apps/9223372036854775807", headers=auth).status_code == 404
