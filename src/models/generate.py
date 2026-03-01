from pydantic import BaseModel, Field
from typing import Optional


class GenerateRequest(BaseModel):
    description: str = Field(min_length=3, max_length=2000)
    language: Optional[str] = "en"


class GeneratedFile(BaseModel):
    path: str
    content: str
    language: str


class GenerateResponse(BaseModel):
    name: str
    description: str
    files: list[GeneratedFile]
    instructions: list[str]
    test_count: int = 0
