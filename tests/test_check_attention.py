"""check_gpu --demo names the attention tier the running laptop loaded, so the demo is never silently rules-only."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_gpu  # noqa: E402


def test_reads_the_last_tier_line_of_the_running_laptop(tmp_path):
    log = tmp_path / "source.log"
    assert check_gpu.attention_tiers_running(log) is None
    log.write_text("[enrich] attention tiers: ['rules']\nnoise\n"
                   "[enrich] attention tiers: ['MoritzLaurer/deberta-v3-base-zeroshot-v2.0', 'rules']\n")
    assert check_gpu.attention_tiers_running(log) == ["MoritzLaurer/deberta-v3-base-zeroshot-v2.0", "rules"]


def test_predicts_every_configured_tier():
    names = [n for n, _ in check_gpu.attention_tiers_possible()]
    from laptop.enrich import config as EC
    assert names == list(EC.TIERS)
