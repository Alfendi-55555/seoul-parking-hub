"""DB 읽기/쓰기. 두 데이터를 합쳐 화면에서 쓰는 '주차장' 하나로 만든다."""

import uuid
from datetime import datetime

from . import config, matching
from .db import get_meta, set_meta
from .fee import FeePolicy, elapsed_minutes, fee_for_minutes, next_increase_minute, parse_time
from .sources import seoul, standard

FEE_COLS = ("basic_min", "basic_fee", "add_min", "add_fee", "day_max")


def now() -> datetime:
    return datetime.now(config.KST).replace(microsecond=0)


# ---------- 적재 ----------

def _upsert(conn, table: str, records: list[dict]) -> None:
    if not records:
        return
    cols = list(records[0])
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [tuple(r[c] for c in cols) for r in records],
    )


def save_standard(conn, rows) -> int:
    """API/CSV 원본 행을 받아 서울 주차장만 저장하고, 스냅샷 파일도 갱신한다."""
    records = [standard.to_record(r) for r in rows if standard.is_seoul(r)]
    # 같은 관리번호가 여러 번 오면 마지막 행을 쓴다
    records = list({r["lot_no"]: r for r in records if r["lot_no"]}.values())
    standard.save_snapshot(records)
    return save_standard_records(conn, records)


def save_standard_records(conn, records: list[dict]) -> int:
    conn.execute("DELETE FROM standard_lots")
    _upsert(conn, "standard_lots", records)
    set_meta(conn, "standard_loaded_at", now().isoformat())
    relink(conn)
    return len(records)


def refresh_realtime(conn) -> int:
    rows, sample = seoul.fetch_rows()
    fetched = now()
    _upsert(conn, "seoul_lots", [seoul.to_record(r, fetched) for r in rows])
    set_meta(conn, "realtime_fetched_at", fetched.isoformat())
    set_meta(conn, "realtime_sample_key", "1" if sample else "0")
    if _coords_stale(conn, fetched):
        refresh_coords(conn)
    relink(conn)
    return len(rows)


def _coords_stale(conn, at: datetime) -> bool:
    last = get_meta(conn, "coords_fetched_at")
    return last is None or (at - parse_time(last)).total_seconds() > 24 * 3600


def refresh_coords(conn) -> int:
    """좌표는 거의 바뀌지 않아서 하루에 한 번만 받는다."""
    coords = seoul.fetch_coords()
    conn.execute("DELETE FROM seoul_coords")
    conn.executemany("INSERT INTO seoul_coords (pklt_cd, lat, lng) VALUES (?, ?, ?)",
                     [(k, lat, lng) for k, (lat, lng) in coords.items()])
    set_meta(conn, "coords_fetched_at", now().isoformat())
    return len(coords)


def relink(conn) -> None:
    seoul_lots = [dict(r) for r in conn.execute("SELECT * FROM seoul_lots")]
    standard_lots = [dict(r) for r in conn.execute("SELECT * FROM standard_lots")]
    coords = {r["pklt_cd"]: (r["lat"], r["lng"]) for r in conn.execute("SELECT * FROM seoul_coords")}
    links = matching.match(seoul_lots, standard_lots, coords)
    conn.execute("DELETE FROM lot_links")
    conn.executemany(
        "INSERT INTO lot_links (pklt_cd, lot_no, method, score) VALUES (?, ?, ?, ?)",
        [(code, no, method, score) for code, (no, method, score) in links.items()],
    )


# ---------- 조회 ----------

def _policy(row) -> FeePolicy:
    return FeePolicy(bool(row["is_free"]), *(int(row[c] or 0) for c in FEE_COLS))


def _fee_text(p: FeePolicy) -> str:
    if p.is_free:
        return "무료"
    if not p.known:
        return "요금 정보 없음"
    text = f"기본 {p.basic_min}분 {p.basic_fee:,}원"
    if p.add_min:
        text += f", 추가 {p.add_min}분당 {p.add_fee:,}원"
    if p.day_max:
        text += f", 1일 최대 {p.day_max:,}원"
    return text


def _lot(row) -> dict:
    policy = _policy(row)
    available = None
    if row["realtime_ok"] and row["capacity"]:
        available = max(0, row["capacity"] - (row["now_count"] or 0))
    return {
        "id": row["id"],
        "source": row["source"],
        "name": row["name"],
        "addr": row["addr"],
        "lat": row["lat"],
        "lng": row["lng"],
        "kind": row["kind"],
        "capacity": row["capacity"],
        "available": available,
        "realtime": row["source"] == "realtime",
        "realtime_ok": bool(row["realtime_ok"]),
        "realtime_note": row["realtime_note"],
        "count_updated_at": row["count_updated_at"],
        "hours": row["hours"],
        "phone": row["phone"],
        "fee": {"is_free": policy.is_free, "known": policy.known, "text": _fee_text(policy),
                **{c: getattr(policy, c) for c in FEE_COLS}},
    }


# 서울 실시간 주차장(+짝지은 표준데이터 좌표) ∪ 짝이 없는 표준데이터 주차장
LOTS_SQL = """
SELECT 'S-' || s.pklt_cd AS id, 'realtime' AS source, s.name,
       COALESCE(NULLIF(n.road_addr, ''), '서울특별시 ' || s.addr) AS addr,
       COALESCE(c.lat, n.lat) AS lat, COALESCE(c.lng, n.lng) AS lng, s.kind, s.capacity, s.now_count, s.count_updated_at,
       s.realtime_ok, s.realtime_note,
       '평일 ' || s.weekday_hours || ' / 주말 ' || s.weekend_hours AS hours, s.phone,
       -- 서울 API에 요금이 없으면 표준데이터 요금을 쓴다
       CASE WHEN s.is_free = 1 OR s.basic_fee > 0 OR s.add_fee > 0 OR n.lot_no IS NULL
            THEN s.is_free ELSE (n.fee_info = '무료') END AS is_free,
       CASE WHEN s.basic_fee > 0 OR s.add_fee > 0 OR n.lot_no IS NULL THEN s.basic_min ELSE n.basic_min END AS basic_min,
       CASE WHEN s.basic_fee > 0 OR s.add_fee > 0 OR n.lot_no IS NULL THEN s.basic_fee ELSE n.basic_fee END AS basic_fee,
       CASE WHEN s.basic_fee > 0 OR s.add_fee > 0 OR n.lot_no IS NULL THEN s.add_min   ELSE n.add_min   END AS add_min,
       CASE WHEN s.basic_fee > 0 OR s.add_fee > 0 OR n.lot_no IS NULL THEN s.add_fee   ELSE n.add_fee   END AS add_fee,
       CASE WHEN s.basic_fee > 0 OR s.add_fee > 0 OR n.lot_no IS NULL THEN s.day_max   ELSE n.day_max   END AS day_max
  FROM seoul_lots s
  LEFT JOIN seoul_coords c ON c.pklt_cd = s.pklt_cd
  LEFT JOIN lot_links l ON l.pklt_cd = s.pklt_cd
  LEFT JOIN standard_lots n ON n.lot_no = l.lot_no
UNION ALL
SELECT 'N-' || n.lot_no, 'standard', n.name,
       COALESCE(NULLIF(n.road_addr, ''), n.jibun_addr), n.lat, n.lng, n.kind, n.capacity,
       NULL, NULL, 0, NULL,
       '평일 ' || n.weekday_hours || ' / 토 ' || n.sat_hours, n.phone,
       (n.fee_info = '무료'), n.basic_min, n.basic_fee, n.add_min, n.add_fee, n.day_max
  FROM standard_lots n
 WHERE n.lot_no NOT IN (SELECT lot_no FROM lot_links WHERE lot_no IS NOT NULL)
"""


def list_lots(conn) -> list[dict]:
    return [_lot(r) for r in conn.execute(LOTS_SQL)]


def get_lot(conn, lot_id: str) -> dict | None:
    row = conn.execute(f"SELECT * FROM ({LOTS_SQL}) WHERE id = ?", (lot_id,)).fetchone()
    return _lot(row) if row else None


def status(conn) -> dict:
    count = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return {
        "realtime_lots": count("seoul_lots"),
        "standard_lots": count("standard_lots"),
        "linked": count("lot_links"),
        "realtime_fetched_at": get_meta(conn, "realtime_fetched_at"),
        "standard_loaded_at": get_meta(conn, "standard_loaded_at"),
        "sample_key": get_meta(conn, "realtime_sample_key") == "1",
    }


# ---------- 주차 기록 ----------

def _session(row, at: datetime | None = None) -> dict:
    policy = _policy(row)
    started = parse_time(row["started_at"])
    ended = parse_time(row["ended_at"]) if row["ended_at"] else None
    until = ended or at or now()
    minutes = elapsed_minutes(started, until)
    next_up = None if ended else next_increase_minute(policy, minutes)
    return {
        "id": row["id"],
        "lot_id": row["lot_id"],
        "lot_name": row["lot_name"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "elapsed_min": minutes,
        "fee": row["final_fee"] if ended else fee_for_minutes(policy, minutes),
        "final": ended is not None,
        "next_increase_after_min": next_up,
        "next_fee": fee_for_minutes(policy, next_up + 1) if next_up is not None else None,
        "fee_text": _fee_text(policy),
        "fee_known": policy.known,
    }


def start_session(conn, lot: dict, started_at: datetime | None = None) -> dict:
    sid = uuid.uuid4().hex[:12]
    f = lot["fee"]
    conn.execute(
        """INSERT INTO parking_sessions
           (id, lot_id, lot_name, started_at, is_free, basic_min, basic_fee, add_min, add_fee, day_max)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, lot["id"], lot["name"], (started_at or now()).isoformat(),
         int(f["is_free"]), *(f[c] for c in FEE_COLS)),
    )
    return get_session(conn, sid)


def get_session(conn, sid: str) -> dict | None:
    row = conn.execute("SELECT * FROM parking_sessions WHERE id = ?", (sid,)).fetchone()
    return _session(row) if row else None


def update_start(conn, sid: str, started_at: datetime) -> dict | None:
    conn.execute(
        "UPDATE parking_sessions SET started_at = ? WHERE id = ? AND ended_at IS NULL",
        (started_at.isoformat(), sid),
    )
    return get_session(conn, sid)


def end_session(conn, sid: str, ended_at: datetime | None = None) -> dict | None:
    # final_fee는 트리거(trg_session_final_fee)가 채운다.
    conn.execute(
        "UPDATE parking_sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
        ((ended_at or now()).isoformat(), sid),
    )
    return get_session(conn, sid)
