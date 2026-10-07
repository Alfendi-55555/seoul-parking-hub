"""static/fee.js(브라우저)와 app/fee.py(서버)가 같은 요금을 내는지 확인한다."""

import json
import shutil
import subprocess
from dataclasses import asdict

import pytest

from app import config
from app.fee import FeePolicy, fee_for_minutes, next_increase_minute

POLICIES = [
    FeePolicy(basic_min=5, basic_fee=430, add_min=5, add_fee=430),
    FeePolicy(basic_min=30, basic_fee=1000, add_min=10, add_fee=500, day_max=10000),
    FeePolicy(basic_min=60, basic_fee=2000, add_min=0, add_fee=0),
    FeePolicy(is_free=True),
    FeePolicy(),
]
MINUTES = [0, 1, 5, 6, 29, 30, 31, 59, 60, 61, 125, 600, 1439, 1440, 1441, 3000]


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없음")
def test_js_matches_python():
    script = """
const Fee = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map(([p, m]) => [Fee.feeForMinutes(p, m), Fee.nextIncreaseMinute(p, m)])));
"""
    cases = [(asdict(p), m) for p in POLICIES for m in MINUTES]
    out = subprocess.run(
        ["node", "-e", script, str(config.ROOT / "static" / "fee.js"), json.dumps(cases)],
        capture_output=True, text=True, check=True,
    ).stdout
    expected = [[fee_for_minutes(p, m), next_increase_minute(p, m)] for p in POLICIES for m in MINUTES]
    assert json.loads(out) == expected
