"""청약홈 분양정보 (한국부동산원, odcloud 계열 API).

odcloud 는 응답 모양이 {page, perPage, totalCount, data:[...]} 로 일정하다.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from ..models import Listing, norm_date, pick
from .base import CollectorError, http_get_json

log = logging.getLogger(__name__)

HINT_KEYS = ("HOUSE_NM", "PBLANC_NO", "HOUSE_MANAGE_NO")


def collect(cfg: dict, api_key: str) -> list[Listing]:
    if not api_key:
        raise CollectorError("DATA_GO_KR_KEY 가 비어 있습니다. .env 를 확인하세요.")

    name = cfg["name"]
    endpoint = cfg["endpoint"]
    per_page = int(cfg.get("page_size", 100))
    cutoff = (date.today() - timedelta(days=int(cfg.get("lookback_days", 30)))).isoformat()

    results: list[Listing] = []
    for page in range(1, 21):
        payload = http_get_json(
            endpoint, {"serviceKey": api_key, "page": page, "perPage": per_page}
        )
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not rows:
            break

        stale_in_page = 0
        for row in rows:
            listing = _to_listing(row, name)
            if not listing:
                continue
            # 공고일이 lookback 밖이면 건너뛴다. odcloud 는 최신순 정렬이 보장되지
            # 않으므로 페이지를 바로 끊지 않고 카운트만 센다.
            if listing.notice_date and listing.notice_date < cutoff:
                stale_in_page += 1
                continue
            results.append(listing)

        if len(rows) < per_page:
            break
        # 한 페이지가 통째로 오래된 공고면 더 볼 필요가 없다.
        if stale_in_page == len(rows):
            break
        time.sleep(0.4)

    log.info("%s: %s건 수집", name, len(results))
    return results


def _to_listing(row: dict, source_name: str) -> Listing | None:
    title = pick(row, "HOUSE_NM", "PBLANC_NM")
    if not title:
        return None
    source_id = pick(row, "PBLANC_NO", "HOUSE_MANAGE_NO") or str(abs(hash(title)))

    house_kind = pick(row, "HOUSE_SECD_NM", "RENT_SECD_NM", default="")
    category = "임대" if "임대" in house_kind or "임대" in source_name else "분양"

    return Listing(
        source=source_name,
        source_id=source_id,
        title=title,
        category=category,
        region=pick(row, "SUBSCRPT_AREA_CODE_NM", "HSSPLY_ADRES"),
        supplier=pick(row, "BSNS_MBY_NM", "CNSTRCT_ENTRPS_NM"),
        notice_date=norm_date(pick(row, "RCRIT_PBLANC_DE")),
        apply_start=norm_date(pick(row, "RCEPT_BGNDE", "SUBSCRPT_RCEPT_BGNDE")),
        apply_end=norm_date(pick(row, "RCEPT_ENDDE", "SUBSCRPT_RCEPT_ENDDE")),
        status=house_kind,
        url=pick(row, "PBLANC_URL", "HMPG_ADRES"),
        raw=row,
    )
