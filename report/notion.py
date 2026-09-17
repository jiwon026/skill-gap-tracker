"""분석 결과를 Notion 데이터베이스에 적재한다.

리포트를 파일이 아니라 Notion에 쌓는 이유는 지원 현황을 사용자가 직접
갱신하기 때문이다. 그래서 이 모듈의 제일 중요한 규칙은 **덮어쓰지 않는
것**이다.

  - `key`(=`source:source_id`)로 upsert한다. 매일 돌려도 중복이 안 쌓인다.
  - 기존 행의 '상태'는 건드리지 않는다. 사용자가 '지원함'으로 바꿔 둔 것을
    다음 실행이 '신규'로 되돌리면 이 도구는 쓸 수 없게 된다.
  - 자격증명이 없으면 `from_env()`가 None을 준다. 호출부는 그냥 건너뛴다.
  - 결과에서 빠진 행은 지우지 않고 '공고 현황'만 바꾼다(`retire`). 행을
    지우면 사용자가 적어 둔 상태와 메모가 함께 사라진다.

행 하나가 실패해도 나머지는 계속 올라간다. 스물몇 건 중 한 건 때문에
전체가 날아가는 것이 더 나쁘다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Collection, Iterable, Iterator, Mapping, Protocol

from analyze.gap import AnalyzedPosting
from analyze.priority import assign_priority

API_ROOT = "https://api.notion.com/v1"
#: Notion은 버전을 헤더로 고정한다. 올리지 않으면 스키마가 갑자기 바뀌지 않는다.
NOTION_VERSION = "2022-06-28"
TIMEOUT_SEC = 30

#: 신규 행의 초깃값. 이후로는 사용자의 것이다.
INITIAL_STATUS = "신규"

#: '공고 현황'. 사용자의 지원 '상태'와 달리 시스템이 매 실행 다시 쓴다.
#: Notion select 는 옵션 순서대로 정렬되므로 이 순서가 곧 정렬 순서다.
LISTING_STATUSES = ("모집중", "제외됨", "마감")
OPEN, EXCLUDED, CLOSED = LISTING_STATUSES

#: Notion multi_select 옵션 하나의 상한. 넘으면 요청 전체가 400이 된다.
_MAX_OPTIONS = 100


class NotionClient(Protocol):
    def find_page(self, key: str) -> "ExistingPage | None": ...
    def create_page(self, properties: Mapping[str, Any]) -> str: ...
    def update_page(self, page_id: str, properties: Mapping[str, Any]) -> None: ...
    def iter_rows(self) -> Iterator[tuple[str, str, str | None]]: ...


@dataclass(frozen=True, slots=True)
class SyncResult:
    created: int = 0
    updated: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class RetireResult:
    excluded: int = 0
    closed: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0


def listing_status(
    key: str,
    *,
    active: Iterable[str],
    collected: Iterable[str],
    complete_sources: Iterable[str],
) -> str | None:
    """행 하나의 공고 현황. 판단할 근거가 없으면 None 이다.

    '없음'을 전부 제외로 찍으면, 어느 날 수집이 네트워크 오류로 실패했을 때
    그 소스의 멀쩡한 공고가 전부 사라진 것처럼 보인다. 그래서 확실한 경우만
    표시한다.

      오늘 결과에 있다                          → 모집중
      오늘 수집은 됐는데 필터에 걸렸다           → 제외됨
      목록이 완전한 소스에서 공고가 사라졌다      → 마감
      그 밖(수집 실패, 검색형 소스)             → None, 건드리지 않는다
    """
    if key in set(active):
        return OPEN
    if key in set(collected):
        return EXCLUDED
    source = key.split(":", 1)[0]
    if source in set(complete_sources):
        return CLOSED
    return None


#: '새 공고' 칩. Notion 에는 조건부 서식이 없어서(행 색을 조건으로 못 바꾼다)
#: 색이 나오는 자리는 select 옵션 색뿐이다. 새로 들어온 행에만 값을 넣는다.
NEW = "NEW"

#: 칩을 며칠 달아 둘지. 매일 들여다보지 않아도 주말치가 보이게 사흘이다.
NEW_DAYS = 3

_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class ExistingPage:
    """이미 있는 행. 언제 생겼는지까지 알아야 '새 공고' 를 가릴 수 있다."""

    page_id: str
    created_at: str


def new_window_start(run_date: str, *, days: int = NEW_DAYS) -> str:
    """오늘 포함 며칠치를 '새 공고' 로 볼지, 그 시작 날짜를 준다."""
    return (datetime.fromisoformat(run_date).date() - timedelta(days=days - 1)).isoformat()


def is_new(created_at: str, since: str | None) -> bool:
    """Notion 이 준 생성 시각(UTC)을 한국 날짜로 보고 창 안인지 본다.

    시각을 못 읽으면 새 공고가 아니라고 본다. 칩을 잘못 다는 것보다 안 다는
    쪽이 낫다. 어차피 다음 실행에서 다시 판단한다.
    """
    if not since or not created_at:
        return False
    try:
        moment = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return moment.astimezone(_KST).date().isoformat() >= since


def _text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": value[:2000]}}]}


def _choice(label: str) -> dict[str, Any]:
    """select 값. multi_select와 똑같이 쉼표를 못 쓰고 길이 상한이 있다.

    사람인 회사명은 정제되지 않은 법인명이라 쉼표가 들어온다. 그대로
    보내면 그 행만 400으로 실패해 영구히 적재되지 않는다.
    """
    cleaned = (label or "").replace(",", " ").strip()[:_MAX_OPTIONS]
    return {"select": {"name": cleaned or "미상"}}


def _options(labels: Iterable[str]) -> dict[str, Any]:
    """multi_select 값. 쉼표는 Notion이 옵션 구분자로 먹으므로 미리 없앤다."""
    seen: list[str] = []
    for label in labels:
        cleaned = label.replace(",", " ").strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return {"multi_select": [{"name": name} for name in seen[:_MAX_OPTIONS]]}


def build_properties(
    analyzed: AnalyzedPosting,
    skill_names: Mapping[str, str],
    *,
    for_update: bool = False,
    featured_skills: Collection[str] = (),
    is_new: bool = False,
) -> dict[str, Any]:
    """분석 결과 한 건을 Notion 속성으로 옮긴다.

    `for_update=True`면 사용자가 소유한 속성('상태')을 뺀다.

    `featured_skills`는 첫 화면 차트에 세울 스킬 이름이다. 부족 스킬 중
    거기 드는 것만 '핵심 부족 스킬'에 넣는다. Notion 차트는 막대를 상위
    몇 개로 못 자르므로, 자를 것을 미리 잘라서 보낸다.
    """
    posting, gap = analyzed.posting, analyzed.gap
    closing = posting.deadline_note or posting.deadline or ""
    missing = gap.labeled(skill_names, "missing")
    properties: dict[str, Any] = {
        "공고명": {"title": [{"text": {"content": posting.title[:2000]}}]},
        "회사": _choice(analyzed.company_name),
        "규모": _choice(analyzed.scale),
        # 상태와 달리 매 실행 다시 계산한다. 사용자가 고치는 값이 아니다.
        "우선순위": _choice(assign_priority(analyzed)),
        "지역": _text(posting.location),
        # 비어 있으면 빈 값을 보낸다. 속성을 빼면 예전 마감 정보가 남는다.
        "마감": _text(closing) if closing else {"rich_text": []},
        "보유 스킬": _options(gap.labeled(skill_names, "matched")),
        "부족 스킬": _options(missing),
        "핵심 부족 스킬": _options(n for n in missing if n in featured_skills),
        # 빠졌으면 빈 값을 보낸다. 속성을 빼면 사흘 전 칩이 그대로 남는다.
        "새 공고": _choice(NEW) if is_new else {"select": None},
        "어필 경험": _options(m.name for m in analyzed.experiences),
        "어필 포인트": _text(analyzed.pitch),
        "URL": {"url": posting.url},
        "key": _text(posting.key),
        # 결과에 올라온 행은 모집중이다. 규칙이 느슨해져 다시 들어온 행도
        # 여기서 모집중으로 돌아온다.
        "공고 현황": _choice(OPEN),
    }

    if not for_update:
        properties["상태"] = _choice(INITIAL_STATUS)

    return properties


def notion_request(
    token: str,
    method: str,
    path: str,
    body: Mapping[str, Any] | None = None,
    *,
    version: str = NOTION_VERSION,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Notion REST 호출 하나.

    본문이 없으면(GET, DELETE) data 를 싣지 않는다. 버전은 기본이 매일 적재용이고,
    Views API 가 필요한 설정 명령만 올려서 부른다. 매일 적재까지 올리면 DB 조회
    방식이 바뀌어 적재 코드 전체를 옮겨야 한다.
    """
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        method=method,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": version,
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    raw = opener(request, timeout=TIMEOUT_SEC).read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)


class HttpNotionClient:
    """Notion REST 클라이언트. 테스트는 이 자리에 대역을 넣는다."""

    def __init__(self, token: str, database_id: str, *, opener=urllib.request.urlopen):
        self._token = token
        self._database_id = database_id
        self._opener = opener

    def _request(self, method: str, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        return notion_request(self._token, method, path, body, opener=self._opener)

    def find_page(self, key: str) -> ExistingPage | None:
        payload = self._request(
            "POST",
            f"/databases/{self._database_id}/query",
            {
                "filter": {"property": "key", "rich_text": {"equals": key}},
                "page_size": 1,
            },
        )
        results = payload.get("results") or ()
        if not results:
            return None
        return ExistingPage(page_id=results[0]["id"], created_at=results[0].get("created_time", ""))

    def create_page(self, properties: Mapping[str, Any]) -> str:
        payload = self._request(
            "POST",
            "/pages",
            {"parent": {"database_id": self._database_id}, "properties": properties},
        )
        return payload.get("id", "")

    def update_page(self, page_id: str, properties: Mapping[str, Any]) -> None:
        self._request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def iter_rows(self) -> Iterator[tuple[str, str, str | None]]:
        """DB 의 모든 행을 (page_id, key, 공고 현황)으로 훑는다.

        휴지통으로 보낸 행은 Notion 조회에 나오지 않는다.
        """
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            payload = self._request("POST", f"/databases/{self._database_id}/query", body)

            for page in payload.get("results") or ():
                props = page.get("properties") or {}
                key = "".join(
                    t.get("plain_text", "") for t in (props.get("key") or {}).get("rich_text") or ()
                ).strip()
                selected = (props.get("공고 현황") or {}).get("select") or {}
                yield page.get("id", ""), key, selected.get("name")

            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")


@dataclass(frozen=True, slots=True)
class NotionSync:
    client: NotionClient

    @classmethod
    def from_env(cls) -> "NotionSync | None":
        """자격증명이 갖춰졌을 때만 동기화를 만든다. 없으면 None이다."""
        token = os.environ.get("NOTION_TOKEN", "").strip()
        database_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
        if not token or not database_id:
            return None
        return cls(client=HttpNotionClient(token, database_id))

    def push(
        self,
        rows: Iterable[AnalyzedPosting],
        skill_names: Mapping[str, str],
        *,
        featured_skills: Collection[str] = (),
        new_since: str | None = None,
    ) -> SyncResult:
        created = updated = failed = 0
        seen: set[str] = set()

        for analyzed in rows:
            key = analyzed.posting.key
            if key in seen:
                # 같은 실행에서 두 번 create 하면 중복 행이 생기고, 다음
                # 실행부터 어느 행이 조회될지 보장되지 않는다. 사용자가
                # 편집한 '상태'가 보이지 않게 되는 경로다.
                continue
            seen.add(key)
            try:
                found = self.client.find_page(key)
                if found:
                    self.client.update_page(
                        found.page_id,
                        build_properties(analyzed, skill_names, for_update=True,
                                         featured_skills=featured_skills,
                                         is_new=is_new(found.created_at, new_since)),
                    )
                    updated += 1
                else:
                    # 방금 만든 행은 언제나 새 공고다.
                    self.client.create_page(build_properties(
                        analyzed, skill_names, featured_skills=featured_skills, is_new=True))
                    created += 1
            except (OSError, ValueError, KeyError) as exc:
                failed += 1
                print(f"  ! {key}: Notion 적재 실패 — {exc}", file=sys.stderr)

        return SyncResult(created=created, updated=updated, failed=failed)

    def retire(
        self,
        *,
        active: Iterable[str],
        collected: Iterable[str],
        complete_sources: Iterable[str],
    ) -> RetireResult:
        """오늘 결과에 없는 행의 '공고 현황'을 제외됨·마감으로 바꾼다.

        '상태'는 건드리지 않는다. 공고가 마감돼도 사용자가 적어 둔 '지원함'은
        기록으로 남아야 한다. 이미 같은 값이면 쓰지 않는다.
        """
        active_set, collected_set = set(active), set(collected)
        complete_set = frozenset(complete_sources)
        excluded = closed = unchanged = skipped = failed = 0

        for page_id, key, current in self.client.iter_rows():
            if not key:
                # 사용자가 손으로 추가한 행일 수 있다.
                skipped += 1
                continue
            if key in active_set:
                # push 가 이미 모집중으로 썼다.
                continue

            target = listing_status(
                key, active=active_set, collected=collected_set, complete_sources=complete_set
            )
            if target is None:
                skipped += 1
                continue
            if target == current:
                unchanged += 1
                continue

            try:
                self.client.update_page(page_id, {"공고 현황": _choice(target)})
            except (OSError, ValueError, KeyError) as exc:
                failed += 1
                print(f"  ! {key}: 공고 현황 갱신 실패 — {exc}", file=sys.stderr)
                continue
            if target == EXCLUDED:
                excluded += 1
            else:
                closed += 1

        return RetireResult(
            excluded=excluded, closed=closed, unchanged=unchanged, skipped=skipped, failed=failed
        )
