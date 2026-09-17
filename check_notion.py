"""Notion 연결·스키마 점검.

실제 적재 전에 세 가지를 확인한다.

  1. 토큰과 DB ID가 환경변수에 있는가
  2. 인테그레이션이 그 DB에 실제로 연결됐는가 (연결을 빼먹으면 404가 난다)
  3. DB 속성의 **이름과 타입**이 `report/notion.py`가 보내는 것과 맞는가

3번이 이 스크립트의 존재 이유다. 속성 이름이 하나라도 다르면 Notion은
그 요청 전체를 400으로 떨구고, `push()`는 실패 건수만 세고 넘어간다.
첫 실행에서 전부 실패하는 것보다 여기서 미리 걸리는 편이 낫다.

사용:  python check_notion.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from report.notion import API_ROOT, NOTION_VERSION

#: report/notion.py 의 build_properties 가 보내는 속성과 그 타입.
#: 여기를 고치면 build_properties 도 함께 고쳐야 한다.
EXPECTED: dict[str, str] = {
    "공고명": "title",
    "회사": "select",
    "규모": "select",
    "우선순위": "select",
    "지역": "rich_text",
    "마감": "rich_text",
    "보유 스킬": "multi_select",
    "부족 스킬": "multi_select",
    "핵심 부족 스킬": "multi_select",
    "어필 경험": "multi_select",
    "어필 포인트": "rich_text",
    "URL": "url",
    "상태": "select",
    "key": "rich_text",
    "공고 현황": "select",
    "새 공고": "select",
}

OK, FAIL, WARN = "  OK  ", "  실패  ", "  경고  "


def _fetch(token: str, database_id: str) -> dict:
    request = urllib.request.Request(
        f"{API_ROOT}/databases/{database_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        },
    )
    return json.loads(urllib.request.urlopen(request, timeout=30).read().decode("utf-8"))


def _diagnose(error: urllib.error.HTTPError) -> str:
    """Notion 오류 코드를 사람이 조치할 수 있는 말로 바꾼다."""
    if error.code == 401:
        return "토큰이 잘못됐습니다. NOTION_TOKEN 을 다시 복사해 주세요"
    if error.code == 404:
        return (
            "DB를 찾을 수 없습니다. 둘 중 하나입니다 —\n"
            "      (a) DB를 인테그레이션에 연결하지 않았다\n"
            "          → DB 페이지 우측 상단 ⋯ → Connections → 인테그레이션 선택\n"
            "      (b) NOTION_DATABASE_ID 가 틀렸다\n"
            "          → URL 의 '?v=' 앞 32자리여야 합니다 (뷰 ID 아님)"
        )
    return f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:200]}"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    token = os.environ.get("NOTION_TOKEN", "").strip()
    database_id = os.environ.get("NOTION_DATABASE_ID", "").strip()

    print("[1] 환경변수")
    if not token:
        print(f"{FAIL}NOTION_TOKEN 이 없습니다")
        return 1
    if not database_id:
        print(f"{FAIL}NOTION_DATABASE_ID 가 없습니다")
        return 1
    print(f"{OK}NOTION_TOKEN {token[:7]}… / DATABASE_ID {database_id[:8]}…")
    if not token.startswith(("ntn_", "secret_")):
        print(f"{WARN}토큰이 'ntn_' 로 시작하지 않습니다. 시크릿이 맞는지 확인하세요")

    print("\n[2] DB 접근")
    try:
        database = _fetch(token, database_id)
    except urllib.error.HTTPError as exc:
        print(f"{FAIL}{_diagnose(exc)}")
        return 1
    except OSError as exc:
        print(f"{FAIL}네트워크 오류 — {exc}")
        return 1

    title = "".join(t.get("plain_text", "") for t in database.get("title") or ())
    print(f"{OK}연결됨 — '{title or '(제목 없음)'}'")

    print("\n[3] 속성 이름·타입")
    actual = {name: prop.get("type") for name, prop in (database.get("properties") or {}).items()}
    problems = 0

    for name, expected_type in EXPECTED.items():
        if name not in actual:
            near = [a for a in actual if a.replace(" ", "") == name.replace(" ", "")]
            hint = f" (비슷한 이름: {near[0]!r} — 공백을 확인하세요)" if near else ""
            print(f"{FAIL}'{name}' 속성이 없습니다{hint}")
            problems += 1
        elif actual[name] != expected_type:
            note = ""
            if name == "상태" and actual[name] == "status":
                note = " — Notion 의 'Status' 타입이 아니라 'Select' 여야 합니다"
            print(f"{FAIL}'{name}' 타입이 {actual[name]} 입니다. {expected_type} 여야 합니다{note}")
            problems += 1

    extra = sorted(set(actual) - set(EXPECTED))
    if extra:
        print(f"{WARN}쓰지 않는 속성이 있습니다(무해): {', '.join(extra)}")

    if problems:
        print(f"\n{problems}건을 고쳐야 적재가 됩니다.")
        return 1

    print(f"{OK}{len(EXPECTED)}개 속성 모두 일치")
    print("\n준비됐습니다. python run.py 를 실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
