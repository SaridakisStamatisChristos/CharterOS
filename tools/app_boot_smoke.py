from fastapi.testclient import TestClient

from apps.api.main import create_app
from charteros.shared.config import Settings


def main() -> None:
    settings = Settings(
        environment="test",
        service_name="charteros-smoke",
        database_url="postgresql+psycopg://charteros:smoke-only@localhost:5432/charteros",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "ok":
            raise RuntimeError(f"unexpected health payload: {body!r}")


if __name__ == "__main__":
    main()
