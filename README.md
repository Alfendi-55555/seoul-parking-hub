# seoul-parking-hub

서울 주차장 통합 서비스: **주차장 찾기 · 실시간 여석 · 주차 요금 계산**.
사용자가 "여기에 주차 완료"를 누르면 그때부터 요금 미터가 돌고, "출차하기"를 누르면 요금이 확정된다.

## 데이터
| 데이터 | 출처 | 갱신 | 용도 |
|---|---|---|---|
| 서울시 시영주차장 실시간 주차대수 정보 (`GetParkingInfo`) | [서울 열린데이터광장 OA-21709](https://data.seoul.go.kr/dataList/OA-21709/S/1/datasetView.do) | 실시간(5분 이상 지연 가능) | 실시간 여석, 요금 (122곳) |
| 전국주차장정보표준데이터 | [공공데이터포털 15012896](https://www.data.go.kr/data/15012896/standard.do) | 반기 | 서울 주차장 위치·운영시간·요금 |
| 서울시 공영주차장 안내 정보 (`GetParkInfo`) | 서울 열린데이터광장 (같은 서울 키) | 하루 1번 받음 | 실시간 주차장 좌표 (실시간 API에 좌표가 없어서) |

두 데이터는 [app/matching.py](app/matching.py)에서 짝지어 하나의 주차장 목록으로 합친다.
자동으로 안 맞는 주차장은 `data/manual_links.json`에 `{"PKLT_CD": "주차장관리번호"}`로 적으면 된다.

## 요금 계산
- 계산식: [app/fee.py](app/fee.py). 기본시간까지 기본요금, 이후 추가단위시간마다 추가요금, 1일 최대요금이 있으면 24시간 단위 상한.
- 경과 시간은 분 단위로 올림. 야간·주말 요금, 할인·감면은 반영하지 않는 **예상 요금**이다.
- 입차할 때 그 주차장의 요금 정책을 주차 기록에 복사해 둔다. 나중에 요금이 바뀌어도 기록은 그대로다.
- 출차 시각이 기록되면 DB 트리거 `trg_session_final_fee`([app/db.py](app/db.py))가 최종 요금을 확정한다. 트리거는 화면 미터와 같은 파이썬 함수를 SQLite 함수(`parking_fee`)로 등록해서 부른다.

## 두 가지 실행 방식
| | 로컬 서버 (FastAPI + SQLite) | GitHub Pages |
|---|---|---|
| 실시간 여석 | 5분마다 직접 받아옴 | GitHub Actions가 10분마다 받아 `lots.json`으로 배포 |
| 주차 기록 | DB(`parking_sessions`) + 트리거 | 브라우저 localStorage |
| 요금 계산 | `app/fee.py` | `static/fee.js` (파이썬과 같은 값인지 테스트로 확인) |

## 로컬에서 실행
```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env  → 키 입력
python -m app.load_standard     # 표준데이터 받기 (처음 한 번, 이후 반기마다)
uvicorn app.main:app --reload   # http://localhost:8000
```
- 서울 키가 비어 있으면 `sample` 키(5건)로 동작한다.
- 공공데이터포털 키는 **Decoding** 키를 넣는다. (Encoding 키를 넣어도 동작은 한다)
- 포털에서 CSV를 내려받았다면 `python -m app.load_standard --csv 파일.csv`
- 테스트: `python -m pytest`

## GitHub Pages 배포
1. 레포 **Settings → Secrets and variables → Actions → New repository secret**
   - 이름 `SEOUL_OPENAPI_KEY`, 값: 서울 열린데이터광장 인증키
2. 레포 **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 바꾼다.
3. **Actions → 사이트 갱신 → Run workflow** (이후 push할 때와 10분마다 자동 실행)

표준데이터는 `data/standard_seoul.json`(서울만 추린 스냅샷)을 쓰므로 Actions에는 공공데이터포털 키가 필요 없다.
반기마다 로컬에서 `python -m app.load_standard`를 돌리고 바뀐 `data/standard_seoul.json`을 커밋한다.

## API (로컬 서버)
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/lots` | 주차장 목록 (실시간 여석 포함) |
| GET | `/api/lots/{id}/estimate?minutes=60` | 주차 전 예상 요금 |
| POST | `/api/sessions` `{"lot_id": "S-171721"}` | 주차 완료 → 요금 계산 시작 |
| GET | `/api/sessions/{id}` | 현재 요금, 다음 요금 인상 시점 |
| POST | `/api/sessions/{id}/start-time` `{"at": "2026-10-07T15:30"}` | 입차 시각 고치기 |
| POST | `/api/sessions/{id}/end` | 출차 → 최종 요금 확정(트리거) |
