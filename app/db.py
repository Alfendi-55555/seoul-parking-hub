import sqlite3
from contextlib import contextmanager

from . import config
from .fee import FeePolicy, elapsed_minutes, fee_for_minutes, parse_time

SCHEMA = """
-- 전국주차장정보표준데이터 (서울만 적재)
CREATE TABLE IF NOT EXISTS standard_lots (
    lot_no      TEXT PRIMARY KEY,      -- 주차장관리번호
    name        TEXT NOT NULL,
    category    TEXT,                  -- 공영/민영
    kind        TEXT,                  -- 노상/노외/부설
    road_addr   TEXT,
    jibun_addr  TEXT,
    capacity    INTEGER,
    oper_days   TEXT,
    weekday_hours TEXT,
    sat_hours   TEXT,
    holiday_hours TEXT,
    fee_info    TEXT,                  -- 무료/유료/혼합
    basic_min   INTEGER, basic_fee INTEGER,
    add_min     INTEGER, add_fee   INTEGER,
    day_max     INTEGER,
    payment     TEXT,
    note        TEXT,
    phone       TEXT,
    lat         REAL, lng REAL,
    ref_date    TEXT
);

-- 서울시 시영주차장 실시간 주차대수 (GetParkingInfo)
CREATE TABLE IF NOT EXISTS seoul_lots (
    pklt_cd     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    addr        TEXT,
    kind        TEXT,
    oper        TEXT,                  -- 시간제/정기권 등
    phone       TEXT,
    capacity    INTEGER,
    now_count   INTEGER,               -- 현재 주차 대수
    count_updated_at TEXT,             -- 주차장에서 집계한 시각
    realtime_ok INTEGER,               -- 20분 이내 연계 데이터가 있으면 1
    realtime_note TEXT,
    is_free     INTEGER,
    weekday_hours TEXT, weekend_hours TEXT, holiday_hours TEXT,
    basic_min   INTEGER, basic_fee INTEGER,
    add_min     INTEGER, add_fee   INTEGER,
    day_max     INTEGER,
    fetched_at  TEXT                   -- 우리가 API에서 받아온 시각
);

-- 서울 실시간 주차장 좌표 (GetParkInfo에서 같은 PKLT_CD로 가져옴)
CREATE TABLE IF NOT EXISTS seoul_coords (
    pklt_cd     TEXT PRIMARY KEY,
    lat         REAL NOT NULL,
    lng         REAL NOT NULL
);

-- 두 데이터의 짝 (서울 실시간 주차장 ↔ 표준데이터 주차장)
CREATE TABLE IF NOT EXISTS lot_links (
    pklt_cd     TEXT PRIMARY KEY REFERENCES seoul_lots(pklt_cd),
    lot_no      TEXT REFERENCES standard_lots(lot_no),
    method      TEXT,                  -- manual / address / name / nearby
    score       REAL
);

-- 사용자가 "주차 완료"를 누르면 생기는 주차 기록
-- 요금 정책은 입차 시점 값을 복사해 둔다. (나중에 요금이 바뀌어도 기록은 그대로)
CREATE TABLE IF NOT EXISTS parking_sessions (
    id          TEXT PRIMARY KEY,
    lot_id      TEXT NOT NULL,
    lot_name    TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    is_free     INTEGER NOT NULL DEFAULT 0,
    basic_min   INTEGER NOT NULL DEFAULT 0, basic_fee INTEGER NOT NULL DEFAULT 0,
    add_min     INTEGER NOT NULL DEFAULT 0, add_fee   INTEGER NOT NULL DEFAULT 0,
    day_max     INTEGER NOT NULL DEFAULT 0,
    final_fee   INTEGER
);

-- 출차 시각이 기록되는 순간 최종 요금을 확정한다.
CREATE TRIGGER IF NOT EXISTS trg_session_final_fee
AFTER UPDATE OF ended_at ON parking_sessions
WHEN NEW.ended_at IS NOT NULL AND OLD.ended_at IS NULL
BEGIN
    UPDATE parking_sessions
       SET final_fee = parking_fee(NEW.started_at, NEW.ended_at, NEW.is_free,
                                   NEW.basic_min, NEW.basic_fee,
                                   NEW.add_min, NEW.add_fee, NEW.day_max)
     WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _sql_parking_fee(started, ended, is_free, basic_min, basic_fee, add_min, add_fee, day_max):
    policy = FeePolicy(bool(is_free), basic_min, basic_fee, add_min, add_fee, day_max)
    return fee_for_minutes(policy, elapsed_minutes(parse_time(started), parse_time(ended)))


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    # 트리거가 부르는 요금 함수. 화면 미터와 같은 계산을 쓴다.
    conn.create_function("parking_fee", 8, _sql_parking_fee, deterministic=True)
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
