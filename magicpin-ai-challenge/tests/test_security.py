import pytest


@pytest.mark.parametrize("path", ["/v1/context", "/v1/tick", "/v1/reply", "/v1/teardown"])
def test_public_writes_require_token(client, monkeypatch, path):
    monkeypatch.setenv("VERA_API_TOKEN", "deployment-test-token")
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = client.post(path, json={}, headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_public_health_and_docs_remain_available(client, monkeypatch):
    monkeypatch.setenv("VERA_API_TOKEN", "deployment-test-token")
    for path in ("/", "/v1/healthz", "/v1/metadata", "/docs", "/openapi.json"):
        response = client.get(path)
        assert response.status_code == 200
        assert "deployment-test-token" not in response.text
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/v1/tick"]["post"]["security"] == [{"HTTPBearer": []}]


def test_correct_bearer_token_allows_tick_and_teardown(client, monkeypatch):
    monkeypatch.setenv("VERA_API_TOKEN", "deployment-test-token")
    headers = {"Authorization": "Bearer deployment-test-token"}
    response = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z"}, headers=headers)
    assert response.status_code == 200 and response.json() == {"actions": []}
    assert client.post("/v1/teardown", headers=headers).json() == {"wiped": True}


def test_missing_required_deployment_secret_fails_closed(client, monkeypatch):
    monkeypatch.setenv("VERA_REQUIRE_AUTH", "true")
    assert client.post("/v1/teardown").status_code == 503
    assert client.get("/v1/healthz").status_code == 200
