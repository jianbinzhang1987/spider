"""Data models for the component search system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class SearchType(StrEnum):
    MPN = "mpn"
    SKU = "sku"


class QueryStatus(StrEnum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(slots=True)
class PriceBreak:
    quantity: int
    unit_price: float


@dataclass(slots=True)
class PartResult:
    supplier: str
    query: str
    status: QueryStatus
    mpn: str | None = None
    sku: str | None = None
    brand: str | None = None
    package: str | None = None
    description: str | None = None
    stock: int | None = None
    moq: int | None = None
    price_unit: float | None = None
    price_breaks: list[PriceBreak] = field(default_factory=list)
    lead_time: str | None = None
    product_url: str | None = None
    datasheet_url: str | None = None
    error_message: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier,
            "query": self.query,
            "status": self.status.value,
            "mpn": self.mpn,
            "sku": self.sku,
            "brand": self.brand,
            "package": self.package,
            "description": self.description,
            "stock": self.stock,
            "moq": self.moq,
            "price_unit": self.price_unit,
            "price_breaks": [
                {"quantity": pb.quantity, "unit_price": pb.unit_price}
                for pb in self.price_breaks
            ],
            "lead_time": self.lead_time,
            "product_url": self.product_url,
            "datasheet_url": self.datasheet_url,
            "error_message": self.error_message,
            "fetched_at": self.fetched_at.isoformat(),
        }
