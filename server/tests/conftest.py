import pytest
from fastapi.testclient import TestClient

from dooh_server.config import Settings
from dooh_server.db import session_factory
from dooh_server.main import create_app
from dooh_server.models import Account, Membership, User
from dooh_server.security import hash_password

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        secret_key="test-secret-key-that-is-long-enough-123",
    )
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def db(client):
    s = session_factory()()
    yield s
    s.close()


@pytest.fixture
def world(client, db):
    """Agency -> {Client A -> Store A1, Client B} with users at each level."""
    agency = Account(name="Agency")
    db.add(agency)
    db.flush()
    a = Account(name="Client A", parent_id=agency.id)
    b = Account(name="Client B", parent_id=agency.id)
    db.add_all([a, b])
    db.flush()
    a1 = Account(name="Store A1", parent_id=a.id)
    db.add(a1)
    db.flush()

    def user(email, account=None, role="viewer", superadmin=False):
        u = User(email=email, password_hash=hash_password(PASSWORD), is_superadmin=superadmin)
        db.add(u)
        db.flush()
        if account is not None:
            db.add(Membership(user_id=u.id, account_id=account.id, role=role))
        return u

    user("root@x.test", superadmin=True)
    user("agency@x.test", agency, "admin")
    user("a-admin@x.test", a, "admin")
    user("a-viewer@x.test", a, "viewer")
    user("b-editor@x.test", b, "editor")
    db.commit()
    return {"agency": agency.id, "a": a.id, "b": b.id, "a1": a1.id}


def login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
