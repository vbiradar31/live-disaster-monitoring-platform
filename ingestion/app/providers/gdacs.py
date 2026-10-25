from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.providers.errors import ProviderError, ProviderResponseError
from app.schemas.earthquake import FeatureCollection, NormalizedEarthquake

PAGE_SIZE = 100


class GDACSProvider:
    name = "gdacs"
    endpoint = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"

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
        page = 1
        while True:
            try:
                response = self._client.get(
                    self.endpoint,
                    params={
                        "eventlist": "EQ",
                        "fromdate": start.date().isoformat(),
                        "todate": end.date().isoformat(),
                        "pagesize": PAGE_SIZE,
                        "pagenumber": page,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                if response.status_code == 204:
                    break
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"GDACS request failed: {exc}") from exc
            try:
                batch = FeatureCollection.model_validate(body).features
                features.extend(batch)
                if len(batch) < PAGE_SIZE:
                    break
                page += 1
            except Exception as exc:
                if isinstance(exc, ProviderResponseError):
                    raise
                raise ProviderResponseError(f"Invalid GDACS earthquake response: {exc}") from exc
        return [self.normalize(feature) for feature in features]

    @staticmethod
    def normalize(feature: dict[str, Any]) -> NormalizedEarthquake:
        try:
            properties = feature["properties"]
            geometry = feature["geometry"]
            coords = geometry["coordinates"]
            event_type = properties["eventtype"]
            if not isinstance(event_type, str) or event_type.upper() != "EQ":
                raise ValueError("feature is not an earthquake")
            event_id = properties["eventid"]
            episode_id = properties.get("episodeid")
            if episode_id is None:
                raise ValueError("missing GDACS episodeid")
            severity = properties.get("severitydata") or {}
            magnitude = severity.get("severity")
            unit = severity.get("severityunit")
            if magnitude is not None and (
                not isinstance(unit, str) or unit.upper() not in {"M", "MAGNITUDE"}
            ):
                magnitude = None
            origin = datetime.fromisoformat(properties["fromdate"].replace("Z", "+00:00"))
            values: dict[str, Any] = {
                "provider": "gdacs",
                "provider_record_id": f"{event_id}:{episode_id}",
                "provider_event_id": str(event_id),
                "provider_episode_id": str(episode_id),
                "origin_time": origin,
                "latitude": coords[1] if len(coords) > 1 else None,
                "longitude": coords[0] if len(coords) > 0 else None,
                "title": properties.get("eventname") or properties.get("name"),
                "raw_payload": feature,
            }
            if "severity" in severity and isinstance(unit, str) and unit.upper() in {"M", "MAGNITUDE"}:
                values["magnitude"] = magnitude
            if isinstance(properties.get("status"), str):
                values["provider_status"] = properties["status"]
            modified = properties.get("datemodified")
            if isinstance(modified, str):
                values["provider_updated_at"] = datetime.fromisoformat(modified.replace("Z", "+00:00"))
            return NormalizedEarthquake(**values)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ProviderResponseError(f"Invalid GDACS earthquake feature: {exc}") from exc
