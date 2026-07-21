from typing import Any
from pydantic import BaseModel, Field


class CompanySettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
