"""주차별 원본 스냅샷.

매주 실행하면 시계열이 추가 비용 없이 쌓인다. 분석 로직을 바꿔도 과거
스냅샷으로 재계산할 수 있어야 하므로, 여기 들어가는 것은 집계가 아니라
공고 레코드 그 자체다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

from collect.schema import Posting


def write_snapshot(postings: Iterable[Posting], path: Path) -> int:
    """공고를 JSONL 한 줄씩 쓴다. 쓴 건수를 돌려준다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for posting in postings:
            fh.write(posting.to_json())
            fh.write("\n")
            count += 1
    return count


def read_snapshot(path: Path) -> Iterator[Posting]:
    """스냅샷을 되읽는다. 재계산 경로의 입구다."""
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield Posting.from_json(json.loads(line))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path.name}:{line_no} 손상된 레코드: {exc}") from exc
