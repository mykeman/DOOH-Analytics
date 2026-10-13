"""Account tree resolution and role-based access checks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, Membership, Role, User


@dataclass
class AccountTree:
    parent: dict[int, int | None]
    children: dict[int, list[int]]
    names: dict[int, str]

    @classmethod
    def load(cls, db: Session) -> AccountTree:
        parent: dict[int, int | None] = {}
        names: dict[int, str] = {}
        children: dict[int, list[int]] = defaultdict(list)
        for acc_id, parent_id, name in db.execute(
            select(Account.id, Account.parent_id, Account.name)
        ):
            parent[acc_id] = parent_id
            names[acc_id] = name
            if parent_id is not None:
                children[parent_id].append(acc_id)
        return cls(parent=parent, children=dict(children), names=names)

    def descendants(self, account_id: int, include_self: bool = True) -> set[int]:
        out: set[int] = {account_id} if include_self else set()
        stack = list(self.children.get(account_id, []))
        while stack:
            node = stack.pop()
            if node in out:
                continue
            out.add(node)
            stack.extend(self.children.get(node, []))
        return out

    def ancestors(self, account_id: int, include_self: bool = True) -> list[int]:
        """Ancestors from ``account_id`` (optionally) up to the root."""
        out: list[int] = [account_id] if include_self else []
        seen = {account_id}
        node = self.parent.get(account_id)
        while node is not None and node not in seen:
            out.append(node)
            seen.add(node)
            node = self.parent.get(node)
        return out

    def path(self, account_id: int) -> str:
        return " / ".join(self.names[a] for a in reversed(self.ancestors(account_id)))


class AccessScope:
    """What a single user may see and do, resolved once per request."""

    def __init__(self, db: Session, user: User):
        self.user = user
        self.tree = AccountTree.load(db)
        self._roles: dict[int, Role] = {}
        if user.is_superadmin:
            for acc_id in self.tree.parent:
                self._roles[acc_id] = Role.admin
            return
        memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
        for m in memberships:
            granted = Role(m.role)
            for acc_id in self.tree.descendants(m.account_id):
                current = self._roles.get(acc_id)
                if current is None or granted.rank > current.rank:
                    self._roles[acc_id] = granted

    @property
    def account_ids(self) -> set[int]:
        return set(self._roles)

    def role(self, account_id: int) -> Role | None:
        return self._roles.get(account_id)

    def can(self, account_id: int, minimum: Role) -> bool:
        role = self._roles.get(account_id)
        return role is not None and role.rank >= minimum.rank

    def root_account_ids(self) -> list[int]:
        """Accessible accounts whose parent is not accessible (tree roots for this user)."""
        ids = self.account_ids
        return sorted(a for a in ids if self.tree.parent.get(a) not in ids)
