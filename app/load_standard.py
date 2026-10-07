"""전국주차장정보표준데이터를 받아 서울 주차장만 DB에 넣는다.

    python -m app.load_standard                 # 공공데이터포털 API로 받기
    python -m app.load_standard --csv 파일.csv   # 포털에서 내려받은 CSV로 넣기
"""

import argparse
from pathlib import Path

from . import db, repo
from .sources import standard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--csv", type=Path, help="내려받은 CSV 파일 경로")
    args = parser.parse_args()

    print("전국주차장정보표준데이터 받는 중...")
    rows = standard.fetch_rows_csv(args.csv) if args.csv else standard.fetch_rows_api()
    with db.session() as conn:
        n = repo.save_standard(conn, list(rows))
        st = repo.status(conn)
    print(f"서울 주차장 {n}건 저장 (data/standard_seoul.json 갱신), "
          f"실시간 주차장 {st['realtime_lots']}곳 중 {st['linked']}곳과 짝지음")


if __name__ == "__main__":
    main()
