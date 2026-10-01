"""Pydantic 请求/响应模型。

里程单位为公里（无单位约定，只需全线一致），限速单位由调用方约定（如 km/h）。
Decimal 序列化为字符串以避免 JSON 浮点误差；时间统一为带偏移的 UTC。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, PlainSerializer
from pydantic import Field

# 以字符串收发 Decimal，杜绝二进制浮点误差。
DecimalIn = Annotated[
    Decimal, BeforeValidator(lambda v: Decimal(str(v).strip()))
]
DecimalOut = Annotated[
    Decimal, PlainSerializer(lambda d: format(d, "f"), return_type=str)
]


def parse_utc(value: object) -> datetime:
    """解析 ISO 8601 时间；必须带时区偏移，统一转换为 UTC。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            raise ValueError("时间必须为 ISO 8601 格式")
    else:
        raise ValueError("时间必须为 ISO 8601 格式")
    if dt.tzinfo is None:
        raise ValueError("时间必须带时区偏移，建议使用 UTC（Z 后缀）")
    return dt.astimezone(timezone.utc)


UtcDatetime = Annotated[
    datetime,
    BeforeValidator(parse_utc),
    PlainSerializer(
        lambda dt: dt.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        return_type=str,
    ),
]


class BaseSegmentIn(BaseModel):
    start_km: DecimalIn
    end_km: DecimalIn
    speed: DecimalIn


class InitializeLineIn(BaseModel):
    total_km: DecimalIn
    base_segments: list[BaseSegmentIn]


class CreateCommandIn(BaseModel):
    command_no: str = Field(min_length=1, max_length=64)
    start_km: DecimalIn
    end_km: DecimalIn
    speed: DecimalIn
    valid_from: UtcDatetime
    valid_to: UtcDatetime


class PublishCommandIn(BaseModel):
    expected_version: int = Field(ge=1)


class RevokeCommandIn(BaseModel):
    expected_version: int = Field(ge=1)


class BaseSegmentOut(BaseModel):
    start_km: DecimalOut
    end_km: DecimalOut
    speed: DecimalOut


class LineOut(BaseModel):
    total_km: DecimalOut
    base_segments: list[BaseSegmentOut]
    initialized: bool


class CommandOut(BaseModel):
    command_no: str
    start_km: DecimalOut
    end_km: DecimalOut
    speed: DecimalOut
    valid_from: UtcDatetime
    valid_to: UtcDatetime
    status: str
    version: int
    published_at: Optional[UtcDatetime] = None
    revoked_at: Optional[UtcDatetime] = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class RestrictionSegmentOut(BaseModel):
    start_km: DecimalOut
    end_km: DecimalOut
    speed: DecimalOut
    command_nos: list[str]


class RestrictionAtOut(BaseModel):
    at: UtcDatetime
    total_km: DecimalOut
    segments: list[RestrictionSegmentOut]


class OkOut(BaseModel):
    ok: bool
