from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.department_service import department_service


router = APIRouter(prefix="/api/departments", tags=["Departments"])


class CreateDepartmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.get("")
def list_departments(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(current_user)
    return {"departments": department_service.list_for_company(company_id=company_id)}


@router.post("")
def create_department(
    payload: CreateDepartmentRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    try:
        departments = department_service.create(company_id=company_id, name=payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"departments": departments}


@router.delete("/{name}")
def delete_department(
    name: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    try:
        departments = department_service.delete(company_id=company_id, name=name)
    except KeyError:
        raise HTTPException(status_code=404, detail="Department not found")
    return {"departments": departments}
