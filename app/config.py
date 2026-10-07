import os
from pathlib import Path
from datetime import timedelta, timezone

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SEOUL_OPENAPI_KEY = os.getenv("SEOUL_OPENAPI_KEY", "").strip()
DATA_GO_KR_SERVICE_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "parking.db"))
# 실시간 여석을 다시 불러오는 간격(초). 서울 데이터 자체가 수 분 단위로 갱신된다.
REALTIME_REFRESH_SEC = int(os.getenv("REALTIME_REFRESH_SEC", "300"))

KST = timezone(timedelta(hours=9), "KST")  # 한국은 서머타임이 없어 고정값으로 충분하다
