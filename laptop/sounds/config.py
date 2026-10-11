"""sounds tunables: the `sounds:` section of laptop/config.yaml as UPPER_CASE module attributes. Edit the YAML."""

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

globals().update({k.upper(): v for k, v in
                  ((yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("sounds") or {}).items()})
