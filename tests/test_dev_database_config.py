import pytest

from scripts.dev_database import UnsafeDatabaseTarget, sanitized_target, validate_database_url


def test_development_url_is_reported_without_credentials():
    url = validate_database_url(
        "postgresql+psycopg://user:private-value@localhost:5432/disaster_monitoring"
    )

    result = sanitized_target(url)
    assert result == "host=localhost port=5432 database=disaster_monitoring"
    assert "private-value" not in result
    assert "user" not in result


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://ldmp_test:fixture@127.0.0.1:55432/postgres",
        "postgresql+psycopg://user:fixture@localhost:5432/ldmp_it_generated_db",
        "postgresql+psycopg://ldmp_test:fixture@localhost:5432/development_db",
    ],
)
def test_integration_database_urls_are_rejected(url):
    with pytest.raises(UnsafeDatabaseTarget, match="integration-test"):
        validate_database_url(url)


@pytest.mark.parametrize(
    "url",
    [None, "not-a-url", "sqlite:///local.db", "postgresql:///missing_host", "postgresql://user:fixture@localhost/db"],
)
def test_missing_or_invalid_development_urls_are_rejected(url):
    with pytest.raises(UnsafeDatabaseTarget):
        validate_database_url(url)
