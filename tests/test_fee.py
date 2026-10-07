from datetime import datetime, timedelta

import pytest

from app import config, db, repo
from app.fee import FeePolicy, fee_for_minutes, next_increase_minute

# 세종로 공영주차장: 기본 5분 430원, 추가 5분당 430원
SEJONG = FeePolicy(basic_min=5, basic_fee=430, add_min=5, add_fee=430)
# 기본 30분 1000원, 추가 10분당 500원, 1일 최대 10000원
CAPPED = FeePolicy(basic_min=30, basic_fee=1000, add_min=10, add_fee=500, day_max=10000)


@pytest.mark.parametrize("minutes,fee", [(0, 430), (5, 430), (6, 860), (60, 5160)])
def test_basic_and_additional(minutes, fee):
    assert fee_for_minutes(SEJONG, minutes) == fee


@pytest.mark.parametrize("minutes,fee", [
    (30, 1000), (31, 1500), (40, 1500), (41, 2000),
    (600, 10000),                 # 하루 최대
    (1440, 10000),                # 정확히 24시간
    (1441, 11000),                # 다음 날 기본요금 시작
    (2880, 20000),
])
def test_day_max(minutes, fee):
    assert fee_for_minutes(CAPPED, minutes) == fee


def test_free_and_unknown():
    assert fee_for_minutes(FeePolicy(is_free=True, basic_fee=500), 100) == 0
    assert fee_for_minutes(FeePolicy(), 100) == 0
    assert next_increase_minute(FeePolicy(), 10) is None


def test_next_increase():
    assert next_increase_minute(SEJONG, 0) == 5
    assert next_increase_minute(SEJONG, 7) == 10
    assert next_increase_minute(CAPPED, 31) == 40
    # 최대요금에 도달하면 다음 날로 넘어갈 때 오른다
    assert next_increase_minute(CAPPED, 600) == 1440


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    c = db.connect()
    yield c
    c.close()


def test_trigger_sets_final_fee(conn):
    lot = {"id": "S-1", "name": "테스트", "fee": {"is_free": False, "basic_min": 5, "basic_fee": 430,
                                                "add_min": 5, "add_fee": 430, "day_max": 0}}
    start = datetime(2026, 10, 7, 9, 0, tzinfo=config.KST)
    s = repo.start_session(conn, lot, start)
    assert s["final"] is False

    done = repo.end_session(conn, s["id"], start + timedelta(minutes=62, seconds=10))
    # 62분 10초 → 63분 → 기본 5분 + 추가 12단위(58분 올림) = 430 + 12*430
    assert done["final"] is True
    assert done["fee"] == 430 + 12 * 430
    # 트리거가 계산한 값이 화면 미터 계산과 같다
    assert done["fee"] == fee_for_minutes(SEJONG, 63)

    # 이미 출차한 기록은 다시 바뀌지 않는다
    again = repo.end_session(conn, s["id"], start + timedelta(hours=5))
    assert again["fee"] == done["fee"]
