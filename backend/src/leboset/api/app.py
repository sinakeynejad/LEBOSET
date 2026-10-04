import hashlib
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from leboset.api.schemas import (
    AvailabilityInput,
    ContextInput,
    ItemCreate,
    ItemUpdate,
    ItemView,
    RevisionView,
    SelectionInput,
    SessionView,
    SwapInput,
    VersionInput,
)
from leboset.application.styling import StylingService
from leboset.domain.styling import DomainError, StableRanker
from leboset.infrastructure.database import ItemRow, TokenRow, make_engine, now, session_factory
from leboset.infrastructure.repository import Repository

bearer = HTTPBearer(auto_error=False)


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as db:
        try:
            yield db
        finally:
            db.rollback()  # Read-only requests and failed commands release locks too.


Db = Annotated[Session, Depends(get_db)]


def current_user(
    db: Db, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> UUID:
    if credentials is not None:
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        token = db.get(TokenRow, digest)
        if token is not None:
            expiry = token.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry > now():
                return token.user_id
    raise HTTPException(401, "Valid access token required", headers={"WWW-Authenticate": "Bearer"})


User = Annotated[UUID, Depends(current_user)]


def commit(db: Session) -> None:
    try:
        db.commit()
    except (StaleDataError, IntegrityError) as exc:
        db.rollback()
        raise DomainError(
            "write_conflict", "Data changed; reload before trying again.", 409
        ) from exc


def item_for(db: Session, user_id: UUID, item_id: UUID) -> ItemRow:
    row = db.scalar(
        select(ItemRow).where(ItemRow.id == item_id, ItemRow.owner_id == user_id).with_for_update()
    )
    if row is None:
        raise DomainError("not_found", "Wardrobe item not found.", 404)
    return row


def version_matches(actual: int, expected: int) -> None:
    if actual != expected:
        raise DomainError("stale_version", "Reload this item before changing it.", 409)


def session_view(repo: Repository, row) -> SessionView:
    return SessionView(
        id=row.id,
        context=row.context,
        status=row.status,
        version=row.version,
        selected_revision_id=row.selected_revision_id,
        selected_at=row.selected_at,
        revisions=[RevisionView.model_validate(value) for value in repo.revisions(row.id)],
    )


def create_app(url: str | None = None) -> FastAPI:
    engine = make_engine(url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="LEBOSET API", version="0.1.0", lifespan=lifespan)
    app.state.sessions = session_factory(engine)
    app.state.engine = engine
    app.state.ranker = StableRanker()

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                }
            },
        )

    @app.exception_handler(StaleDataError)
    async def stale_error(_request: Request, _exc: StaleDataError):
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "write_conflict",
                    "message": "Data changed; reload before trying again.",
                }
            },
        )

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}

    @app.post("/api/v1/wardrobe/items", response_model=ItemView, status_code=201, tags=["wardrobe"])
    def create_item(body: ItemCreate, db: Db, user: User):
        row = ItemRow(
            owner_id=user,
            name=body.name,
            role=body.role.value,
            colors=body.colors,
            confirmed=False,
            available=True,
        )
        db.add(row)
        commit(db)
        return row

    @app.get("/api/v1/wardrobe/items", response_model=list[ItemView], tags=["wardrobe"])
    def list_items(db: Db, user: User, offset: int = 0, limit: int = 50):
        if offset < 0 or not 1 <= limit <= 100:
            raise DomainError(
                "invalid_pagination", "Use offset >= 0 and a limit between 1 and 100."
            )
        return list(
            db.scalars(
                select(ItemRow)
                .where(ItemRow.owner_id == user)
                .order_by(ItemRow.id)
                .offset(offset)
                .limit(limit)
            )
        )

    @app.put("/api/v1/wardrobe/items/{item_id}", response_model=ItemView, tags=["wardrobe"])
    def update_item(item_id: UUID, body: ItemUpdate, db: Db, user: User):
        row = item_for(db, user, item_id)
        version_matches(row.version, body.expected_version)
        row.name, row.role, row.colors = body.name, body.role.value, body.colors
        row.confirmed = False
        row.version += 1
        commit(db)
        return row

    @app.post(
        "/api/v1/wardrobe/items/{item_id}/confirm", response_model=ItemView, tags=["wardrobe"]
    )
    def confirm_item(item_id: UUID, body: VersionInput, db: Db, user: User):
        row = item_for(db, user, item_id)
        version_matches(row.version, body.expected_version)
        row.confirmed = True
        commit(db)
        return row

    @app.put(
        "/api/v1/wardrobe/items/{item_id}/availability", response_model=ItemView, tags=["wardrobe"]
    )
    def set_availability(item_id: UUID, body: AvailabilityInput, db: Db, user: User):
        row = item_for(db, user, item_id)
        version_matches(row.version, body.expected_version)
        row.available = body.available
        commit(db)
        return row

    @app.post(
        "/api/v1/styling/sessions", response_model=SessionView, status_code=201, tags=["styling"]
    )
    def start_session(body: ContextInput, db: Db, user: User):
        repo = Repository(db)
        row = StylingService(repo, app.state.ranker).start(user, body.model_dump(mode="json"))
        view = session_view(repo, row)
        commit(db)
        return view

    @app.get("/api/v1/styling/sessions/{session_id}", response_model=SessionView, tags=["styling"])
    def get_session(session_id: UUID, db: Db, user: User):
        repo = Repository(db)
        return session_view(repo, repo.styling_session(user, session_id))

    @app.post(
        "/api/v1/styling/sessions/{session_id}/revisions",
        response_model=SessionView,
        status_code=201,
        tags=["styling"],
    )
    def swap(session_id: UUID, body: SwapInput, db: Db, user: User):
        repo = Repository(db)
        row = StylingService(repo, app.state.ranker).swap(
            user,
            session_id,
            body.expected_version,
            body.base_revision_id,
            body.old_item_id,
            body.new_item_id,
            body.reason,
        )
        view = session_view(repo, row)
        commit(db)
        return view

    @app.put(
        "/api/v1/styling/sessions/{session_id}/selection",
        response_model=SessionView,
        tags=["styling"],
    )
    def select_revision(session_id: UUID, body: SelectionInput, db: Db, user: User):
        repo = Repository(db)
        row = StylingService(repo, app.state.ranker).select(
            user,
            session_id,
            body.expected_version,
            body.revision_id,
        )
        view = session_view(repo, row)
        commit(db)
        return view

    return app
