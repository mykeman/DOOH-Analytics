"""Accounts (sub-account tree) and account membership management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_scope, require
from ..models import Account, Membership, Role, Screen, User
from ..schemas import AccountIn, AccountOut, AccountUpdate, MemberIn, MemberOut, MemberUpdate
from ..security import hash_password
from ..tenancy import AccessScope

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


def _account_out(db: Session, scope: AccessScope, account: Account) -> AccountOut:
    count = db.scalar(select(func.count(Screen.id)).where(Screen.account_id == account.id)) or 0
    return AccountOut(
        id=account.id,
        name=account.name,
        parent_id=account.parent_id,
        path=scope.tree.path(account.id),
        role=scope.role(account.id),
        screen_count=count,
    )


@router.get("", response_model=list[AccountOut])
def list_accounts(scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)):
    ids = scope.account_ids
    if not ids:
        return []
    counts = dict(
        db.execute(
            select(Screen.account_id, func.count(Screen.id))
            .where(Screen.account_id.in_(ids))
            .group_by(Screen.account_id)
        ).all()
    )
    out = [
        AccountOut(
            id=a,
            name=scope.tree.names[a],
            # Hide the parent link when the parent is outside the user's scope.
            parent_id=scope.tree.parent[a] if scope.tree.parent[a] in ids else None,
            path=scope.tree.path(a),
            role=scope.role(a),
            screen_count=counts.get(a, 0),
        )
        for a in ids
    ]
    out.sort(key=lambda a: a.path.lower())
    return out


@router.post("", response_model=AccountOut, status_code=201)
def create_account(
    body: AccountIn, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    if body.parent_id is None:
        if not scope.user.is_superadmin:
            raise HTTPException(status_code=403, detail="Only super-admins create top-level accounts")
    else:
        require(scope, body.parent_id, Role.admin)
    account = Account(name=body.name.strip(), parent_id=body.parent_id)
    db.add(account)
    db.commit()
    return _account_out(db, AccessScope(db, scope.user), account)


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    body: AccountUpdate,
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    require(scope, account_id, Role.admin)
    account = db.get(Account, account_id)
    account.name = body.name.strip()
    db.commit()
    return _account_out(db, AccessScope(db, scope.user), account)


@router.delete("/{account_id}", status_code=204)
def delete_account(
    account_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    require(scope, account_id, Role.admin)
    parent_id = scope.tree.parent.get(account_id)
    # Deleting requires admin rights *above* the account, so a client admin
    # cannot remove their own account.
    if parent_id is None:
        if not scope.user.is_superadmin:
            raise HTTPException(status_code=403, detail="Only super-admins delete top-level accounts")
    elif not scope.can(parent_id, Role.admin):
        raise HTTPException(status_code=403, detail="Requires admin role on the parent account")
    if scope.tree.children.get(account_id):
        raise HTTPException(status_code=409, detail="Account has sub-accounts; delete them first")
    if db.scalar(select(func.count(Screen.id)).where(Screen.account_id == account_id)):
        raise HTTPException(status_code=409, detail="Account has screens; move or delete them first")
    db.delete(db.get(Account, account_id))
    db.commit()


# --- members ----------------------------------------------------------------


@router.get("/{account_id}/members", response_model=list[MemberOut])
def list_members(
    account_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    require(scope, account_id, Role.admin)
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.account_id == account_id)
        .order_by(User.email)
    ).all()
    return [
        MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role, is_active=u.is_active)
        for m, u in rows
    ]


@router.post("/{account_id}/members", response_model=MemberOut, status_code=201)
def add_member(
    account_id: int,
    body: MemberIn,
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    require(scope, account_id, Role.admin)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email))
    if user is None:
        if not body.password:
            raise HTTPException(status_code=422, detail="password is required for a new user")
        user = User(email=body.email, name=body.name, password_hash=hash_password(body.password))
        db.add(user)
        db.flush()
    existing = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id, Membership.account_id == account_id
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member of this account")
    membership = Membership(user_id=user.id, account_id=account_id, role=body.role.value)
    db.add(membership)
    db.commit()
    return MemberOut(
        user_id=user.id, email=user.email, name=user.name, role=body.role, is_active=user.is_active
    )


@router.patch("/{account_id}/members/{user_id}", response_model=MemberOut)
def update_member(
    account_id: int,
    user_id: int,
    body: MemberUpdate,
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    require(scope, account_id, Role.admin)
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user_id, Membership.account_id == account_id
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Member not found")
    membership.role = body.role.value
    db.commit()
    u = membership.user
    return MemberOut(user_id=u.id, email=u.email, name=u.name, role=body.role, is_active=u.is_active)


@router.delete("/{account_id}/members/{user_id}", status_code=204)
def remove_member(
    account_id: int,
    user_id: int,
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    require(scope, account_id, Role.admin)
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user_id, Membership.account_id == account_id
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Member not found")
    db.delete(membership)
    db.commit()
