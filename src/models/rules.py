from pydantic import BaseModel, Field
from typing import Optional, Literal
from enum import Enum


class RuleBlockType(str, Enum):
    ORDERING = "ordering"
    BATCHING = "batching"
    MATCHING = "matching"
    PRIORITY = "priority"
    FILTER = "filter"


class OrderingParams(BaseModel):
    method: Literal["FIFO", "price_time", "pro_rata"] = "FIFO"
    tiebreaker: Optional[Literal["fee_amount", "timestamp", "stake"]] = None


class BatchingParams(BaseModel):
    interval_ms: int = Field(ge=10, le=10000, default=100)
    max_batch: int = Field(ge=1, le=1000, default=50)
    min_batch: int = Field(ge=1, le=100, default=1)


class MatchingParams(BaseModel):
    engine: Literal["clob", "amm", "rfq"] = "clob"
    partial_fill: bool = True


class PriorityParams(BaseModel):
    factor: Literal["stake", "fee", "token_hold", "custom"] = "fee"
    weight: float = Field(ge=0, le=100, default=1.0)


class FilterParams(BaseModel):
    blacklist: list[str] = []
    whitelist: list[str] = []
    max_size: Optional[float] = None
    min_size: Optional[float] = None


class Position(BaseModel):
    x: float
    y: float


class RuleBlock(BaseModel):
    id: str
    type: RuleBlockType
    params: dict  # Union type handled dynamically
    position: Position
    connections: list[str] = []


class RuleSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=500, default="")
    blocks: list[RuleBlock]


class RuleSetResponse(BaseModel):
    id: str
    name: str
    description: str
    blocks: list[RuleBlock]
    owner: str
    created_at: int
    updated_at: int


class ValidateRequest(BaseModel):
    blocks: list[RuleBlock]


class ValidateResponse(BaseModel):
    valid: bool
    conflicts: list[str] = []
    warnings: list[str] = []
    cycle_detected: bool = False


class SimulateRequest(BaseModel):
    blocks: list[RuleBlock]
    sample_txs: int = Field(ge=1, le=20, default=5)


class SimulationTxResult(BaseModel):
    tx_id: str
    outcome: Literal["included", "filtered", "batched", "rejected"]
    position: Optional[int] = None
    batch_id: Optional[int] = None
    reason: Optional[str] = None


class SimulationReport(BaseModel):
    results: list[SimulationTxResult]
    total_txs: int
    processed: int
    filtered: int
    conflicts: list[str] = []


class ExportRequest(BaseModel):
    blocks: list[RuleBlock]
    format: Literal["json", "anchor"] = "json"
