"""Notion 지원 보드 DB 만들기.

파이프라인이 쓰는 열 14개를 이름·종류·선택지 순서까지 맞춰 한 번에 만든다.
손으로 만들면 열 이름 한 글자만 틀려도 적재가 전부 400으로 실패하는데,
그 실패는 `push()` 가 건수만 세고 넘어가서 원인이 보이지 않는다.

열 목록은 `check_notion.EXPECTED` 에서 가져온다. EXPECTED 는 적재 코드
(`report/notion.py` 의 build_properties)와 테스트로 묶여 있으므로, 여기서
만드는 DB 와 실제로 쓰는 속성이 갈라질 수 없다.

사용:
    1. Notion 에서 인테그레이션을 만들고 토큰을 NOTION_TOKEN 에 넣는다.
    2. DB 를 둘 페이지를 만들고, 페이지 메뉴(⋯)의 연결에서 인테그레이션을 추가한다.
    3. python setup_notion.py <페이지 URL 또는 ID> [--title 이름]
    4. (선택) 대시보드: 빈 페이지를 만들어 연결한 뒤
       python setup_notion.py <대시보드 페이지 URL> --dashboard
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from analyze.priority import PRIORITIES
from check_notion import EXPECTED
from extract.company_size import SIZES, UNKNOWN
from report.dashboard_layout import VIEWS_API_VERSION, build_dashboard
from report.notion import API_ROOT, INITIAL_STATUS, LISTING_STATUSES, NOTION_VERSION, TIMEOUT_SEC, notion_request

DEFAULT_TITLE = "지원 보드"

#: 대시보드 설정 도중 실패했을 때 공통으로 보여줄 안내. build_dashboard 는 여러
#: 호출을 순서대로 하므로, 어떤 예외로 멈추든 페이지에는 이미 일부가 생겨 있을 수 있다.
_PARTIAL_BUILD_HINT = "페이지에 일부만 만들어졌을 수 있습니다. 새로 생긴 블록을 지우고 다시 실행하세요."

#: 순서가 의미 있는 선택지. Notion 은 선택지 순서대로 정렬하므로 만들 때 박아 둔다.
#: 여기 없는 select(회사)는 빈 채로 만들고, 값이 들어올 때 Notion 이 선택지를 만든다.
#: '상태'는 사용자가 쓰는 열이다. 파이프라인은 새 행에 INITIAL_STATUS 만 넣는다.
SELECT_OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "우선순위": tuple(zip(PRIORITIES, ("red", "yellow", "gray"))),
    "규모": tuple(zip((*SIZES, UNKNOWN), ("red", "orange", "green", "blue", "gray"))),
    "공고 현황": tuple(zip(LISTING_STATUSES, ("green", "orange", "gray"))),
    "상태": ((INITIAL_STATUS, "green"), ("검토", "yellow"), ("지원", "blue")),
}

_HEX_ID = re.compile(r"([0-9a-f]{8})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{12})", re.I)


def page_id_from(value: str) -> str:
    """페이지 URL 이나 ID 에서 32자리 ID 를 꺼낸다. 쿼리 문자열은 보지 않는다."""
    path = (value or "").split("?", 1)[0]
    matches = _HEX_ID.findall(path)
    if not matches:
        raise ValueError(f"페이지 ID 를 찾을 수 없습니다: {value!r} (페이지 URL 을 그대로 붙여 넣으세요)")
    return "".join(matches[-1]).lower()


def _property(name: str, kind: str) -> dict[str, Any]:
    if kind == "select":
        options = [{"name": n, "color": c} for n, c in SELECT_OPTIONS.get(name, ())]
        return {"select": {"options": options}}
    if kind == "multi_select":
        return {"multi_select": {"options": []}}
    return {kind: {}}


def database_payload(parent_page_id: str, *, title: str = DEFAULT_TITLE) -> dict[str, Any]:
    """DB 생성 요청 본문. 순수 함수다."""
    return {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": {name: _property(name, kind) for name, kind in EXPECTED.items()},
    }


def create_database(
    token: str,
    parent_page_id: str,
    *,
    title: str = DEFAULT_TITLE,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, str]:
    request = urllib.request.Request(
        f"{API_ROOT}/databases",
        method="POST",
        data=json.dumps(database_payload(parent_page_id, title=title), ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    raw = opener(request, timeout=TIMEOUT_SEC).read()
    created = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    return {"id": created["id"], "url": created.get("url", "")}


def _diagnose(error: urllib.error.HTTPError) -> str:
    if error.code == 401:
        return "토큰이 잘못됐습니다. NOTION_TOKEN 을 다시 복사해 넣으세요."
    if error.code == 404:
        return (
            "페이지를 찾을 수 없습니다. 페이지를 인테그레이션에 연결했는지 확인하세요.\n"
            "  페이지 오른쪽 위 ⋯ → 연결 → 만든 인테그레이션 선택"
        )
    return f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:300]}"


def _setup_dashboard(token: str, page_id: str, *, opener: Callable[..., Any]) -> int:
    jobs_database_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if not jobs_database_id:
        print("NOTION_DATABASE_ID 가 없습니다. 공고 DB 를 먼저 만들고 환경변수에 넣으세요.", file=sys.stderr)
        return 1

    def request(method: str, path: str, body):
        return notion_request(token, method, path, body, version=VIEWS_API_VERSION, opener=opener)

    try:
        ids = build_dashboard(request, page_id=page_id, jobs_database_id=jobs_database_id)
    except urllib.error.HTTPError as exc:
        print(_diagnose(exc), file=sys.stderr)
        print(_PARTIAL_BUILD_HINT, file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Notion 에 연결하지 못했습니다: {exc}", file=sys.stderr)
        print(_PARTIAL_BUILD_HINT, file=sys.stderr)
        return 1
    except (KeyError, IndexError, ValueError) as exc:
        print(f"대시보드를 만들다 멈췄습니다: {exc}", file=sys.stderr)
        print(_PARTIAL_BUILD_HINT, file=sys.stderr)
        return 1

    print("대시보드를 만들었습니다.")
    print("\n환경변수 두 개를 넣으세요 (PowerShell):")
    print(f'  setx NOTION_DASHBOARD_PAGE_ID "{ids.page_id}"')
    print(f'  setx NOTION_SKILL_DATABASE_ID "{ids.skill_database_id}"')
    print("\n다음 자동 실행부터 요약 숫자와 스킬 DB 가 채워집니다.")
    return 0


def main(argv: Sequence[str] | None = None, *, opener: Callable[..., Any] = urllib.request.urlopen) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Notion 지원 보드 DB 를 만든다.")
    parser.add_argument("page", help="DB 를 만들 Notion 페이지의 URL 또는 ID")
    parser.add_argument("--title", default=DEFAULT_TITLE, help=f"DB 이름 (기본: {DEFAULT_TITLE})")
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="이 페이지에 대시보드를 만든다 (NOTION_DATABASE_ID 의 공고 DB 기준)",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("NOTION_TOKEN", "").strip()
    if not token:
        print("NOTION_TOKEN 이 없습니다. 인테그레이션 토큰을 환경변수로 넣은 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    try:
        page_id = page_id_from(args.page)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.dashboard:
        return _setup_dashboard(token, page_id, opener=opener)

    try:
        created = create_database(token, page_id, title=args.title, opener=opener)
    except urllib.error.HTTPError as exc:
        print(_diagnose(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Notion 에 연결하지 못했습니다: {exc}", file=sys.stderr)
        return 1

    print(f"DB 를 만들었습니다: {args.title}")
    if created["url"]:
        print(f"  {created['url']}")
    print("\n다음 순서:")
    print(f"  1. 환경변수 NOTION_DATABASE_ID 에 이 값을 넣으세요: {created['id']}")
    print("  2. python check_notion.py 로 연결과 열 구성을 확인하세요.")
    print("  3. (선택) Notion 보기 설정에서 key 열을 숨기세요. 지우면 안 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
