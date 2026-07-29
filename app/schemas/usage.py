from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UsagePeriodResponse(BaseModel):
    application_id: UUID
    period_month: date
    request_count: int
    bytes_received: int
    cost_eur_cents: int
    model_config = ConfigDict(from_attributes=True)


class UsageSummaryResponse(BaseModel):
    application_id: UUID
    periods: list[UsagePeriodResponse]
    total_requests: int
    total_bytes: int
    total_cost_eur_cents: int
