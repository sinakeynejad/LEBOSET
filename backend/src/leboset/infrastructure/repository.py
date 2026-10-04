from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from leboset.domain.styling import DomainError, Item, Role
from leboset.infrastructure.database import ItemRow, RevisionRow, SessionRow


def as_item(row: ItemRow) -> Item:
    return Item(
        row.id,
        row.owner_id,
        row.name,
        Role(row.role),
        tuple(row.colors),
        row.confirmed,
        row.available,
        row.version,
    )


class Repository:
    def __init__(self, db: Session):
        self.db = db

    def wardrobe(self, owner_id: UUID) -> tuple[Item, ...]:
        # Lock wardrobe rows for the short command transaction on PostgreSQL.
        rows = self.db.scalars(
            select(ItemRow)
            .where(ItemRow.owner_id == owner_id)
            .order_by(ItemRow.id)
            .with_for_update()
        ).all()
        return tuple(as_item(row) for row in rows)

    def styling_session(self, owner_id: UUID, session_id: UUID) -> SessionRow:
        row = self.db.scalar(
            select(SessionRow)
            .where(SessionRow.id == session_id, SessionRow.owner_id == owner_id)
            .with_for_update()
        )
        if row is None:
            raise DomainError("not_found", "Styling session not found.", 404)
        return row

    def revisions(self, session_id: UUID) -> list[RevisionRow]:
        return list(
            self.db.scalars(
                select(RevisionRow)
                .where(RevisionRow.session_id == session_id)
                .order_by(RevisionRow.number)
            )
        )

    def save(self, row) -> None:
        self.db.add(row)
        self.db.flush()
