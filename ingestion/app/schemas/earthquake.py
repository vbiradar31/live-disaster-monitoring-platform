from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NormalizedEarthquake(BaseModel):
    """Provider-neutral earthquake observation with its source feature retained."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["gdacs", "usgs"]
    provider_record_id: str = Field(min_length=1, max_length=250)
    provider_event_id: str = Field(min_length=1, max_length=200)
    provider_episode_id: str | None = Field(default=None, max_length=200)
    origin_time: datetime
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    magnitude: float | None = Field(default=None, allow_inf_nan=False)
    depth_km: float | None = Field(default=None, allow_inf_nan=False)
    provider_status: str | None = Field(default=None, max_length=80)
    provider_updated_at: datetime | None = None
    title: str | None = None
    raw_payload: dict[str, Any]

    @field_validator("origin_time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("origin_time must include a timezone")
        return value

    @model_validator(mode="after")
    def require_coordinate_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must both be present or both be absent")
        return self


class FeatureCollection(BaseModel):
    """Minimal container used to validate source response structure."""

    type: Literal["FeatureCollection"]
    features: list[dict[str, Any]]
