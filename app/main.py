import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, repo
from .fee import FeePolicy, fee_for_minutes

log = logging.getLogger("parking")
STATIC = config.ROOT / "static"


def _refresh_once() -> None:
    try:
        with db.session() as conn:
            n = repo.refresh_realtime(conn)
        log.info("실시간 주차 정보 %d건 갱신", n)
    except Exception as e:  # 네트워크 오류가 나도 서버는 계속 돈다
        log.warning("실시간 주차 정보 갱신 실패: %s", e)


async def _refresh_loop() -> None:
    while True:
        await asyncio.to_thread(_refresh_once)
        await asyncio.sleep(config.REALTIME_REFRESH_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    if not config.SEOUL_OPENAPI_KEY:
        log.warning("SEOUL_OPENAPI_KEY가 비어 있어 sample 키(5건)로 동작합니다.")
    task = asyncio.create_task(_refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="서울 주차 허브", lifespan=lifespan)


def _time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.KST)
    value = value.astimezone(config.KST).replace(microsecond=0)
    if value > repo.now():
        raise HTTPException(400, "미래 시각은 넣을 수 없습니다.")
    return value


@app.get("/api/status")
def get_status():
    with db.session() as conn:
        return repo.status(conn)


@app.get("/api/lots")
def get_lots():
    with db.session() as conn:
        return repo.list_lots(conn)


@app.get("/api/lots/{lot_id}")
def get_lot(lot_id: str):
    with db.session() as conn:
        lot = repo.get_lot(conn, lot_id)
    if not lot:
        raise HTTPException(404, "주차장을 찾을 수 없습니다.")
    return lot


@app.get("/api/lots/{lot_id}/estimate")
def estimate(lot_id: str, minutes: int):
    """주차 전 예상 요금."""
    lot = get_lot(lot_id)
    f = lot["fee"]
    policy = FeePolicy(f["is_free"], f["basic_min"], f["basic_fee"], f["add_min"], f["add_fee"], f["day_max"])
    return {"minutes": minutes, "fee": fee_for_minutes(policy, max(0, minutes)), "known": policy.known}


class StartBody(BaseModel):
    lot_id: str
    started_at: datetime | None = None


class TimeBody(BaseModel):
    at: datetime | None = None


@app.post("/api/sessions")
def start_session(body: StartBody):
    """사용자가 '주차 완료'를 누르면 요금 계산을 시작한다."""
    with db.session() as conn:
        lot = repo.get_lot(conn, body.lot_id)
        if not lot:
            raise HTTPException(404, "주차장을 찾을 수 없습니다.")
        return repo.start_session(conn, lot, _time(body.started_at))


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    with db.session() as conn:
        s = repo.get_session(conn, sid)
    if not s:
        raise HTTPException(404, "주차 기록이 없습니다.")
    return s


@app.post("/api/sessions/{sid}/start-time")
def change_start(sid: str, body: TimeBody):
    """'주차 완료'를 늦게 누른 경우 입차 시각을 고친다."""
    if body.at is None:
        raise HTTPException(400, "시각이 필요합니다.")
    with db.session() as conn:
        s = repo.update_start(conn, sid, _time(body.at))
    if not s:
        raise HTTPException(404, "주차 기록이 없습니다.")
    return s


@app.post("/api/sessions/{sid}/end")
def end_session(sid: str, body: TimeBody | None = None):
    with db.session() as conn:
        s = repo.end_session(conn, sid, _time(body.at if body else None))
    if not s:
        raise HTTPException(404, "주차 기록이 없습니다.")
    return s


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
