from typing import Optional
from pydantic import BaseModel


class FileInfoResponse(BaseModel):
    file_id: str
    file_unique_id: str
    file_size: Optional[int]
