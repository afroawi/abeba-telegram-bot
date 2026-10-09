import os
from dataclasses import dataclass
from urllib.parse import urlparse
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    api_base_url: str
    service_token: str
    mini_app_url: str
    health_host: str = "127.0.0.1"
    health_port: int = 8081

    @classmethod
    def load(cls):
        load_dotenv(dotenv_path=".env")
        try:
            health_port = int(os.getenv("ABEBA_BOT_HEALTH_PORT", "8081"))
            if not 1 <= health_port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("ABEBA_BOT_HEALTH_PORT must be an integer from 1 to 65535.") from None
        health_host = os.getenv("ABEBA_BOT_HEALTH_HOST", "127.0.0.1").strip()
        if not health_host:
            raise ValueError("Set a non-empty ABEBA_BOT_HEALTH_HOST.")
        result = cls(
            os.getenv("TELEGRAM_BOT_TOKEN", ""),
            os.getenv("ABEBA_API_BASE_URL", "http://127.0.0.1:8000/api/v1/"),
            os.getenv("ABEBA_TELEGRAM_SERVICE_TOKEN", ""),
            os.getenv("ABEBA_MINI_APP_URL", ""),
            health_host,
            health_port,
        )
        if not result.bot_token or not result.service_token:
            raise ValueError(
                "Set TELEGRAM_BOT_TOKEN and ABEBA_TELEGRAM_SERVICE_TOKEN in .env."
            )
        mini = urlparse(result.mini_app_url)
        base = urlparse(result.api_base_url)
        if (
            mini.scheme != "https"
            or not mini.netloc
            or mini.username
            or mini.password
            or mini.query
            or mini.fragment
        ):
            raise ValueError(
                "ABEBA_MINI_APP_URL must be a clean public HTTPS URL ending in /telegram/chat."
            )
        if not mini.path.rstrip("/").endswith(
            "/telegram/chat"
        ) or mini.hostname.endswith(".example"):
            raise ValueError(
                "Set ABEBA_MINI_APP_URL to your real frontend's /telegram/chat URL."
            )
        if (
            base.scheme not in {"http", "https"}
            or not base.netloc
            or base.username
            or base.password
        ):
            raise ValueError("Invalid ABEBA_API_BASE_URL.")
        if base.scheme == "http" and base.hostname not in {
            "localhost",
            "127.0.0.1",
            "backend",
        }:
            raise ValueError("Use HTTPS for a remote backend.")
        return result
