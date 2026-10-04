import hashlib
import importlib.util
import socket
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from leboset.infrastructure.database import TokenRow, UserRow, now

script = Path(__file__).resolve().parents[2] / "scripts/dev.py"
spec = importlib.util.spec_from_file_location("dev_launcher", script)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_launch_reuses_user_and_valid_token(api, tmp_path):
    app = api[1]
    url = app.state.engine.url.render_as_string(hide_password=False)
    path = tmp_path / "access.json"
    first = launcher.ensure_dev_access(url, path)
    second = launcher.ensure_dev_access(url, path)
    assert second == first
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(UserRow)) == 3
        assert db.get(UserRow, UUID(first["user_id"])) is not None
        token = db.get(TokenRow, hashlib.sha256(first["token"].encode()).hexdigest())
        assert token.user_id == UUID(first["user_id"])


def test_expired_token_rotates_without_replacing_user(api, tmp_path):
    app = api[1]
    url = app.state.engine.url.render_as_string(hide_password=False)
    path = tmp_path / "access.json"
    first = launcher.ensure_dev_access(url, path)
    old_digest = hashlib.sha256(first["token"].encode()).hexdigest()
    with app.state.sessions() as db:
        db.get(TokenRow, old_digest).expires_at = now() - timedelta(seconds=1)
        db.commit()
    renewed = launcher.ensure_dev_access(url, path)
    assert renewed["user_id"] == first["user_id"]
    assert renewed["token"] != first["token"]
    with app.state.sessions() as db:
        assert db.get(TokenRow, old_digest) is None
        assert db.scalar(select(func.count()).select_from(UserRow)) == 3


def test_busy_port_is_not_reused():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        busy = listener.getsockname()[1]
        if busy < 65535:
            assert launcher.available_port(busy) != busy
