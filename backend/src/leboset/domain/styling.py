from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class Role(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"
    SHOES = "shoes"
    ONE_PIECE = "one_piece"
    LAYER = "layer"


class Template(StrEnum):
    SEPARATES = "separates"
    ONE_PIECE = "one_piece"


@dataclass(frozen=True)
class Item:
    id: UUID
    owner_id: UUID
    name: str
    role: Role
    colors: tuple[str, ...]
    confirmed: bool
    available: bool
    version: int


@dataclass(frozen=True)
class Context:
    occasion: str
    template: Template = Template.SEPARATES
    include_layer: bool = False
    required_ids: tuple[UUID, ...] = ()
    excluded_ids: tuple[UUID, ...] = ()


class Ranker(Protocol):
    def order(self, candidates: tuple[Item, ...], context: Context) -> tuple[Item, ...]: ...


class StableRanker:
    """Reproducible baseline, not an AI or fashion-quality assessment."""

    def order(self, candidates: tuple[Item, ...], context: Context) -> tuple[Item, ...]:
        return tuple(sorted(candidates, key=lambda item: (item.name.casefold(), str(item.id))))


def roles_for(context: Context) -> tuple[Role, ...]:
    roles = (
        (Role.TOP, Role.BOTTOM, Role.SHOES)
        if context.template == Template.SEPARATES
        else (Role.ONE_PIECE, Role.SHOES)
    )
    return roles + ((Role.LAYER,) if context.include_layer else ())


def allowed_items(items: tuple[Item, ...], owner_id: UUID, context: Context) -> dict[UUID, Item]:
    return {
        item.id: item
        for item in items
        if item.owner_id == owner_id
        and item.confirmed
        and item.available
        and item.id not in context.excluded_ids
    }


def validate(
    selected: tuple[UUID, ...], items: tuple[Item, ...], owner_id: UUID, context: Context
) -> None:
    allowed = allowed_items(items, owner_id, context)
    if set(context.required_ids) & set(context.excluded_ids):
        raise DomainError("conflicting_constraints", "An item cannot be required and excluded.")
    if len(set(selected)) != len(selected) or any(item_id not in allowed for item_id in selected):
        raise DomainError("invalid_items", "An outfit item is unavailable or not confirmed.")
    if not set(context.required_ids).issubset(selected):
        raise DomainError("required_item_missing", "Required items must remain in the outfit.")
    roles = [allowed[item_id].role for item_id in selected]
    if len(roles) != len(roles_for(context)) or set(roles) != set(roles_for(context)):
        raise DomainError("invalid_template", "Outfit roles do not match the selected template.")


def propose(
    items: tuple[Item, ...], owner_id: UUID, context: Context, ranker: Ranker
) -> tuple[UUID, ...]:
    if set(context.required_ids) & set(context.excluded_ids):
        raise DomainError("conflicting_constraints", "An item cannot be required and excluded.")
    allowed = allowed_items(items, owner_id, context)
    if any(item_id not in allowed for item_id in context.required_ids):
        raise DomainError("required_item_unavailable", "A required item is not available.")
    required_roles = [allowed[item_id].role for item_id in context.required_ids]
    if len(set(required_roles)) != len(required_roles):
        raise DomainError(
            "conflicting_constraints", "Only one required item per role is supported."
        )
    if not set(required_roles).issubset(roles_for(context)):
        raise DomainError("invalid_template", "A required item does not fit the chosen template.")
    selected = []
    for role in roles_for(context):
        required = [item_id for item_id in context.required_ids if allowed[item_id].role == role]
        if required:
            selected.append(required[0])
            continue
        candidates = tuple(item for item in allowed.values() if item.role == role)
        if not candidates:
            raise DomainError("insufficient_wardrobe", f"Add a confirmed available {role.value}.")
        ordered = ranker.order(candidates, context)
        if not ordered or ordered[0].id not in {item.id for item in candidates}:
            raise DomainError("invalid_ranking", "The ranker returned an invalid candidate.")
        selected.append(ordered[0].id)
    result = tuple(selected)
    validate(result, items, owner_id, context)
    return result


def replace_item(
    selected: tuple[UUID, ...],
    items: tuple[Item, ...],
    owner_id: UUID,
    context: Context,
    old_id: UUID,
    new_id: UUID,
    reason: str,
) -> tuple[UUID, ...]:
    if not reason.strip():
        raise DomainError("reason_required", "Describe why you are replacing this item.")
    if old_id not in selected or old_id == new_id:
        raise DomainError(
            "invalid_replacement", "Choose an outfit item and a different replacement."
        )
    if old_id in context.required_ids:
        raise DomainError("item_locked", "The requested item is fixed for this session.")
    # Allow replacing an item that has since become unavailable, but revalidate the result.
    owned = {item.id: item for item in items if item.owner_id == owner_id}
    new_item = allowed_items(items, owner_id, context).get(new_id)
    if old_id not in owned or new_item is None:
        raise DomainError("invalid_replacement", "The replacement is not available.")
    if owned[old_id].role != new_item.role:
        raise DomainError("wrong_role", "Replace an item with one serving the same clothing role.")
    result = tuple(new_id if item_id == old_id else item_id for item_id in selected)
    validate(result, items, owner_id, context)
    return result
