from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserDTO(DTO):
    id: UUID
    organization_id: UUID
    role: Literal["owner", "admin", "member"]


class OrganizationDTO(DTO):
    id: UUID
    name: str
    member_count: int
    member_limit: int


class SubscriptionDTO(DTO):
    id: UUID
    organization_id: UUID
    plan: Literal["free", "pro", "enterprise"]
    status: Literal["active", "past_due", "expired", "canceled"]
    expires_at: datetime | None


class PaymentDTO(DTO):
    id: UUID
    organization_id: UUID
    status: Literal["succeeded", "failed", "pending"]
    paid_at: datetime | None
    created_at: datetime


class PaymentHistoryDTO(DTO):
    items: list[PaymentDTO]
