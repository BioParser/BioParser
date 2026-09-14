from typing import Literal

from pydantic import BaseModel


class SubmitResponse(BaseModel):
    job_id: str
    status: Literal["queued"]
