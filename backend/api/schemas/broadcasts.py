from pydantic import BaseModel, Field


# The design branch declared `numbers` as a bare `list[str] | None`. Every
# request list on this platform carries a ceiling
# (`tests/test_a_request_list_cannot_grow_without_bound.py`): an unbounded one
# is a body a caller can grow without limit, and each entry here becomes a
# contact upsert, so the cost is paid per element. Five thousand is far above
# any real campaign and far below anything that would hold a worker for
# minutes.
MAX_BROADCAST_NUMBERS = 5000


class BroadcastCreateRequest(BaseModel):
    name: str = Field(max_length=200)
    message_text: str = Field(max_length=4000)
    channel: str = Field(max_length=40)
    segment_id: int | None = None
    lifecycle_stage: str | None = Field(default=None, max_length=40)
    tag: str | None = Field(default=None, max_length=80)
    numbers: list[str] | None = Field(default=None, max_length=MAX_BROADCAST_NUMBERS)
    media_url: str | None = Field(default=None, max_length=1000)
    media_type: str | None = Field(default=None, max_length=20)
