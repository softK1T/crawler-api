from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ArchiveEntryResponse(BaseModel):
    id: UUID
    url: str
    warc_filename: str
    offset: int
    length: int
    sha256: str
    is_revisit: bool
    content_type: str | None
    status_code: int | None
    captured_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ArchiveContentResponse(BaseModel):
    url: str
    status_code: int | None
    content_type: str | None
    body_b64: str
    captured_at: datetime
    warc_filename: str
    is_revisit: bool
    sha256: str
