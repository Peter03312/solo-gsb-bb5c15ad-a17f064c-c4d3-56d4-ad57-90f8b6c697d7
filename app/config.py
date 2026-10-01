from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_prefix: str
    app_host: str
    app_port: int


def _load() -> Settings:
    return Settings(
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://speed:speed@localhost:5432/speed_restriction",
        ),
        api_prefix=os.getenv("API_PREFIX", "/api"),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=int(os.getenv("APP_PORT", "8080")),
    )


settings = _load()
