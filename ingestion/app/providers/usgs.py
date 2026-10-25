from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.providers.errors import ProviderError, ProviderResponseError
from app.schemas.earthquake import FeatureCollection, NormalizedEarthquake

PAGE_SIZE = 20000


class USGSProvider:
    name = "usgs"
    endpoint = "https://earthquake.usgs.gov/fdsnws/event/1/query"

    def __init__(self, client: httpx.Client | None = None, timeout: float = 15.0):
        self.timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[NormalizedEarthquake]:
        end = end or datetime.now(UTC)
        start = start or end - timedelta(days=7)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        features = []
        offset = 1
        while True:
            try:
                response = self._client.get(
                    self.endpoint,
                    params={
                        "format": "geojson",
                        "eventtype": "earthquake",
                        "starttime": start.isoformat(),
                        "endtime": end.isoformat(),
                        "orderby": "time",
                        "limit": PAGE_SIZE,
                        "offset": offset,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                if response.status_code == 204:
                    break
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"USGS request failed: {exc}") from exc
            try:
                batch = FeatureCollection.model_validate(body).features
                features.extend(batch)
                if len(batch) < PAGE_SIZE:
                    break
                offset += len(batch)
            except Exception as exc:
                if isinstance(exc, ProviderResponseError):
                    raise
                raise ProviderResponseError(f"Invalid USGS earthquake response: {exc}") from exc
        return [self.normalize(feature) for feature in features]

    @staticmethod
    def normalize(feature: dict[str, Any]) -> NormalizedEarthquake:
        try:
            properties = feature["properties"]
            coordinates = feature["geometry"]["coordinates"]
            event_type = properties["type"]
            if not isinstance(event_type, str) or event_type.lower() != "earthquake":
                raise ValueError("feature is not an earthquake")
            origin = datetime.fromtimestamp(properties["time"] / 1000, tz=UTC)
            event_id = str(feature["id"])
            values: dict[str, Any] = {
                "provider": "usgs",
                "provider_record_id": event_id,
                "provider_event_id": event_id,
                "origin_time": origin,
                "longitude": coordinates[0] if len(coordinates) > 0 else None,
                "latitude": coordinates[1] if len(coordinates) > 1 else None,
                "title": properties.get("title") or properties.get("place"),
                "raw_payload": feature,
            }
            if len(coordinates) > 2:
                values["depth_km"] = coordinates[2]
            if "mag" in properties:
                values["magnitude"] = properties["mag"]
            if "status" in properties:
                values["provider_status"] = properties["status"]
            if isinstance(properties.get("updated"), (int, float)):
                values["provider_updated_at"] = datetime.fromtimestamp(properties["updated"] / 1000, tz=UTC)
            return NormalizedEarthquake(**values)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ProviderResponseError(f"Invalid USGS earthquake feature: {exc}") from exc
