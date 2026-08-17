"""The knowledge base the assistant answers from.

The previous router at this path was removed rather than patched. It had two
independent faults: every call returned HTTP 500 because it invoked methods its
manager class never had, and it had no authentication at all — including the
POST and DELETE routes, so anyone who knew the URL could rewrite what the
assistant tells customers.

This one enforces ``knowledge.view`` on reads and ``knowledge.manage`` on
writes, and never takes a company from the request body or query string: the
company is resolved from the caller's token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.schemas.knowledge import (
    KnowledgeCategoryCreate,
    KnowledgeItemCreate,
    KnowledgeItemUpdate,
)
from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.knowledge_service import knowledge_service


router = APIRouter(prefix="/api/knowledge", tags=["Knowledge"])


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("knowledge.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("knowledge.manage"))):
    return _context(current_user)


# ----------------------------------------------------------------------
# Categories and filter options
#
# Declared before /{item_id} so a literal path segment is never parsed as an id.
# ----------------------------------------------------------------------


@router.get("/categories")
def list_categories(context=Depends(view_context)):
    _, company_id = context
    return {"items": knowledge_service.list_categories(company_id=company_id)}


@router.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: KnowledgeCategoryCreate,
    context=Depends(manage_context),
):
    _, company_id = context

    try:
        return knowledge_service.create_category(
            company_id=company_id,
            name=payload.name,
            department=payload.department,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/options")
def list_options(context=Depends(view_context)):
    """Everything the list screen needs to build its filters."""
    _, company_id = context

    return {
        "departments": knowledge_service.departments(company_id=company_id),
        "categories": knowledge_service.list_categories(company_id=company_id),
        "statuses": list(knowledge_service.ALLOWED_STATUS),
    }


# ----------------------------------------------------------------------
# Items
# ----------------------------------------------------------------------


@router.get("")
def list_items(
    search: str | None = Query(default=None, max_length=200),
    department: str | None = Query(default=None, max_length=60),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    try:
        return knowledge_service.list_items(
            company_id=company_id,
            search=search,
            department=department,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: KnowledgeItemCreate,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        item = knowledge_service.create_item(
            company_id=company_id,
            data=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # The title, never the content. The log records that the assistant's
    # knowledge changed and who changed it; a copy of the answer text would
    # duplicate the base into a table with different retention, and the base is
    # already the record of what it says.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.KNOWLEDGE_CREATED,
        category="knowledge",
        target_type="knowledge_item",
        target_id=item.get("id"),
        summary=f"Taught the assistant: {item.get('title')}",
        after={"title": item.get("title"), "status": item.get("status")},
        ip_address=client_ip(request),
    )

    return item


@router.get("/{item_id}")
def get_item(item_id: int, context=Depends(view_context)):
    _, company_id = context
    item = knowledge_service.get_item(company_id=company_id, item_id=item_id)

    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    return item


@router.put("/{item_id}")
def update_item(
    item_id: int,
    payload: KnowledgeItemUpdate,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    previous = knowledge_service.get_item(company_id=company_id, item_id=item_id)

    try:
        item = knowledge_service.update_item(
            company_id=company_id,
            item_id=item_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.KNOWLEDGE_UPDATED,
        category="knowledge",
        target_type="knowledge_item",
        target_id=item_id,
        summary=f"Edited what the assistant knows about: {item.get('title')}",
        before={
            "title": (previous or {}).get("title"),
            "status": (previous or {}).get("status"),
        },
        after={"title": item.get("title"), "status": item.get("status")},
        ip_address=client_ip(request),
    )

    return item


@router.delete("/{item_id}")
def delete_item(
    item_id: int,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    previous = knowledge_service.get_item(company_id=company_id, item_id=item_id)

    if not knowledge_service.delete_item(company_id=company_id, item_id=item_id):
        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.KNOWLEDGE_DELETED,
        category="knowledge",
        target_type="knowledge_item",
        target_id=item_id,
        summary=(
            f"Removed what the assistant knew about: "
            f"{(previous or {}).get('title') or item_id}"
        ),
        before={"title": (previous or {}).get("title")},
        ip_address=client_ip(request),
    )

    return {"success": True}
