"""서울시 시영주차장 실시간 주차대수 정보 (서울 열린데이터광장 OA-21709, 서비스명 GetParkingInfo).

실시간 API에는 좌표가 없어서, 같은 서울 키로 쓰는 '서울시 공영주차장 안내 정보'(GetParkInfo)에서
주차장 코드(PKLT_CD)가 같은 행의 좌표를 가져온다.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime

from .. import config

SERVICE = "GetParkingInfo"
COORD_SERVICE = "GetParkInfo"
PAGE = 1000           # 한 번에 받을 수 있는 최대 건수
SAMPLE_PAGE = 5       # sample 키는 5건까지만 준다


class SeoulApiError(RuntimeError):
    pass


def _hours(begin: str, end: str) -> str:
    if not begin or not end:
        return ""
    return f"{begin[:2]}:{begin[2:]}~{end[:2]}:{end[2:]}"


def _int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _get(key: str, start: int, end: int, service: str = SERVICE) -> dict:
    url = f"http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key)}/json/{service}/{start}/{end}/"
    with urllib.request.urlopen(url, timeout=30) as res:
        data = json.load(res)
    body = data.get(service)
    if body is None:
        result = data.get("RESULT", {})
        raise SeoulApiError(f"{result.get('CODE')}: {result.get('MESSAGE')}")
    return body


def fetch_rows(service: str = SERVICE) -> tuple[list[dict], bool]:
    """(행 목록, sample 키 사용 여부)"""
    key = config.SEOUL_OPENAPI_KEY or "sample"
    sample = key == "sample"
    page = SAMPLE_PAGE if sample else PAGE
    rows: list[dict] = []
    start = 1
    while True:
        body = _get(key, start, start + page - 1, service)
        rows.extend(body.get("row", []))
        total = int(body.get("list_total_count", 0))
        start += page
        if sample or start > total:
            break
    return rows, sample


def to_record(row: dict, fetched_at: datetime) -> dict:
    return {
        "pklt_cd": row["PKLT_CD"],
        "name": row.get("PKLT_NM", "").strip(),
        "addr": row.get("ADDR", "").strip(),
        "kind": row.get("PRK_TYPE_NM", ""),
        "oper": row.get("OPER_SE_NM", ""),
        "phone": row.get("TELNO", ""),
        "capacity": _int(row.get("TPKCT")),
        "now_count": _int(row.get("NOW_PRK_VHCL_CNT")),
        "count_updated_at": row.get("NOW_PRK_VHCL_UPDT_TM") or None,
        # PRK_STTS_YN 1 = 현재~20분 이내 연계 데이터 있음
        "realtime_ok": 1 if str(row.get("PRK_STTS_YN")) == "1" else 0,
        "realtime_note": row.get("PRK_STTS_NM", ""),
        "is_free": 1 if row.get("PAY_YN") == "N" else 0,
        "weekday_hours": _hours(row.get("WD_OPER_BGNG_TM"), row.get("WD_OPER_END_TM")),
        "weekend_hours": _hours(row.get("WE_OPER_BGNG_TM"), row.get("WE_OPER_END_TM")),
        "holiday_hours": _hours(row.get("LHLDY_OPER_BGNG_TM"), row.get("LHLDY_OPER_END_TM")),
        "basic_min": _int(row.get("BSC_PRK_HR")),
        "basic_fee": _int(row.get("BSC_PRK_CRG")),
        "add_min": _int(row.get("ADD_PRK_HR")),
        "add_fee": _int(row.get("ADD_PRK_CRG")),
        "day_max": _int(row.get("DAY_MAX_CRG")),
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
    }


def fetch_coords() -> dict[str, tuple[float, float]]:
    """{PKLT_CD: (위도, 경도)}. 좌표가 0인 행은 뺀다."""
    rows, _ = fetch_rows(COORD_SERVICE)
    coords: dict[str, tuple[float, float]] = {}
    for r in rows:
        try:
            lat, lng = float(r.get("LAT") or 0), float(r.get("LOT") or 0)
        except ValueError:
            continue
        if lat and lng:
            coords.setdefault(r["PKLT_CD"], (lat, lng))
    return coords
