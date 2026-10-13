"""Brands whose shopping bags the edge agents should recognise."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_scope, require
from ..models import Brand, BrandTemplate, Role
from ..schemas import BrandIn, BrandOut, BrandTemplateOut
from ..tenancy import AccessScope, AccountTree

router = APIRouter(prefix="/api/v1", tags=["brands"])

MAX_TEMPLATE_BYTES = 2 * 1024 * 1024
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp"}


def brand_out(brand: Brand, inherited: bool = False) -> BrandOut:
    return BrandOut(
        id=brand.id,
        account_id=brand.account_id,
        name=brand.name,
        colors=brand.colors or [],
        templates=[
            BrandTemplateOut(id=t.id, filename=t.filename, sha256=t.sha256) for t in brand.templates
        ],
        inherited=inherited,
    )


def effective_brands(db: Session, tree: AccountTree, account_id: int) -> list[tuple[Brand, bool]]:
    """Brands defined on the account or any ancestor, nearest definition wins by name."""
    chain = tree.ancestors(account_id)
    brands = db.scalars(select(Brand).where(Brand.account_id.in_(chain))).all()
    depth = {acc: i for i, acc in enumerate(chain)}
    chosen: dict[str, Brand] = {}
    for b in sorted(brands, key=lambda b: depth[b.account_id]):
        chosen.setdefault(b.name.lower(), b)
    return [(b, b.account_id != account_id) for b in sorted(chosen.values(), key=lambda b: b.name)]


def _get_brand(db: Session, scope: AccessScope, brand_id: int, minimum: Role) -> Brand:
    brand = db.get(Brand, brand_id)
    if brand is None or brand.account_id not in scope.account_ids:
        raise HTTPException(status_code=404, detail="Brand not found")
    require(scope, brand.account_id, minimum)
    return brand


@router.get("/accounts/{account_id}/brands", response_model=list[BrandOut])
def list_brands(
    account_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    require(scope, account_id, Role.viewer)
    return [brand_out(b, inherited) for b, inherited in effective_brands(db, scope.tree, account_id)]


@router.post("/accounts/{account_id}/brands", response_model=BrandOut, status_code=201)
def create_brand(
    account_id: int,
    body: BrandIn,
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    require(scope, account_id, Role.editor)
    brand = Brand(
        account_id=account_id,
        name=body.name.strip(),
        colors=[c.model_dump(mode="json") for c in body.colors],
    )
    db.add(brand)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Brand already exists on this account")
    return brand_out(brand)


@router.put("/brands/{brand_id}", response_model=BrandOut)
def update_brand(
    brand_id: int,
    body: BrandIn,
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    brand = _get_brand(db, scope, brand_id, Role.editor)
    brand.name = body.name.strip()
    brand.colors = [c.model_dump(mode="json") for c in body.colors]
    db.commit()
    return brand_out(brand)


@router.delete("/brands/{brand_id}", status_code=204)
def delete_brand(
    brand_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    brand = _get_brand(db, scope, brand_id, Role.editor)
    db.delete(brand)
    db.commit()


@router.post("/brands/{brand_id}/templates", response_model=BrandTemplateOut, status_code=201)
async def upload_template(
    brand_id: int,
    file: UploadFile = File(...),
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    brand = _get_brand(db, scope, brand_id, Role.editor)
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="Upload a PNG, JPEG or WebP logo")
    data = await file.read(MAX_TEMPLATE_BYTES + 1)
    if len(data) > MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=413, detail="Logo image must be under 2 MB")
    template = BrandTemplate(
        brand_id=brand.id,
        filename=(file.filename or "logo")[:255],
        content_type=file.content_type,
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
    )
    db.add(template)
    db.commit()
    return BrandTemplateOut(id=template.id, filename=template.filename, sha256=template.sha256)


@router.get("/brand-templates/{template_id}")
def get_template(
    template_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    template = db.get(BrandTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    _get_brand(db, scope, template.brand_id, Role.viewer)
    return Response(content=template.data, media_type=template.content_type)


@router.delete("/brand-templates/{template_id}", status_code=204)
def delete_template(
    template_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    template = db.get(BrandTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    _get_brand(db, scope, template.brand_id, Role.editor)
    db.delete(template)
    db.commit()
