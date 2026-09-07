"""API 키 없이 핵심 로직(저장 → 변경판정 → 중복방지 → 필터)을 검증한다.

사용: python scripts/selftest.py
임시 DB(data/selftest.db)를 쓰고 끝나면 지운다. 실제 DB는 건드리지 않는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import store  # noqa: E402
from src.filters import apply_filters  # noqa: E402
from src.models import Listing, norm_date  # noqa: E402
from src.notify import send  # noqa: E402

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}: {actual!r} != {expected!r}")
        failures.append(label)


def make(source_id: str, title: str, **kw) -> Listing:
    base = dict(region="서울특별시", category="임대", apply_end="2099-12-31", url="https://example.com/x")
    base.update(kw)
    return Listing(source="test", source_id=source_id, title=title, **base)


def main() -> int:
    store.DB_PATH = Path(__file__).resolve().parent.parent / "data" / "selftest.db"
    store.DB_PATH.unlink(missing_ok=True)
    con = store.connect()

    print("날짜 정규화")
    check("20250103", norm_date("20250103"), "2025-01-03")
    check("2025.01.03", norm_date("2025.01.03"), "2025-01-03")
    check("빈 값", norm_date(None), "")
    check("이상한 값", norm_date("추후공지"), "")

    # 대기열의 기준은 '저장했는가'가 아니라 '알림을 보냈는가'다.
    # mark_notified 를 호출해야만 대기열에서 빠진다.
    print("\n신규 판정")
    a = make("A1", "행복주택 청년 입주자 모집")
    changes = store.upsert_many(con, [a])
    check("처음 보면 new", [c for _, c in changes], ["new"])

    print("\n전송 실패 시 재시도")
    changes = store.upsert_many(con, [a])
    check("아직 안 보냈으면 계속 대기열에 남음", [c for _, c in changes], ["new"])

    print("\n중복 방지")
    store.mark_notified(con, [a])
    changes = store.upsert_many(con, [a])
    check("알림 후 같은 내용은 무시", changes, [])

    print("\n변경 판정")
    changed = make("A1", "행복주택 청년 입주자 모집", apply_end="2100-01-31")
    changes = store.upsert_many(con, [changed])
    check("마감일이 바뀌면 updated", [c for _, c in changes], ["updated"])

    changes = store.upsert_many(con, [changed])
    check("변경분 전송 실패도 다시 잡힘", [c for _, c in changes], ["updated"])

    store.mark_notified(con, [changed])
    changes = store.upsert_many(con, [changed])
    check("변경분 알림 후에는 안 잡힘", changes, [])

    print("\n필터")
    cfg = {
        "region_include": ["서울"],
        "exclude_keywords": ["상가"],
        "highlight_keywords": ["청년"],
        "skip_closed": True,
        "notify_on_update": True,
    }
    pool = [
        (make("B1", "청년 매입임대 모집"), "new"),
        (make("B2", "부산 신혼부부 임대", region="부산광역시"), "new"),
        (make("B3", "근린생활시설 상가 공급"), "new"),
        (make("B4", "마감된 공고", apply_end="2000-01-01"), "new"),
        (make("B5", "마감일 미상 공고", apply_end=""), "new"),
    ]
    kept = apply_filters(pool, cfg)
    check("통과한 공고", sorted(i.source_id for i, _ in kept), ["B1", "B5"])
    check("청년 공고는 강조", next(i.highlight for i, _ in kept if i.source_id == "B1"), True)
    check("강조어 없으면 일반", next(i.highlight for i, _ in kept if i.source_id == "B5"), False)

    print("\n알림 출력(dry-run)")
    check("웹훅 없이도 안전하게 처리", send("", kept, dry_run=True), True)

    con.close()
    store.DB_PATH.unlink(missing_ok=True)

    print()
    if failures:
        print(f"실패 {len(failures)}건: {', '.join(failures)}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
