"""전국주차장정보표준데이터 (공공데이터포털 15012896).

반기마다 갱신되므로 서버가 매번 부르지 않고, 명령으로 한 번 받아서 DB에 넣는다.
- API:  python -m app.load_standard
- 파일: python -m app.load_standard --csv 내려받은파일.csv   (포털에서 CSV를 받은 경우)
"""

import csv
import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config

API_URL = "https://api.data.go.kr/openapi/tn_pubr_prkplce_info_api"
PAGE = 1000

# 같은 항목을 API(영문)와 CSV(한글) 이름으로 모두 받는다.
FIELDS = {
    "lot_no":       ("prkplceNo", "주차장관리번호"),
    "name":         ("prkplceNm", "주차장명"),
    "category":     ("prkplceSe", "주차장구분"),
    "kind":         ("prkplceType", "주차장유형"),
    "road_addr":    ("rdnmadr", "소재지도로명주소"),
    "jibun_addr":   ("lnmadr", "소재지지번주소"),
    "capacity":     ("prkcmprt", "주차구획수"),
    "oper_days":    ("operDay", "운영요일"),
    "wd_open":      ("weekdayOperOpenHhmm", "평일운영시작시각"),
    "wd_close":     ("weekdayOperColseHhmm", "평일운영종료시각"),
    "sat_open":     ("satOperOperOpenHhmm", "토요일운영시작시각"),
    "sat_close":    ("satOperCloseHhmm", "토요일운영종료시각"),
    "hol_open":     ("holidayOperOpenHhmm", "공휴일운영시작시각"),
    "hol_close":    ("holidayCloseOpenHhmm", "공휴일운영종료시각"),
    "fee_info":     ("parkingchrgeInfo", "요금정보"),
    "basic_min":    ("basicTime", "주차기본시간"),
    "basic_fee":    ("basicCharge", "주차기본요금"),
    "add_min":      ("addUnitTime", "추가단위시간"),
    "add_fee":      ("addUnitCharge", "추가단위요금"),
    "day_max":      ("dayCmmtkt", "1일주차권요금"),
    "payment":      ("metpay", "결제방법"),
    "note":         ("spcmnt", "특기사항"),
    "phone":        ("phoneNumber", "전화번호"),
    "lat":          ("latitude", "위도"),
    "lng":          ("longitude", "경도"),
    "ref_date":     ("referenceDate", "데이터기준일자"),
}


class StandardApiError(RuntimeError):
    pass


def _pick(row: dict, field: str) -> str:
    for name in FIELDS[field]:
        if name in row and row[name] is not None:
            return str(row[name]).strip()
    return ""


def _int(value: str) -> int:
    try:
        return int(float(value.replace(",", ""))) if value else 0
    except ValueError:
        return 0


def _float(value: str) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _hours(begin: str, end: str) -> str:
    return f"{begin}~{end}" if begin and end else ""


def is_seoul(row: dict) -> bool:
    addr = _pick(row, "road_addr") or _pick(row, "jibun_addr")
    return addr.startswith("서울")


def to_record(row: dict) -> dict:
    return {
        "lot_no": _pick(row, "lot_no"),
        "name": _pick(row, "name"),
        "category": _pick(row, "category"),
        "kind": _pick(row, "kind"),
        "road_addr": _pick(row, "road_addr"),
        "jibun_addr": _pick(row, "jibun_addr"),
        "capacity": _int(_pick(row, "capacity")),
        "oper_days": _pick(row, "oper_days"),
        "weekday_hours": _hours(_pick(row, "wd_open"), _pick(row, "wd_close")),
        "sat_hours": _hours(_pick(row, "sat_open"), _pick(row, "sat_close")),
        "holiday_hours": _hours(_pick(row, "hol_open"), _pick(row, "hol_close")),
        "fee_info": _pick(row, "fee_info"),
        "basic_min": _int(_pick(row, "basic_min")),
        "basic_fee": _int(_pick(row, "basic_fee")),
        "add_min": _int(_pick(row, "add_min")),
        "add_fee": _int(_pick(row, "add_fee")),
        "day_max": _int(_pick(row, "day_max")),
        "payment": _pick(row, "payment"),
        "note": _pick(row, "note"),
        "phone": _pick(row, "phone"),
        "lat": _float(_pick(row, "lat")),
        "lng": _float(_pick(row, "lng")),
        "ref_date": _pick(row, "ref_date"),
    }


def _service_key() -> str:
    key = config.DATA_GO_KR_SERVICE_KEY
    if not key:
        raise StandardApiError(".env에 DATA_GO_KR_SERVICE_KEY가 비어 있습니다.")
    # Encoding 키를 넣어도 동작하도록 한 번 풀어서 다시 인코딩한다.
    return urllib.parse.unquote(key)


def _get_page(key: str, page_no: int) -> dict:
    query = urllib.parse.urlencode(
        {"serviceKey": key, "pageNo": page_no, "numOfRows": PAGE, "type": "json"}
    )
    for attempt in range(3):  # 공공데이터포털은 가끔 응답이 끊긴다
        try:
            with urllib.request.urlopen(f"{API_URL}?{query}", timeout=60) as res:
                text = res.read().decode("utf-8")
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(3)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 인증 오류는 XML로 오는 경우가 많다.
        raise StandardApiError(f"API 응답을 읽을 수 없습니다: {text[:300]}")
    data = data.get("response", data)  # {"response": {...}}로 감싸 오기도, 바로 오기도 한다
    header = data.get("header", {})
    if header.get("resultCode") not in ("00", "0"):
        raise StandardApiError(f"{header.get('resultCode')}: {header.get('resultMsg')}")
    return data["body"]


def fetch_rows_api(progress=print):
    key = _service_key()
    page_no = 1
    while True:
        body = _get_page(key, page_no)
        items = body.get("items") or []
        if isinstance(items, dict):  # 결과가 1건이면 dict로 오는 경우가 있다
            items = items.get("item", [])
            items = [items] if isinstance(items, dict) else items
        yield from items
        total = int(body.get("totalCount", 0))
        progress(f"  {min(page_no * PAGE, total)}/{total}건")
        if page_no * PAGE >= total or not items:
            break
        page_no += 1


# 서울 주차장만 추린 결과를 레포에 넣어 둔다. GitHub Actions는 매번 전국 데이터를 받지 않고 이 파일을 쓴다.
SNAPSHOT = config.ROOT / "data" / "standard_seoul.json"


def save_snapshot(records: list[dict]) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(records, key=lambda r: r["lot_no"])
    SNAPSHOT.write_text(json.dumps(records, ensure_ascii=False, indent=0), encoding="utf-8")


def load_snapshot() -> list[dict]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def fetch_rows_csv(path: Path):
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise StandardApiError("CSV 인코딩을 알 수 없습니다. (UTF-8 또는 CP949)")
    yield from csv.DictReader(io.StringIO(text))
