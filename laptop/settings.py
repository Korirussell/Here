"""laptop/config.yaml + secrets from the environment or laptop/.env (SPEC §22: secrets never committed)."""

import os
from pathlib import Path

import yaml

LAPTOP = Path(__file__).resolve().parent
ROOT = LAPTOP.parent
CONFIG_PATH = LAPTOP / "config.yaml"


def read_dotenv(path):
    """Minimal .env reader: KEY=VALUE lines, # comments, optional quotes -> dict (the environment untouched)."""
    path = Path(path)
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        end = value.find(value[:1], 1) if value[:1] in ("'", '"') else -1
        if end > 0:                                                  # quoted: up to the closing quote, '#' kept
            value = value[1:end]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()   # trailing comments, as in .env.example
        out[key.strip()] = value
    return out


def load_dotenv(path):
    """read_dotenv into os.environ. Existing env vars win."""
    for key, value in read_dotenv(path).items():
        os.environ.setdefault(key, value)


def load(path=CONFIG_PATH):
    load_dotenv(LAPTOP / ".env")
    load_dotenv(ROOT / ".env")
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    relay = cfg["relay"]
    relay["url"] = os.environ.get("HERE_RELAY_URL", relay["url"])
    relay["session"] = os.environ.get("HERE_SESSION", relay["session"])
    relay["token"] = os.environ.get("HERE_RELAY_TOKEN", "")
    env_mirrors = [u.strip() for u in os.environ.get("HERE_RELAY_MIRRORS", "").split(",") if u.strip()]
    relay["mirrors"] = [u for u in dict.fromkeys([*(relay.get("mirrors") or []), *env_mirrors]) if u != relay["url"]]
    return cfg
