"""공고 원본 캐시(`store/raw/`)의 뒷정리.

`{소스}-{날짜}.json` 은 같은 날 다시 돌릴 때만 읽힌다. 수집기의 fetch_payload 가
오늘 날짜 파일만 보기 때문이다. 그러니 지난 날 파일은 그대로 두면 쌓이기만 한다.

그렇다고 지울 수는 없다. 이 원본에는 **수집 필터가 걸러낸 공고까지** 들어 있다.
스냅샷(`store/snapshots/`)에는 걸러진 뒤의 것만 남는다. 나중에 직무 범위를 넓히면
과거를 다시 뽑을 수 있는 자료는 이쪽뿐이다. 그래서 지우지 않고 압축한다.
JSON 이라 잘 눌린다(2026-09-17 실측: 44.4MB -> 6.6MB, 15%).

압축본은 그냥 gzip 이다. 나중에 읽을 때는 `gzip.open(path, "rt", encoding="utf-8")`.
"""
from __future__ import annotations

import gzip
import shutil
from datetime import date
from pathlib import Path

SUFFIX = ".gz"


def _cache_date(path: Path) -> date | None:
    """파일 이름 끝에 붙은 날짜. 캐시 이름이 아니면 None 이다.

    소스 이름 자체에 하이픈이 들어간다(greenhouse-coupang). 하이픈으로 나누면
    개수가 소스마다 달라지므로, 날짜 길이만큼 뒤에서 잘라 본다.
    """
    try:
        return date.fromisoformat(path.stem[-10:])
    except ValueError:
        return None


def compress_old(raw_dir: Path, today: str) -> int:
    """지난 날 원본을 gzip 으로 바꾸고, 바꾼 수를 준다.

    오늘 것은 아직 재실행이 읽으므로 남긴다. 앞날 날짜도 남긴다. 시계가 어긋난
    날 쓴 파일을 건드리면 그날 받은 것을 또 받는다.

    하위 폴더는 보지 않는다. `work24/` 가 이 밑에 있고 그쪽은 성격이 다르다
    (collect.training.prune_cache 가 창을 넘긴 것을 지운다).

    압축은 임시 파일에 쓰고 마지막에 바꿔 놓는다. 중간에 죽어도 원본은 남는다.
    """
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        return 0
    limit = date.fromisoformat(today)
    done = 0
    for path in raw_dir.glob("*.json"):
        when = _cache_date(path)
        if when is None or when >= limit:
            continue
        target = path.with_name(path.name + SUFFIX)
        staging = target.with_name(target.name + ".tmp")
        try:
            with path.open("rb") as source, gzip.open(staging, "wb") as sink:
                shutil.copyfileobj(source, sink)
            staging.replace(target)
        except OSError:
            staging.unlink(missing_ok=True)
            raise
        path.unlink()
        done += 1
    return done
