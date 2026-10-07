"""GitHub Pages용 정적 사이트 만들기.

    python -m app.export_static        # site/ 폴더에 index.html, static/, lots.json 생성

서버 없이 돌아가도록 실시간 데이터를 받아 lots.json 한 파일로 내보낸다.
요금 계산과 주차 기록은 브라우저(static/fee.js, localStorage)가 맡는다.
GitHub Actions(.github/workflows/pages.yml)가 10분마다 이 명령을 돌려 사이트를 갱신한다.
"""

import json
import shutil

from . import config, db, repo
from .sources import standard

SITE = config.ROOT / "site"


def main() -> None:
    with db.session() as conn:
        if repo.status(conn)["standard_lots"] == 0:
            n = repo.save_standard_records(conn, standard.load_snapshot())
            print(f"표준데이터 스냅샷 {n}건 적재")
        n = repo.refresh_realtime(conn)
        print(f"실시간 주차장 {n}곳 갱신")
        payload = {"status": repo.status(conn), "lots": repo.list_lots(conn)}

    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(config.ROOT / "static", SITE / "static")
    html = (config.ROOT / "static" / "index.html").read_text(encoding="utf-8")
    html = html.replace("<!-- static-config -->", "<script>window.PARKING_STATIC = true;</script>")
    (SITE / "index.html").write_text(html, encoding="utf-8")
    (SITE / "static" / "index.html").unlink()
    (SITE / "lots.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"site/ 생성 완료 (주차장 {len(payload['lots'])}곳)")


if __name__ == "__main__":
    main()
