"""설정 로딩. .env(비밀값) + config/*.yaml(수집 규칙)을 한곳에서 읽는다."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _load_yaml(name: str) -> dict:
    path = ROOT / "config" / name
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


SOURCES: list[dict] = _load_yaml("sources.yaml").get("sources", [])
FILTERS: dict = _load_yaml("filters.yaml")

DATA_GO_KR_KEY = os.getenv("DATA_GO_KR_KEY", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

DB_PATH = ROOT / "data" / "listings.db"
DASHBOARD_PATH = ROOT / "data" / "dashboard.html"
LOG_DIR = ROOT / "logs"
RAW_DIR = ROOT / "data" / "raw"

USER_AGENT = "housing-alert-bot/1.0 (personal use; contact via github)"
