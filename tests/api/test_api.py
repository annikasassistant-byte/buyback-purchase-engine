"""API integration tests - hit the real FastAPI app, the real engine, and a
real Postgres (Neon in dev; the `DATABASE_URL`/`DATABASE_URL_POOLED` CI
secrets in CI - see ``.github/workflows/ci.yml``). Needs `DATABASE_URL`; not
marked ``golden`` on purpose - unlike ``tests/golden`` (excluded from CI
because that test's whole point is pinning exact scores against the checked-in
workbook and is deliberately slow to keep out of the fast path), this suite
*should* run in CI: it's what actually proves the API works, and both the
workbook and a database are available there.

One real engine run is triggered once per module (module-scoped fixtures) and
every read/allocate/action test reuses it - a full pipeline run is not free,
no reason to pay for it seven times.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Neon) - not set here"
)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from purchase_engine.api.app import app
    from purchase_engine.api.settings import get_settings

    headers = {}
    api_key = get_settings().api_key
    if api_key is not None:
        headers["X-API-Key"] = api_key
    with TestClient(app, headers=headers) as c:
        yield c


@pytest.fixture(scope="module")
def triggered_run(client):
    """One real ``POST /runs`` for the whole module; the row is deleted
    (cascading to its recommendations/actions) when the last test is done."""
    resp = client.post("/runs", json={"budget_eur": 500})
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["run_id"]
    try:
        yield run_id
    finally:
        import psycopg

        from purchase_engine.adapters.store import dsn_from_env

        dsn = dsn_from_env()
        assert dsn is not None
        with psycopg.connect(dsn) as cx, cx.cursor() as cur:
            cur.execute("delete from engine_run where run_id = %s", (run_id,))


def test_health_needs_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_wrong_api_key_is_rejected_when_configured(client):
    from purchase_engine.api.settings import get_settings

    if get_settings().api_key is None:
        pytest.skip("API_KEY not set in this environment - auth is disabled by design")
    resp = client.get("/runs/latest", headers={"X-API-Key": "definitely-wrong"})
    assert resp.status_code == 401


def test_trigger_run_persists_and_is_readable(client, triggered_run):
    run_id = triggered_run

    got = client.get(f"/runs/{run_id}")
    assert got.status_code == 200
    assert got.json()["run_id"] == run_id
    assert got.json()["budget_eur"] == 500.0

    assert client.get("/runs/latest").status_code == 200


def test_recommendations_filtered_by_label(client, triggered_run):
    resp = client.get(f"/runs/{triggered_run}/recommendations", params={"label": "BUY"})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) > 0  # the sample workbook has real BUYs at this budget
    assert all(r["label"] == "BUY" for r in rows)
    assert "reasons" in rows[0]["payload"]


def test_allocate_reranks_live_without_a_full_rerun(client, triggered_run):
    resp = client.post(f"/runs/{triggered_run}/allocate", json={"budget_eur": 10.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == triggered_run
    assert body["budget_eur"] == 10.0
    funded = [line for line in body["lines"] if line["final_qty"] > 0]
    assert len(funded) < len(body["lines"])  # 10 EUR can't fund every line


def test_actions_round_trip(client, triggered_run):
    recs = client.get(f"/runs/{triggered_run}/recommendations", params={"label": "BUY"}).json()
    produkt_id = recs[0]["produkt_id"]

    posted = client.post(
        "/actions",
        json={"run_id": triggered_run, "produkt_id": produkt_id, "action": "BUY", "qty": 1},
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["action"] == "BUY"

    listed = client.get("/actions", params={"run_id": triggered_run})
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_unknown_run_is_404(client):
    assert client.get("/runs/does-not-exist").status_code == 404
    resp = client.post("/runs/does-not-exist/allocate", json={"budget_eur": 1})
    assert resp.status_code == 404
