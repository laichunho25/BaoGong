import json

from django.test import RequestFactory

from apps.core import views


def _get(rf: RequestFactory):
    return views.healthz(rf.get("/healthz"))


class TestHealthz:
    def test_returns_200_when_all_dependencies_are_up(self, monkeypatch):
        monkeypatch.setattr(views, "_check_database", lambda: (True, None))
        monkeypatch.setattr(views, "_check_redis", lambda: (True, None))

        response = _get(RequestFactory())
        payload = json.loads(response.content)

        assert response.status_code == 200
        assert payload["status"] == "ok"
        assert payload["checks"]["database"]["ok"] is True
        assert payload["checks"]["redis"]["ok"] is True

    def test_returns_503_and_names_the_failing_dependency(self, monkeypatch):
        monkeypatch.setattr(views, "_check_database", lambda: (True, None))
        monkeypatch.setattr(views, "_check_redis", lambda: (False, "connection refused"))

        response = _get(RequestFactory())
        payload = json.loads(response.content)

        assert response.status_code == 503
        assert payload["status"] == "degraded"
        assert payload["checks"]["redis"]["error"] == "connection refused"

    def test_response_is_not_cached(self, monkeypatch):
        monkeypatch.setattr(views, "_check_database", lambda: (True, None))
        monkeypatch.setattr(views, "_check_redis", lambda: (True, None))

        response = _get(RequestFactory())
        assert "no-cache" in response.headers.get("Cache-Control", "")


class TestDependencyChecks:
    def test_database_check_reports_failure_instead_of_raising(self, monkeypatch):
        class BrokenConnection:
            def cursor(self):
                raise RuntimeError("db is down")

        monkeypatch.setattr(views, "connection", BrokenConnection())
        ok, error = views._check_database()
        assert ok is False
        assert "db is down" in error

    def test_redis_check_reports_failure_instead_of_raising(self, settings):
        # Port 1 is never a Redis server; the check must degrade, not raise.
        settings.REDIS_URL = "redis://127.0.0.1:1/0"
        ok, error = views._check_redis()
        assert ok is False
        assert error
