from uuid import uuid4

import pytest

from leboset.domain.styling import (
    Context,
    DomainError,
    Item,
    Role,
    StableRanker,
    propose,
    replace_item,
)


def test_ranker_cannot_inject_foreign_item():
    owner = uuid4()
    items = tuple(
        Item(uuid4(), owner, role.value, role, ("black",), True, True, 1)
        for role in (Role.TOP, Role.BOTTOM, Role.SHOES)
    )

    class BadRanker:
        def order(self, candidates, context):
            return (Item(uuid4(), uuid4(), "Foreign", Role.TOP, (), True, True, 1),)

    with pytest.raises(DomainError, match="invalid candidate"):
        propose(items, owner, Context("Campus"), BadRanker())


def test_conflicting_constraints_are_not_relaxed():
    owner, item_id = uuid4(), uuid4()
    with pytest.raises(DomainError, match="required and excluded"):
        propose(
            (),
            owner,
            Context("Campus", required_ids=(item_id,), excluded_ids=(item_id,)),
            StableRanker(),
        )


def test_unavailable_old_item_can_be_replaced():
    owner = uuid4()
    top = Item(uuid4(), owner, "Tee", Role.TOP, (), True, True, 1)
    bottom = Item(uuid4(), owner, "Trousers", Role.BOTTOM, (), True, True, 1)
    old = Item(uuid4(), owner, "Old shoes", Role.SHOES, (), True, False, 2)
    new = Item(uuid4(), owner, "New shoes", Role.SHOES, (), True, True, 1)
    assert replace_item(
        (top.id, bottom.id, old.id),
        (top, bottom, old, new),
        owner,
        Context("Campus"),
        old.id,
        new.id,
        "Old shoes need cleaning",
    ) == (
        top.id,
        bottom.id,
        new.id,
    )
