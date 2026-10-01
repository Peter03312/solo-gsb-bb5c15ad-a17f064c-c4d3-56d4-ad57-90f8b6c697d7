"""Pydantic request and response models."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from pydantic.alias_generators import to_camel


def finite_decimal(value: Any, *, minimum_exclusive: bool = False) -> Decimal:
    """Parse a JSON number/string and reject booleans, NaN, and infinity."""
    if isinstance(value, bool):
        raise ValueError("不接受布尔值")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("必须是有限十进制数字")
    if not result.is_finite():
        raise ValueError("不接受 NaN 或无穷大")
    if minimum_exclusive and result <= 0:
        raise ValueError("必须大于 0")
    return result


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class BaseSegmentIn(CamelModel):
    start_mileage: Any
    end_mileage: Any
    speed: Any

    @field_validator("start_mileage")
    @classmethod
    def start_must_be_non_negative(cls, value: Any) -> Decimal:
        result = finite_decimal(value)
        if result < 0:
            raise ValueError("起点里程不能为负")
        return result

    @field_validator("end_mileage", "speed")
    @classmethod
    def positive(cls, value: Any) -> Decimal:
        return finite_decimal(value, minimum_exclusive=True)


class LineIn(CamelModel):
    total_mileage: Any
    base_segments: list[BaseSegmentIn] = Field(min_length=1)

    @field_validator("total_mileage")
    @classmethod
    def positive_total(cls, value: Any) -> Decimal:
        return finite_decimal(value, minimum_exclusive=True)

    def ordered_segments(self) -> list[BaseSegmentIn]:
        segments = sorted(
            self.base_segments, key=lambda item: (item.start_mileage, item.end_mileage)
        )
        if segments[0].start_mileage != 0:
            raise ValueError("基础限速段必须从里程 0 开始，且不能存在缺口")
        for previous, current in zip(segments, segments[1:]):
            if current.start_mileage < previous.end_mileage:
                raise ValueError("基础限速段不能重叠")
            if current.start_mileage > previous.end_mileage:
                raise ValueError("基础限速段必须连续覆盖全线，不能存在缺口")
        if segments[-1].end_mileage != self.total_mileage:
            raise ValueError("基础限速段必须覆盖到线路总里程")
        return segments


class CommandIn(CamelModel):
    number: str = Field(min_length=1, max_length=128)
    start_mileage: Any
    end_mileage: Any
    speed: Any
    effective_start: datetime
    effective_end: datetime

    @field_validator("number")
    @classmethod
    def non_blank_number(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("命令编号不能为空")
        return value

    @field_validator("start_mileage")
    @classmethod
    def start_must_be_non_negative(cls, value: Any) -> Decimal:
        result = finite_decimal(value)
        if result < 0:
            raise ValueError("起点里程不能为负")
        return result

    @field_validator("end_mileage", "speed")
    @classmethod
    def positive(cls, value: Any) -> Decimal:
        return finite_decimal(value, minimum_exclusive=True)

    @field_validator("effective_start", "effective_end")
    @classmethod
    def utc_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("时间必须带时区；请使用 UTC ISO-8601 格式")
        return value.astimezone(timezone.utc)

    def validate_range(self, total_mileage: Decimal) -> None:
        if self.end_mileage <= self.start_mileage:
            raise ValueError("里程范围必须非空且按升序排列")
        if self.end_mileage > total_mileage:
            raise ValueError("里程范围越界，终点不能超过线路总里程")
        if self.effective_end <= self.effective_start:
            raise ValueError("生效时段必须非空且按升序排列")


class ExpectedVersionMixin(CamelModel):
    expected_version: int = Field(ge=0)


class PublishCommandIn(ExpectedVersionMixin):
    pass


class CancelCommandIn(ExpectedVersionMixin):
    pass


class QueryAtIn(CamelModel):
    at: datetime

    @field_validator("at")
    @classmethod
    def utc_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("查询时刻必须带时区；请使用 UTC ISO-8601 格式")
        return value.astimezone(timezone.utc)


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_mileage: Decimal
    end_mileage: Decimal
    speed: Decimal
    command_numbers: list[str] = []

    @field_serializer("start_mileage", "end_mileage", "speed")
    def decimal_text(self, value: Decimal) -> str:
        return format(value, "f")


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_mileage: Decimal
    base_segments: list[SegmentOut]

    @field_serializer("total_mileage")
    def decimal_text(self, value: Decimal) -> str:
        return format(value, "f")


class CommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: str
    start_mileage: Decimal
    end_mileage: Decimal
    speed: Decimal
    effective_start: datetime
    effective_end: datetime
    status: Literal["draft", "published", "cancelled"]
    version: int
    created_at: datetime
    published_at: datetime | None
    cancelled_at: datetime | None

    @field_serializer("start_mileage", "end_mileage", "speed")
    def decimal_text(self, value: Decimal) -> str:
        return format(value, "f")

    @field_serializer("effective_start", "effective_end", "created_at",
                     "published_at", "cancelled_at")
    def utc_text(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class QueryResponse(BaseModel):
    at: datetime
    total_mileage: Decimal
    segments: list[SegmentOut]

    @field_serializer("at")
    def utc_text(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @field_serializer("total_mileage")
    def decimal_text(self, value: Decimal) -> str:
        return format(value, "f")
