"""One-click local setup. Only stdlib imports until dependencies are installed."""

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from contextlib import contextmanager
from datetime import UTC, timedelta
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VENV = BACKEND / ".venv"
LOCAL = BACKEND / ".local"


def run_step(label, args, env):
    print(f"\n--- {label} ---", flush=True)
    subprocess.run(args, cwd=BACKEND, env=env, check=True)


@contextmanager
def launcher_lock():
    """Prevent simultaneous installers or duplicate servers from this launcher."""
    LOCAL.mkdir(parents=True, exist_ok=True)
    with (LOCAL / "launcher.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(
                    "LEBOSET setup is already running. Use its existing window."
                ) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def ensure_dev_access(url, path):
    """Reuse one local user; rotate expired or revoked tokens without losing their wardrobe."""
    from leboset.cli import provision_user
    from leboset.infrastructure.database import TokenRow, UserRow, make_engine, now, session_factory

    saved = None
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            UUID(saved["user_id"])
            if not isinstance(saved["token"], str) or not saved["token"]:
                raise ValueError("Missing token")
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError(
                f"Invalid local access file: {path}. Restore it before continuing."
            ) from exc

    engine = make_engine(url)
    try:
        with session_factory(engine)() as db:
            user = db.get(UserRow, UUID(saved["user_id"])) if saved else None
            if user is None:
                user, token = provision_user(db, "Local Student")
            else:
                digest = hashlib.sha256(saved["token"].encode()).hexdigest()
                existing = db.get(TokenRow, digest)
                if existing is not None and existing.user_id == user.id:
                    expiry = existing.expires_at
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=UTC)
                    if expiry > now():
                        return saved
                    db.delete(existing)
                token = secrets.token_urlsafe(32)
                db.add(
                    TokenRow(
                        digest=hashlib.sha256(token.encode()).hexdigest(),
                        user_id=user.id,
                        expires_at=now() + timedelta(days=7),
                    )
                )
                db.commit()
            saved = {"user_id": str(user.id), "token": token}
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
            return saved
    finally:
        engine.dispose()


def available_port(preferred):
    for port in range(preferred, min(preferred + 10, 65536)):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("No free local port found. Close an unused development server and retry.")


def wait_for_server(process, base_url, token, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The API server exited before it was ready. See its output above.")
        try:
            request = urllib.request.Request(
                base_url + "/api/v1/wardrobe/items",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200 and isinstance(json.load(response), list):
                    return
        except (OSError, ValueError):
            time.sleep(0.25)
    raise RuntimeError("The API did not become ready in time. See its output above.")


def stop_server(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        process.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        if os.name == "nt":
            # Only this launcher's process tree, including the Windows venv redirector.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            process.kill()
        process.wait(timeout=5)


def start_server(python, env, access, port, smoke):
    port = available_port(port)
    base_url = f"http://127.0.0.1:{port}"
    print(f"\n--- Starting LEBOSET at {base_url}/docs ---", flush=True)
    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "uvicorn",
            "leboset.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=BACKEND,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        wait_for_server(process, base_url, access["token"])
        if smoke:
            with urllib.request.urlopen(base_url + "/docs", timeout=3) as response:
                if response.status != 200:
                    raise RuntimeError("API documentation is unavailable.")
            print("Launcher smoke test passed: authenticated API and documentation are ready.")
            return
        print("\nReady. This is the developer API; the product frontend is not built yet.")
        print("In Swagger, click Authorize and paste this local development token:")
        print(access["token"])
        print(f"\nLocal token file: {LOCAL / 'dev-access.json'}")
        print("Keep this window open. Press Ctrl+C to stop the server.\n", flush=True)
        if not webbrowser.open(base_url + "/docs"):
            print(f"Open this address in your browser: {base_url}/docs", flush=True)
        if process.wait() != 0:
            raise RuntimeError("The API server stopped with an error.")
    finally:
        stop_server(process)


def main():
    parser = argparse.ArgumentParser(description="Set up, test and launch LEBOSET locally")
    parser.add_argument(
        "--check-only", action="store_true", help="Set up and test without starting a server"
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Verify startup, then stop without opening a browser",
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if sys.version_info < (3, 12):  # noqa: UP036 - bootstrap validates the host interpreter
        raise RuntimeError(
            "Python 3.12 or newer is required. Recreate an older virtual environment manually."
        )
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535")
    if args.check_only and args.smoke_test:
        parser.error("Choose --check-only or --smoke-test")
    python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if Path(sys.prefix).resolve() != VENV.resolve():
        if not python.exists():
            run_step(
                "Create isolated Python environment", [sys.executable, "-m", "venv", str(VENV)], env
            )
        # Re-enter using the venv before importing application dependencies.
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], env=env)

    with launcher_lock():
        url = f"sqlite:///{(LOCAL / 'leboset.db').as_posix()}"
        env["LEBOSET_DATABASE_URL"] = url
        env.pop("LEBOSET_TEST_POSTGRES_URL", None)
        print("LEBOSET local development setup\nDatabase: backend/.local/leboset.db", flush=True)
        run_step(
            "1/6 Install locked dependencies",
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "-r",
                "requirements-dev.lock",
            ],
            env,
        )
        run_step(
            "2/6 Install LEBOSET",
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--no-deps",
                "-e",
                ".",
            ],
            env,
        )
        run_step("3/6 Check dependencies", [str(python), "-m", "pip", "check"], env)
        run_step(
            "Check code",
            [
                str(python),
                "-m",
                "ruff",
                "check",
                "--config",
                str(BACKEND / "pyproject.toml"),
                "src",
                "tests",
                "migrations",
                str(ROOT / "scripts"),
            ],
            env,
        )
        run_step(
            "Check formatting",
            [
                str(python),
                "-m",
                "ruff",
                "format",
                "--config",
                str(BACKEND / "pyproject.toml"),
                "--check",
                "src",
                "tests",
                "migrations",
                str(ROOT / "scripts"),
            ],
            env,
        )
        run_step("4/6 Run isolated tests", [str(python), "-m", "pytest", "-q"], env)
        run_step(
            "5/6 Apply local migrations", [str(python), "-m", "alembic", "upgrade", "head"], env
        )
        run_step("Check migration consistency", [str(python), "-m", "alembic", "check"], env)
        if args.check_only:
            print("\nAll development checks passed.", flush=True)
            return 0
        print("\n--- 6/6 Prepare local development access ---", flush=True)
        access = ensure_dev_access(url, LOCAL / "dev-access.json")
        start_server(python, env, access, args.port, args.smoke_test)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nLEBOSET stopped.", flush=True)
        raise SystemExit(0) from None
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(
            f"\nERROR: {exc}\nSetup stopped; no later steps were run.", file=sys.stderr, flush=True
        )
        raise SystemExit(1) from None
