import json
from pathlib import Path

import httpx
import pytest

from app.providers.errors import ProviderError, ProviderResponseError
from app.providers.gdacs import GDACSProvider
from app.providers.usgs import USGSProvider
from app.providers import gdacs as gdacs_module
from app.providers import usgs as usgs_module


FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("provider", "fixture", "expected_provider", "expected_id", "expected_depth"),
    [
        (GDACSProvider, "gdacs_earthquake.json", "gdacs", "1476137:1632460", None),
        (USGSProvider, "usgs_earthquake.json", "usgs", "us7000test", 24.0),
    ],
)
def test_provider_normalizes_geojson_feature(provider, fixture, expected_provider, expected_id, expected_depth):
    feature = read_fixture(fixture)["features"][0]
    observation = provider.normalize(feature)

    assert observation.provider == expected_provider
    assert observation.provider_record_id == expected_id
    assert observation.provider_event_id == ("1476137" if expected_provider == "gdacs" else expected_id)
    assert observation.provider_episode_id == ("1632460" if expected_provider == "gdacs" else None)
    assert observation.origin_time.isoformat() == "2026-10-24T15:12:36+00:00"
    assert (observation.latitude, observation.longitude) == (38.322, 142.369)
    assert observation.magnitude == 6.8
    assert observation.depth_km == expected_depth
    assert observation.raw_payload == feature


@pytest.mark.parametrize("provider, fixture", [(GDACSProvider, "gdacs_earthquake.json"), (USGSProvider, "usgs_earthquake.json")])
def test_fetch_uses_feed_response_and_returns_normalized_items(provider, fixture):
    payload = read_fixture(fixture)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = provider(client=client).fetch()

    assert len(results) == 1


@pytest.mark.parametrize("provider", [GDACSProvider, USGSProvider])
def test_fetch_wraps_http_failures_as_provider_errors(provider):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            provider(client=client).fetch()


def test_invalid_gdacs_feature_is_rejected():
    with pytest.raises(ProviderResponseError):
        GDACSProvider.normalize({"type": "Feature", "properties": {}, "geometry": None})


def test_usgs_explicit_null_fields_remain_distinguishable_from_absent_fields():
    feature = read_fixture("usgs_earthquake.json")["features"][0]
    feature["properties"]["mag"] = None
    feature["properties"]["status"] = None

    observation = USGSProvider.normalize(feature)

    assert "magnitude" in observation.model_fields_set
    assert observation.magnitude is None
    assert "provider_status" in observation.model_fields_set
    assert observation.provider_status is None


def test_normalized_earthquake_rejects_partial_coordinate_pairs():
    from app.schemas.earthquake import NormalizedEarthquake

    with pytest.raises(ValueError):
        NormalizedEarthquake(
            provider="usgs",
            provider_record_id="us-test",
            provider_event_id="us-test",
            origin_time="2026-10-24T15:12:36Z",
            latitude=38.0,
            raw_payload={},
        )


@pytest.mark.parametrize(
    ("provider", "module", "page_param", "offsets"),
    [
        (GDACSProvider, gdacs_module, "pagenumber", ["1", "2"]),
        (USGSProvider, usgs_module, "offset", ["1", "3"]),
    ],
)
def test_provider_fetches_all_pages(provider, module, page_param, offsets, monkeypatch):
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    fixture_name = "gdacs_earthquake.json" if provider is GDACSProvider else "usgs_earthquake.json"
    feature = read_fixture(fixture_name)["features"][0]
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.params[page_param])
        batch_size = 2 if len(requested) == 1 else 1
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [feature] * batch_size})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = provider(client=client).fetch()

    assert requested == offsets
    assert len(results) == 3


@pytest.mark.parametrize("provider", [GDACSProvider, USGSProvider])
def test_empty_no_content_response_is_successful(provider):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert provider(client=client).fetch() == []
