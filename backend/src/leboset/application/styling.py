from uuid import UUID

from leboset.domain.styling import (
    Context,
    DomainError,
    Ranker,
    Template,
    propose,
    replace_item,
    validate,
)
from leboset.infrastructure.database import RevisionRow, SessionRow, now
from leboset.infrastructure.repository import Repository


def context_from(data: dict) -> Context:
    return Context(
        occasion=data["occasion"],
        template=Template(data["template"]),
        include_layer=data["include_layer"],
        required_ids=tuple(UUID(value) for value in data["required_item_ids"]),
        excluded_ids=tuple(UUID(value) for value in data["excluded_item_ids"]),
    )


def snapshot(selected, items) -> list[dict]:
    by_id = {item.id: item for item in items}
    return [
        {
            "id": str(item_id),
            "name": by_id[item_id].name,
            "role": by_id[item_id].role.value,
            "colors": list(by_id[item_id].colors),
            "version": by_id[item_id].version,
        }
        for item_id in selected
    ]


class StylingService:
    """Explicit orchestration; pure styling rules live in the domain package.

    This first adapter uses SQLAlchemy records. Domain rules and the ranker remain
    independent; a generic repository framework is intentionally not introduced.
    The caller owns commit/rollback for the whole command.
    """

    def __init__(self, repo: Repository, ranker: Ranker):
        self.repo = repo
        self.ranker = ranker

    def start(self, owner_id: UUID, context_data: dict) -> SessionRow:
        context = context_from(context_data)
        items = self.repo.wardrobe(owner_id)
        selected = propose(items, owner_id, context, self.ranker)
        session = SessionRow(owner_id=owner_id, context=context_data)
        self.repo.save(session)
        self.repo.save(
            RevisionRow(
                session_id=session.id,
                number=1,
                parent_id=None,
                item_ids=[str(value) for value in selected],
                item_snapshot=snapshot(selected, items),
                change=None,
            )
        )
        return session

    def swap(
        self,
        owner_id: UUID,
        session_id: UUID,
        expected_version: int,
        base_revision_id: UUID,
        old_id: UUID,
        new_id: UUID,
        reason: str,
    ) -> SessionRow:
        session = self.repo.styling_session(owner_id, session_id)
        if session.status != "active":
            raise DomainError(
                "session_closed", "Start another session to change a final selection.", 409
            )
        if session.version != expected_version:
            raise DomainError(
                "stale_version", "Reload the session before making another change.", 409
            )
        revisions = self.repo.revisions(session.id)
        base = next((revision for revision in revisions if revision.id == base_revision_id), None)
        if base is None:
            raise DomainError("not_found", "Outfit revision not found.", 404)
        items = self.repo.wardrobe(owner_id)
        selected = replace_item(
            tuple(UUID(value) for value in base.item_ids),
            items,
            owner_id,
            context_from(session.context),
            old_id,
            new_id,
            reason,
        )
        # Branching from an older version preserves history rather than rewriting it.
        session.version += 1
        self.repo.save(session)
        self.repo.save(
            RevisionRow(
                session_id=session.id,
                number=revisions[-1].number + 1,
                parent_id=base.id,
                item_ids=[str(value) for value in selected],
                item_snapshot=snapshot(selected, items),
                change={
                    "old_item_id": str(old_id),
                    "new_item_id": str(new_id),
                    "reason": reason.strip(),
                    "scope": "session",
                },
            )
        )
        return session

    def select(
        self,
        owner_id: UUID,
        session_id: UUID,
        expected_version: int,
        revision_id: UUID,
    ) -> SessionRow:
        session = self.repo.styling_session(owner_id, session_id)
        if session.status == "selected":
            if session.selected_revision_id == revision_id:
                return session  # An identical retry has no additional side effects.
            raise DomainError("session_closed", "This session already has a final selection.", 409)
        if session.version != expected_version:
            raise DomainError(
                "stale_version", "Reload the session before selecting an outfit.", 409
            )
        revision = next(
            (row for row in self.repo.revisions(session.id) if row.id == revision_id), None
        )
        if revision is None:
            raise DomainError("not_found", "Outfit revision not found.", 404)
        items = self.repo.wardrobe(owner_id)
        validate(
            tuple(UUID(value) for value in revision.item_ids),
            items,
            owner_id,
            context_from(session.context),
        )
        # Do not silently finalize a historical snapshot whose item details have changed.
        current_versions = {str(item.id): item.version for item in items}
        if any(
            current_versions.get(item["id"]) != item["version"] for item in revision.item_snapshot
        ):
            raise DomainError(
                "wardrobe_changed", "An item changed; create a fresh suggestion.", 409
            )
        session.status = "selected"
        session.selected_revision_id = revision.id
        session.selected_at = now()
        self.repo.save(session)
        return session
