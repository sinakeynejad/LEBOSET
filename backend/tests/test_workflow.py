from datetime import timedelta
from uuid import UUID

import pytest
from conftest import add_item, start, swap_payload
from fastapi.testclient import TestClient
from sqlalchemy import select

from leboset.api.app import create_app
from leboset.infrastructure.database import TokenRow, now


def test_full_workflow_preserves_history_and_survives_restart(api, wardrobe):
    client, app, *_ = api
    first = start(client, include_layer=True, required_item_ids=[wardrobe["jacket"]["id"]])
    response = client.post(
        f"/api/v1/styling/sessions/{first['id']}/revisions", json=swap_payload(first, wardrobe)
    )
    assert response.status_code == 201, response.text
    updated = response.json()
    previous, current = updated["revisions"]
    assert previous == first["revisions"][0]
    assert current["parent_id"] == previous["id"]
    assert set(previous["item_ids"]) - set(current["item_ids"]) == {wardrobe["a-shoes"]["id"]}
    assert set(current["item_ids"]) - set(previous["item_ids"]) == {wardrobe["b-sneakers"]["id"]}
    assert current["change"]["scope"] == "session"
    assert current["change"]["reason"] == swap_payload(first, wardrobe)["reason"]
    body = {"expected_version": updated["version"], "revision_id": current["id"]}
    selected = client.put(f"/api/v1/styling/sessions/{first['id']}/selection", json=body)
    assert selected.status_code == 200, selected.text
    assert selected.json()["status"] == "selected"
    repeated = client.put(f"/api/v1/styling/sessions/{first['id']}/selection", json=body)
    assert repeated.json() == selected.json()
    with TestClient(
        create_app(app.state.engine.url.render_as_string(hide_password=False))
    ) as restarted:
        loaded = restarted.get(
            f"/api/v1/styling/sessions/{first['id']}",
            headers={"Authorization": client.headers["Authorization"]},
        )
        assert loaded.json() == selected.json()


def test_can_choose_original_or_branch_from_it(api, wardrobe):
    client = api[0]
    first = start(client)
    url = f"/api/v1/styling/sessions/{first['id']}"
    changed = client.post(url + "/revisions", json=swap_payload(first, wardrobe)).json()
    branched = client.post(
        url + "/revisions",
        json=swap_payload(
            changed,
            wardrobe,
            base_revision_id=first["revisions"][0]["id"],
            reason="Compare again",
        ),
    )
    assert branched.status_code == 201
    assert branched.json()["revisions"][-1]["parent_id"] == first["revisions"][0]["id"]
    selected = client.put(
        url + "/selection",
        json={
            "expected_version": branched.json()["version"],
            "revision_id": first["revisions"][0]["id"],
        },
    )
    assert selected.status_code == 200
    assert selected.json()["selected_revision_id"] == first["revisions"][0]["id"]


def test_no_auth_or_expired_auth_is_rejected(api):
    client, app, *_ = api
    with app.state.sessions() as db:
        for token in db.scalars(select(TokenRow)):
            token.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/wardrobe/items").status_code == 401
    client.headers.pop("Authorization")
    assert client.get("/api/v1/wardrobe/items").status_code == 401
    assert client.get("/health").status_code == 200


def test_cross_user_isolation(api, wardrobe):
    client, _, bob_auth, *_ = api
    session = start(client)
    headers = {"Authorization": bob_auth}
    assert client.get("/api/v1/wardrobe/items", headers=headers).json() == []
    assert (
        client.get(f"/api/v1/styling/sessions/{session['id']}", headers=headers).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/wardrobe/items/{wardrobe['tee']['id']}/confirm",
            json={"expected_version": 2},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/styling/sessions",
            json={
                "occasion": "Campus",
                "required_item_ids": [wardrobe["tee"]["id"]],
            },
            headers=headers,
        ).status_code
        == 422
    )


def test_unconfirmed_items_do_not_make_a_complete_wardrobe(api):
    client = api[0]
    add_item(client, "top", "Tee", confirmed=False)
    add_item(client, "bottom", "Trousers")
    add_item(client, "shoes", "Shoes")
    response = client.post("/api/v1/styling/sessions", json={"occasion": "Campus"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_wardrobe"


def test_one_piece_template(api):
    client = api[0]
    dress = add_item(client, "one_piece", "Dress")
    shoes = add_item(client, "shoes", "Shoes")
    session = start(client, template="one_piece")
    assert set(session["revisions"][0]["item_ids"]) == {dress["id"], shoes["id"]}


@pytest.mark.parametrize(
    "failure", ["locked", "wrong_role", "excluded", "blank_reason", "same_item"]
)
def test_invalid_swap_has_no_partial_revision(api, wardrobe, failure):
    client = api[0]
    context = {}
    if failure == "locked":
        context["required_item_ids"] = [wardrobe["a-shoes"]["id"]]
    if failure == "excluded":
        context["excluded_item_ids"] = [wardrobe["b-sneakers"]["id"]]
    session = start(client, **context)
    payload = swap_payload(session, wardrobe)
    if failure == "wrong_role":
        payload["new_item_id"] = wardrobe["tee"]["id"]
    if failure == "blank_reason":
        payload["reason"] = "   "
    if failure == "same_item":
        payload["new_item_id"] = payload["old_item_id"]
    url = f"/api/v1/styling/sessions/{session['id']}"
    assert client.post(url + "/revisions", json=payload).status_code == 422
    assert client.get(url).json() == session


def test_stale_write_and_duplicate_swap_do_not_duplicate_history(api, wardrobe):
    client = api[0]
    session = start(client)
    url = f"/api/v1/styling/sessions/{session['id']}"
    body = swap_payload(session, wardrobe)
    assert client.post(url + "/revisions", json=body).status_code == 201
    assert client.post(url + "/revisions", json=body).status_code == 409
    assert len(client.get(url).json()["revisions"]) == 2


def test_final_selection_revalidates_current_availability(api, wardrobe):
    client = api[0]
    session = start(client)
    item = wardrobe["tee"]
    response = client.put(
        f"/api/v1/wardrobe/items/{item['id']}/availability",
        json={
            "expected_version": item["version"],
            "available": False,
        },
    )
    assert response.status_code == 200
    assert (
        client.put(
            f"/api/v1/styling/sessions/{session['id']}/selection",
            json={
                "expected_version": session["version"],
                "revision_id": session["revisions"][0]["id"],
            },
        ).status_code
        == 422
    )


def test_editing_item_requires_reconfirmation_and_invalidates_old_snapshot(api, wardrobe):
    client = api[0]
    session = start(client)
    item = wardrobe["tee"]
    response = client.put(
        f"/api/v1/wardrobe/items/{item['id']}",
        json={
            "name": "Updated tee",
            "role": "top",
            "colors": ["white"],
            "expected_version": item["version"],
        },
    )
    assert response.status_code == 200
    assert not response.json()["confirmed"]
    client.post(
        f"/api/v1/wardrobe/items/{item['id']}/confirm",
        json={
            "expected_version": response.json()["version"],
        },
    )
    selection = client.put(
        f"/api/v1/styling/sessions/{session['id']}/selection",
        json={
            "expected_version": session["version"],
            "revision_id": session["revisions"][0]["id"],
        },
    )
    assert selection.status_code == 409
    assert selection.json()["error"]["code"] == "wardrobe_changed"


def test_closed_session_cannot_be_edited(api, wardrobe):
    client = api[0]
    session = start(client)
    url = f"/api/v1/styling/sessions/{session['id']}"
    client.put(
        url + "/selection",
        json={
            "expected_version": session["version"],
            "revision_id": session["revisions"][0]["id"],
        },
    )
    response = client.post(url + "/revisions", json=swap_payload(session, wardrobe))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_closed"


def test_unknown_input_fields_and_blank_values_rejected(api):
    client = api[0]
    assert (
        client.post(
            "/api/v1/wardrobe/items",
            json={
                "name": " ",
                "role": "top",
                "colors": ["black"],
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/wardrobe/items",
            json={
                "name": "Tee",
                "role": "top",
                "colors": ["black"],
                "confirmed": True,
            },
        ).status_code
        == 422
    )


def test_foreign_revision_cannot_be_selected(api, wardrobe):
    client = api[0]
    first, other = start(client), start(client)
    assert (
        client.put(
            f"/api/v1/styling/sessions/{first['id']}/selection",
            json={
                "expected_version": first["version"],
                "revision_id": other["revisions"][0]["id"],
            },
        ).status_code
        == 404
    )


def test_database_optimistic_lock_rejects_stale_session_writer(api, wardrobe):
    from sqlalchemy.orm.exc import StaleDataError

    from leboset.infrastructure.database import SessionRow

    client, app, *_ = api
    session = start(client)
    with app.state.sessions() as left, app.state.sessions() as right:
        first = left.get(SessionRow, UUID(session["id"]))
        stale = right.get(SessionRow, UUID(session["id"]))
        first.version += 1
        left.commit()
        stale.version += 1
        with pytest.raises(StaleDataError):
            right.commit()
