import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from leboset.api.app import create_app
from leboset.cli import provision_user


@pytest.fixture
def api(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    pg_url = os.environ.get("LEBOSET_TEST_POSTGRES_URL")
    admin = None
    schema = "test_" + uuid4().hex
    if pg_url:
        admin = create_engine(pg_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = (
            make_url(pg_url)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False)
        )
    try:
        monkeypatch.setenv("LEBOSET_DATABASE_URL", url)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
        app = create_app(url)
        with app.state.sessions() as db:
            alice, token_a = provision_user(db, "Alice")
            bob, token_b = provision_user(db, "Bob")
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {token_a}"
            yield client, app, f"Bearer {token_b}", alice.id, bob.id
    finally:
        if admin is not None:
            # Only the generated test schema is removed; never the shared public schema.
            with admin.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def add_item(client, role, name, confirmed=True):
    response = client.post(
        "/api/v1/wardrobe/items",
        json={
            "name": name,
            "role": role,
            "colors": ["black"],
        },
    )
    assert response.status_code == 201, response.text
    row = response.json()
    if confirmed:
        response = client.post(
            f"/api/v1/wardrobe/items/{row['id']}/confirm",
            json={
                "expected_version": row["version"],
            },
        )
        assert response.status_code == 200, response.text
        row = response.json()
    return row


@pytest.fixture
def wardrobe(api):
    client = api[0]
    return {
        name: add_item(client, role, name)
        for name, role in (
            ("tee", "top"),
            ("trousers", "bottom"),
            ("a-shoes", "shoes"),
            ("b-sneakers", "shoes"),
            ("jacket", "layer"),
        )
    }


def start(client, **kwargs):
    response = client.post(
        "/api/v1/styling/sessions",
        json={
            "occasion": "University, then meeting friends",
            **kwargs,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def swap_payload(session, wardrobe, **kwargs):
    return {
        "expected_version": session["version"],
        "base_revision_id": session["revisions"][0]["id"],
        "old_item_id": wardrobe["a-shoes"]["id"],
        "new_item_id": wardrobe["b-sneakers"]["id"],
        "reason": "I prefer these for walking between classes today",
        **kwargs,
    }
