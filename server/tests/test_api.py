import uuid
from datetime import datetime, timedelta, timezone

from conftest import login


def make_screen(client, headers, account_id, name="Screen"):
    r = client.post("/api/v1/screens", headers=headers, json={"account_id": account_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def event(start: datetime, **kw):
    e = {
        "event_id": str(uuid.uuid4()),
        "started_at": start.isoformat(),
        "ended_at": (start + timedelta(seconds=kw.get("dwell", 5))).isoformat(),
        "dwell_seconds": kw.pop("dwell", 5),
        "attention_seconds": 0,
        "viewed": False,
    }
    e.update(kw)
    return e


# --- auth -------------------------------------------------------------------


def test_login_and_me(client, world):
    h = login(client, "a-admin@x.test")
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["email"] == "a-admin@x.test"
    assert me["memberships"][0]["role"] == "admin"
    assert client.post("/api/v1/auth/login", json={"email": "a-admin@x.test", "password": "nope"}).status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


# --- tenancy ----------------------------------------------------------------


def test_account_visibility_follows_tree(client, world):
    names = lambda h: {a["name"] for a in client.get("/api/v1/accounts", headers=h).json()}  # noqa: E731
    assert names(login(client, "root@x.test")) == {"Agency", "Client A", "Client B", "Store A1"}
    assert names(login(client, "agency@x.test")) == {"Agency", "Client A", "Client B", "Store A1"}
    assert names(login(client, "a-viewer@x.test")) == {"Client A", "Store A1"}
    assert names(login(client, "b-editor@x.test")) == {"Client B"}


def test_client_cannot_see_or_touch_other_client(client, world):
    agency = login(client, "agency@x.test")
    screen_b = make_screen(client, agency, world["b"], "B screen")
    a = login(client, "a-admin@x.test")
    assert client.get(f"/api/v1/screens/{screen_b['id']}", headers=a).status_code == 404
    assert client.delete(f"/api/v1/screens/{screen_b['id']}", headers=a).status_code == 404
    assert client.get(f"/api/v1/accounts/{world['b']}/members", headers=a).status_code == 404
    assert client.get(f"/api/v1/analytics/summary?account_id={world['b']}", headers=a).status_code == 404
    assert client.post("/api/v1/screens", headers=a, json={"account_id": world["b"], "name": "x"}).status_code == 404
    # Moving one of A's screens into B is also refused.
    screen_a = make_screen(client, a, world["a"])
    r = client.patch(f"/api/v1/screens/{screen_a['id']}", headers=a, json={"account_id": world["b"]})
    assert r.status_code == 404
    # Listing all screens only returns A's.
    assert [s["name"] for s in client.get("/api/v1/screens", headers=a).json()] == ["Screen"]


def test_roles(client, world):
    viewer = login(client, "a-viewer@x.test")
    assert client.post("/api/v1/screens", headers=viewer, json={"account_id": world["a"], "name": "x"}).status_code == 403
    assert client.get(f"/api/v1/accounts/{world['a']}/members", headers=viewer).status_code == 403
    editor = login(client, "b-editor@x.test")
    make_screen(client, editor, world["b"])
    assert client.post("/api/v1/accounts", headers=editor, json={"name": "x", "parent_id": world["b"]}).status_code == 403


def test_subaccount_and_member_management(client, world):
    a = login(client, "a-admin@x.test")
    r = client.post("/api/v1/accounts", headers=a, json={"name": "Store A2", "parent_id": world["a"]})
    assert r.status_code == 201
    a2 = r.json()
    assert a2["path"] == "Agency / Client A / Store A2"
    # Client admins cannot create top-level accounts or delete their own account.
    assert client.post("/api/v1/accounts", headers=a, json={"name": "Rogue"}).status_code == 403
    assert client.delete(f"/api/v1/accounts/{world['a']}", headers=a).status_code == 403
    # Invite a store manager who only sees Store A2.
    r = client.post(
        f"/api/v1/accounts/{a2['id']}/members",
        headers=a,
        json={"email": "Store2@X.test", "role": "viewer", "password": "store-two-password"},
    )
    assert r.status_code == 201, r.text
    store = client.post("/api/v1/auth/login", json={"email": "store2@x.test", "password": "store-two-password"})
    sh = {"Authorization": f"Bearer {store.json()['access_token']}"}
    assert [x["name"] for x in client.get("/api/v1/accounts", headers=sh).json()] == ["Store A2"]
    # Parent accounts are not leaked through parent_id.
    assert client.get("/api/v1/accounts", headers=sh).json()[0]["parent_id"] is None
    # Remove and the access is gone.
    uid = r.json()["user_id"]
    assert client.delete(f"/api/v1/accounts/{a2['id']}/members/{uid}", headers=a).status_code == 204
    assert client.get("/api/v1/accounts", headers=sh).json() == []
    # Deleting the now-empty sub-account works for the parent's admin.
    assert client.delete(f"/api/v1/accounts/{a2['id']}", headers=a).status_code == 204


def test_new_member_requires_password(client, world):
    a = login(client, "a-admin@x.test")
    r = client.post(f"/api/v1/accounts/{world['a']}/members", headers=a, json={"email": "new@x.test"})
    assert r.status_code == 422


# --- devices & ingest -------------------------------------------------------


def test_ingest_dedupes_and_scopes_to_screen_account(client, world):
    a = login(client, "a-admin@x.test")
    screen = make_screen(client, a, world["a1"], "Store screen")
    dev = {"X-Device-Key": screen["device_key"]}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    events = [event(now - timedelta(minutes=i), viewed=i % 2 == 0, attention_seconds=2.0 if i % 2 == 0 else 0,
                    age_bucket="25-34", gender="female", has_bag=True, brand="Acme" if i < 3 else None)
              for i in range(10)]
    r = client.post("/api/v1/ingest/events", headers=dev, json={"events": events})
    assert r.json() == {"accepted": 10, "duplicates": 0}
    r = client.post("/api/v1/ingest/events", headers=dev, json={"events": events[:4]})
    assert r.json() == {"accepted": 0, "duplicates": 4}

    # Client A (parent of Store A1) sees the data; client B does not.
    summary = client.get(f"/api/v1/analytics/summary?account_id={world['a']}", headers=a).json()
    assert summary["totals"]["impressions"] == 10
    assert summary["totals"]["viewers"] == 5
    assert summary["totals"]["branded_bag_carriers"] == 3
    assert summary["brands"][0] == {"brand": "Acme", "carriers": 3, "viewers": 2, "view_rate": 0.6667,
                                   "avg_attention_seconds": 2.0}
    b = login(client, "b-editor@x.test")
    assert client.get("/api/v1/analytics/summary", headers=b).json()["totals"]["impressions"] == 0


def test_device_auth(client, world):
    a = login(client, "a-admin@x.test")
    screen = make_screen(client, a, world["a"])
    ev = {"events": [event(datetime.now(timezone.utc))]}
    assert client.post("/api/v1/ingest/events", json=ev).status_code == 401
    assert client.post("/api/v1/ingest/events", headers={"X-Device-Key": "dooh_bad_key"}, json=ev).status_code == 401
    forged = screen["device_key"][:-4] + "AAAA"
    assert client.post("/api/v1/ingest/events", headers={"X-Device-Key": forged}, json=ev).status_code == 401
    # Rotating the key invalidates the old one.
    new = client.post(f"/api/v1/screens/{screen['id']}/rotate-key", headers=a).json()["device_key"]
    assert client.post("/api/v1/ingest/events", headers={"X-Device-Key": screen["device_key"]}, json=ev).status_code == 401
    assert client.post("/api/v1/ingest/events", headers={"X-Device-Key": new}, json=ev).status_code == 200
    # Disabled screens are refused.
    client.patch(f"/api/v1/screens/{screen['id']}", headers=a, json={"is_active": False})
    assert client.post("/api/v1/ingest/events", headers={"X-Device-Key": new}, json=ev).status_code == 403


def test_ingest_validation(client, world):
    a = login(client, "a-admin@x.test")
    dev = {"X-Device-Key": make_screen(client, a, world["a"])["device_key"]}
    bad = event(datetime.now(timezone.utc), age_bucket="20-30")
    assert client.post("/api/v1/ingest/events", headers=dev, json={"events": [bad]}).status_code == 422


def test_heartbeat_marks_screen_online(client, world):
    a = login(client, "a-admin@x.test")
    screen = make_screen(client, a, world["a"])
    assert client.get(f"/api/v1/screens/{screen['id']}", headers=a).json()["online"] is False
    r = client.post("/api/v1/ingest/heartbeat", headers={"X-Device-Key": screen["device_key"]},
                    json={"agent_version": "0.1.0", "status": {"people_now": 3, "camera_ok": True}})
    assert r.status_code == 204
    s = client.get(f"/api/v1/screens/{screen['id']}", headers=a).json()
    assert s["online"] is True and s["last_status"]["people_now"] == 3 and s["agent_version"] == "0.1.0"


def test_brands_inherit_down_the_tree(client, world):
    agency = login(client, "agency@x.test")
    r = client.post(f"/api/v1/accounts/{world['agency']}/brands", headers=agency,
                    json={"name": "Northwind", "colors": [{"lo": [5, 100, 100], "hi": [20, 255, 255]}]})
    assert r.status_code == 201
    brand_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = client.post(f"/api/v1/brands/{brand_id}/templates", headers=agency,
                    files={"file": ("logo.png", png, "image/png")})
    assert r.status_code == 201
    a = login(client, "a-admin@x.test")
    client.post(f"/api/v1/accounts/{world['a']}/brands", headers=a, json={"name": "Client A House Brand"})

    brands = client.get(f"/api/v1/accounts/{world['a1']}/brands", headers=a).json()
    assert {(b["name"], b["inherited"]) for b in brands} == {("Northwind", True), ("Client A House Brand", True)}
    # Client A cannot edit the agency's brand.
    assert client.delete(f"/api/v1/brands/{brand_id}", headers=a).status_code == 404

    screen = make_screen(client, a, world["a1"])
    dev = {"X-Device-Key": screen["device_key"]}
    cfg = client.get("/api/v1/ingest/config", headers=dev).json()
    assert {b["name"] for b in cfg["brands"]} == {"Northwind", "Client A House Brand"}
    tpl = [b for b in cfg["brands"] if b["name"] == "Northwind"][0]["templates"][0]
    assert client.get(tpl["url"], headers=dev).content == png

    # Client B's screens get the agency brand but not Client A's.
    b = login(client, "b-editor@x.test")
    dev_b = {"X-Device-Key": make_screen(client, b, world["b"])["device_key"]}
    assert {x["name"] for x in client.get("/api/v1/ingest/config", headers=dev_b).json()["brands"]} == {"Northwind"}


def test_analytics_timeseries_and_export(client, world):
    a = login(client, "a-admin@x.test")
    screen = make_screen(client, a, world["a"])
    dev = {"X-Device-Key": screen["device_key"]}
    day = datetime(2026, 3, 10, tzinfo=timezone.utc)
    evs = [event(day + timedelta(hours=9, minutes=i), viewed=True, attention_seconds=3) for i in range(3)]
    evs += [event(day + timedelta(hours=14), viewed=False)]
    client.post("/api/v1/ingest/events", headers=dev, json={"events": evs})

    r = client.get("/api/v1/analytics/summary?start=2026-03-10&end=2026-03-10&tz=UTC", headers=a).json()
    ts = r["timeseries"]
    assert ts["granularity"] == "hour" and len(ts["points"]) == 24
    assert ts["points"][9] == {"t": "2026-03-10T09:00", "impressions": 3, "viewers": 3, "attention_seconds": 9.0}
    assert ts["hour_of_day"][14]["impressions"] == 1
    assert r["totals"]["avg_attention_seconds"] == 3.0

    # Local-time bucketing: 09:00 UTC is 10:00 in Paris (CET, before DST).
    r = client.get("/api/v1/analytics/summary?start=2026-03-10&end=2026-03-10&tz=Europe/Paris", headers=a).json()
    assert r["timeseries"]["hour_of_day"][10]["impressions"] == 3

    csv = client.get("/api/v1/analytics/export.csv?start=2026-03-10&end=2026-03-10", headers=a).text
    lines = csv.strip().splitlines()
    assert lines[0].startswith("started_at_utc,") and len(lines) == 5

    assert client.get("/api/v1/analytics/summary?start=2026-03-11&end=2026-03-10", headers=a).status_code == 422
    assert client.get("/api/v1/analytics/summary?tz=Mars/Base", headers=a).status_code == 422


def test_daily_buckets_across_dst_change(client, world):
    a = login(client, "a-admin@x.test")
    # Europe/Paris leaves DST on 2026-10-25 (a 25-hour day).
    r = client.get("/api/v1/analytics/summary?start=2026-10-20&end=2026-10-30&tz=Europe/Paris", headers=a)
    assert r.status_code == 200
    days = [p["t"] for p in r.json()["timeseries"]["points"]]
    assert days[0] == "2026-10-20" and days[-1] == "2026-10-30" and len(days) == 11
    r = client.get("/api/v1/analytics/summary?start=2026-10-25&end=2026-10-25&tz=Europe/Paris", headers=a)
    assert len(r.json()["timeseries"]["points"]) == 24  # 02:00 occurs twice but is one bucket


def test_dashboard_served(client):
    assert "DOOH Audience Analytics" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/healthz").json()["status"] == "ok"
