"""서울 실시간 주차장(GetParkingInfo)과 표준데이터 주차장 짝짓기.

짝을 지으면 같은 주차장이 지도에 두 번 나오지 않고, 서울 API에 요금이 없을 때 표준데이터 요금을 쓴다.
좌표는 서울 API(GetParkInfo)를 먼저 쓰고, 없으면 짝지은 표준데이터 좌표를 쓴다.
1) data/manual_links.json 에 직접 적은 짝 {"PKLT_CD": "주차장관리번호"}
2) 지번 주소(구 + 동 + 번지)가 같으면 짝
3) 같은 구 안에서 이름이 거의 같으면 짝
4) 좌표가 80m 안이고 이름이 어느 정도 비슷하면 짝
"""

import json
import math
import re
from difflib import SequenceMatcher

from . import config

MANUAL_LINKS = config.ROOT / "data" / "manual_links.json"
NAME_THRESHOLD = 0.9          # 0.75로 하면 "청계6(북)"과 "청계1(북)"이 짝지어진다
NEARBY_M = 80
NEARBY_NAME_THRESHOLD = 0.5

_NAME_NOISE = re.compile(r"\((시|구)\)|관광버스\s*전용|공영주차장|주차장|공영|시영|노상|노외|[\s()\[\]·.,\-_]")
_SEOUL = re.compile(r"^서울(특별시|시)?\s*")
_JIBUN = re.compile(r"(\S+?[동가로리])\s*(산?\d+(?:-\d+)?)")


def gu_of(addr: str) -> str:
    m = re.search(r"(\S+구)(\s|$)", _SEOUL.sub("", addr or ""))
    return m.group(1) if m else ""


def norm_addr(addr: str) -> str:
    """'서울특별시 강동구 암사1동 473-3(지하)' → '강동구 암사동 473-3'"""
    addr = _SEOUL.sub("", addr or "")
    addr = re.sub(r"\(.*?\)", " ", addr)
    m = _JIBUN.search(addr)
    if not m:
        return ""
    dong = re.sub(r"\d+동$", "동", m.group(1))
    bunji = re.sub(r"-0$", "", m.group(2))
    return f"{gu_of(addr)} {dong} {bunji}"


def norm_name(name: str) -> str:
    return _NAME_NOISE.sub("", name or "")


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_name(a), norm_name(b)).ratio()


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(lat)
    return math.hypot(dx, dy)


def load_manual() -> dict[str, str]:
    if MANUAL_LINKS.exists():
        return json.loads(MANUAL_LINKS.read_text(encoding="utf-8"))
    return {}


def match(seoul_lots: list[dict], standard_lots: list[dict],
          coords: dict[str, tuple[float, float]] | None = None) -> dict[str, tuple[str, str, float]]:
    """{pklt_cd: (lot_no, method, score)}"""
    coords = coords or {}
    by_addr: dict[str, dict] = {}
    by_gu: dict[str, list[dict]] = {}
    for s in standard_lots:
        if s["lat"] is None or s["lng"] is None:
            continue
        key = norm_addr(s["jibun_addr"])
        if key:
            by_addr.setdefault(key, s)
        by_gu.setdefault(gu_of(s["jibun_addr"] or s["road_addr"]), []).append(s)

    manual = load_manual()
    known = {s["lot_no"] for s in standard_lots}
    links: dict[str, tuple[str, str, float]] = {}
    for lot in seoul_lots:
        code = lot["pklt_cd"]
        if manual.get(code) in known:
            links[code] = (manual[code], "manual", 1.0)
            continue
        hit = by_addr.get(norm_addr(lot["addr"]))
        if hit:
            links[code] = (hit["lot_no"], "address", 1.0)
            continue
        candidates = by_gu.get(gu_of(lot["addr"]), [])
        best = max(candidates, key=lambda s: similar(lot["name"], s["name"]), default=None)
        if best and (score := similar(lot["name"], best["name"])) >= NAME_THRESHOLD:
            links[code] = (best["lot_no"], "name", round(score, 3))
            continue
        if code in coords:
            near = [s for s in candidates if distance_m(coords[code], (s["lat"], s["lng"])) <= NEARBY_M]
            best = max(near, key=lambda s: similar(lot["name"], s["name"]), default=None)
            if best and (score := similar(lot["name"], best["name"])) >= NEARBY_NAME_THRESHOLD:
                links[code] = (best["lot_no"], "nearby", round(score, 3))
    return links
