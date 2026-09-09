"""진입점: 수집 → 정규화 → 저장/변경판정 → 대시보드 갱신 (+ 선택적 디스코드 알림)."""
from __future__ import annotations

import argparse
import logging
import sys

from . import config, dashboard, store
from .collectors import applyhome_api, lh_api, myhome_api
from .collectors.base import mask
from .filters import apply_filters
from .notify import send

# 소스 이름 → 수집기. 소스를 늘려도 여기 한 줄만 추가하면 된다.
COLLECTORS = {
    "lh_notice": lh_api.collect,
    "myhome_notice": myhome_api.collect,
    "applyhome_apt": applyhome_api.collect,
    "applyhome_officetel": applyhome_api.collect,
    "applyhome_public_rent": applyhome_api.collect,
}


def setup_logging(verbose: bool) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_DIR / "bot.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="주택 분양·임대 공고 수집 및 대시보드 갱신")
    parser.add_argument("--dry-run", action="store_true",
                        help="콘솔에만 출력하고 알림기록을 남기지 않는다 (대시보드는 갱신됨)")
    parser.add_argument("--discord", action="store_true",
                        help="대시보드 외에 디스코드 웹훅으로도 새 공고를 보낸다")
    parser.add_argument("--seed", action="store_true",
                        help="현재 공고를 전부 '이미 알림 보낸 것'으로 저장만 한다. "
                             "최초 1회 실행해서 수백 건이 한꺼번에 날아오는 것을 막는다.")
    parser.add_argument("--source", help="이 이름의 소스 하나만 실행")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    log = logging.getLogger("main")

    con = store.connect()
    all_changes = []
    had_error = False
    ok_n = 0

    for source_cfg in config.SOURCES:
        name = source_cfg.get("name", "")
        if args.source and name != args.source:
            continue
        if not source_cfg.get("enabled", False):
            log.debug("건너뜀(비활성): %s", name)
            continue

        collector = COLLECTORS.get(name)
        if collector is None:
            log.warning("수집기가 아직 없는 소스: %s", name)
            continue

        label = source_cfg.get("label", name)
        try:
            listings = collector(source_cfg, config.DATA_GO_KR_KEY)
            changes = store.upsert_many(con, listings)
            new_n = sum(1 for _, c in changes if c == "new")
            upd_n = len(changes) - new_n
            store.log_run(con, name, len(listings), new_n, upd_n)
            log.info("%s: 수집 %s / 신규 %s / 변경 %s", label, len(listings), new_n, upd_n)
            all_changes.extend(changes)
            ok_n += 1
        except Exception as exc:  # 한 소스가 죽어도 나머지는 계속 간다
            had_error = True
            reason = mask(exc)  # 인증키가 로그/DB에 남지 않도록
            log.error("%s 수집 실패: %s", label, reason)
            store.log_run(con, name, 0, 0, 0, error=reason)

    # 대시보드는 DB 전체를 다시 그리므로 이번 실행에 새 공고가 없어도 갱신한다
    # (어제 '접수예정'이던 공고가 오늘 '접수중'으로 바뀌는 것을 반영하려면 필요하다).
    dashboard.build(con, config.FILTERS)

    if args.seed:
        # 필터를 거치지 않고 전부 처리 표시. 지금 존재하는 공고는 알리지 않는다.
        store.mark_notified(con, [item for item, _ in all_changes])
        log.info("시드 완료: %s건을 알림 완료로 표시. 이후 실행부터 새 공고만 알립니다.",
                 len(all_changes))
        con.close()
        return 1 if had_error and ok_n == 0 else 0

    kept = apply_filters(all_changes, config.FILTERS)
    # 마감 임박 순. 마감일을 모르는 건 뒤로 민다.
    kept.sort(key=lambda t: (t[0].apply_end or "9999-99-99", t[0].title))

    if args.discord:
        sent_ok = send(config.DISCORD_WEBHOOK_URL, kept, dry_run=args.dry_run)
        if sent_ok and not args.dry_run:
            store.mark_notified(con, [item for item, _ in kept])
    else:
        log.info("새 공고 %s건. 대시보드: %s", len(kept), config.DASHBOARD_PATH)

    con.close()
    # 한 소스가 죽어도 나머지가 살아 있으면 성공으로 끝낸다.
    # CI 는 종료코드가 0 이 아니면 뒤따르는 스텝(DB 커밋·Pages 배포)을 통째로 건너뛰므로,
    # 여기서 1 을 뱉으면 성공한 소스의 수집분까지 커밋 없이 버려진다.
    # 마이홈 API 는 마감된 공고를 더 이상 주지 않아 그날 치를 되살릴 방법이 없다.
    # (실제 사고: 2026-09-09 09:10 KST 실행. PROGRESS.md 참고)
    # 전부 실패했을 때만 1 — 그때는 어차피 커밋할 새 데이터가 없다.
    return 1 if had_error and ok_n == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
