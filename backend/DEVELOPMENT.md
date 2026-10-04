# Backend development

This is the first executable LEBOSET slice: manually entered wardrobe items,
explicit confirmation, an initial outfit, versioned item replacement with a
reason, and final selection. It is a developer API, not a released web app.

## One-click startup on Windows

Double-click **`start-dev.bat`** in the repository root. Python 3.12 or newer
must already be installed; an internet connection is needed to download missing
packages. The launcher works independently of the terminal's current directory.

It creates or reuses `backend/.venv`, installs the locked dependencies and local
package, runs dependency/lint/format checks and tests, applies migrations, then
starts the API and opens its Swagger page in your default browser. Any failed
step stops the sequence and leaves the error visible in the window.

The launcher uses a dedicated **`backend/.local/leboset.db`**. Existing data is
preserved on subsequent runs. It does not use an inherited PostgreSQL connection
or PostgreSQL test URL. The manual commands below use a different SQLite file
unless you explicitly set `LEBOSET_DATABASE_URL` to this launcher database.

A local test user is created once and reused. Its token is renewed when needed,
printed in the launcher window, and stored in the Git-ignored file
`backend/.local/dev-access.json`. In Swagger, click **Authorize** and paste that
token. Do not share the file or token. This is developer access, not a public
signup flow.

Keep the launcher window open while using the API. Press **Ctrl+C** to stop it.
Port 8000 is preferred; if occupied, the launcher tries the following nine ports
and prints the actual address. No product frontend exists yet: the page opened
is the interactive API documentation.

Optional commands from the repository root:

```bat
start-dev.bat --check-only
start-dev.bat --smoke-test
start-dev.bat --port 8080
```

`--check-only` installs dependencies and runs checks without starting a server.
`--smoke-test` additionally verifies authenticated startup and documentation,
then stops automatically without opening a browser. Automated runners can set
`LEBOSET_NO_PAUSE=1` to disable the final keypress prompt.

## Run locally on Windows

Use Python 3.12 or newer. From the repository root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m leboset.cli create-user --name Student
.\.venv\Scripts\python.exe -m uvicorn leboset.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/docs>. Use **Authorize** and paste the token printed
by `create-user`. Tokens expire after seven days; only their SHA-256 digest is
stored. Do not commit or share the token. To revoke access:

```powershell
.\.venv\Scripts\python.exe -m leboset.cli revoke-tokens --user-id USER_UUID
```

SQLite is the default local database (`backend/leboset.db` when started here).
Database migrations are explicit; application startup does not create tables.
`.env.example` documents configuration but is not loaded automatically.

For PostgreSQL, set the environment variable before migration and startup:

```powershell
$env:LEBOSET_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@localhost:5432/leboset'
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Use a dedicated development database and configure credentials outside Git.
The lock file records tested development dependencies for Python 3.12.

## Exercise the workflow

1. Create at least one `top`, one `bottom`, and two `shoes` using
   `POST /api/v1/wardrobe/items`. Optionally add a `layer`.
2. Confirm each item with `POST /api/v1/wardrobe/items/{id}/confirm`, passing its
   current `version` as `expected_version`.
3. Create a session with `POST /api/v1/styling/sessions`:

   ```json
   {
     "occasion": "University, then meeting friends",
     "template": "separates",
     "include_layer": false,
     "required_item_ids": [],
     "excluded_item_ids": []
   }
   ```

4. Use `POST /api/v1/styling/sessions/{id}/revisions` to replace one item. Supply
   `expected_version` from the session, `base_revision_id`, `old_item_id`,
   `new_item_id`, and a nonempty `reason`. Read the new version from the response.
5. Compare snapshots in `GET /api/v1/styling/sessions/{id}`. You may branch from
   an older revision while supplying the latest session version.
6. Use `PUT /api/v1/styling/sessions/{id}/selection` with `expected_version` and
   `revision_id`. Any valid revision in this session can be selected. Repeating
   the same final selection is a no-op; changing it requires a new session.

Items start unconfirmed. Editing item details resets confirmation. Selection
rechecks current availability, confirmation, constraints, and item versions.
If a saved snapshot no longer matches item details, create a new session.

Failed commands roll back. Stale edits return `409`; reload before retrying.
A repeated swap with an old version cannot add another revision. Session
creation does not yet support idempotency keys; do not automatically retry a
timed-out creation request. A lost response can require inspecting the database
in this developer-only slice; session listing/recovery is follow-up work.

## Boundaries

```text
api/              HTTP validation, token authentication and transaction boundary
application/      Session creation, revision and selection use cases
domain/           Pure outfit rules and the ranker interface
infrastructure/   SQLAlchemy persistence and repository
migrations/       Versioned database changes
tests/            Domain and API/persistence behavior
```

The domain imports neither FastAPI nor SQLAlchemy. The application layer uses
the concrete persistence adapter in this first slice; it is not a fully
abstracted ports-and-adapters framework. The ranker is replaceable independently
of hard constraints. Revisions store immutable item snapshots for comparison;
live wardrobe records remain authoritative when making a new selection.

PostgreSQL commands use row locks; version checks reject stale edits. SQLite is
a local development profile, not a multi-user deployment target. No network AI
call belongs inside these database transactions.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests migrations
.\.venv\Scripts\python.exe -m ruff format --check src tests migrations
.\.venv\Scripts\python.exe -m alembic check
```

Tests migrate a temporary database and verify isolation, confirmation, required
items, replacement roles, revision history, stale writes, final selection and
persistence across application recreation. For the PostgreSQL test profile,
set `LEBOSET_TEST_POSTGRES_URL` to a dedicated test database. Each test uses an
isolated schema which is removed afterward. Never use a production connection.

## Current scope

- The baseline ranker sorts eligible items deterministically. It does not assess
  fashion compatibility or interpret the occasion; context is retained for the
  later ranking implementation.
- Replacements are chosen explicitly by item ID. Automatic alternative ranking
  is not implemented yet.
- Reasons are saved with `scope=session`; they do not update long-term preferences.
- CLI-issued bearer tokens are for local development. Public registration,
  browser sessions, rate limiting and account recovery are not implemented.
- Image upload, image analysis, natural-language parsing, frontend, background
  jobs and learned preferences are not implemented.

Keep the server bound to localhost until production authentication and deployment
controls have been implemented and reviewed.
