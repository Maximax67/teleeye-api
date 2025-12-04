from typing import Optional
from pydantic import BaseModel


class ReadRequest(BaseModel):
    message_thread_id: Optional[int] = None
    message_id: Optional[int] = None
