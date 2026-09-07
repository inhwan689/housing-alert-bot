"""내 조건에 맞는 공고만 남긴다."""
from __future__ import annotations

import logging
from datetime import date

from .models import Listing

log = logging.getLogger(__name__)


def _haystack(item: Listing) -> str:
    return f"{item.title} {item.region} {item.status} {item.category}"


def matches(item: Listing, cfg: dict) -> bool:
    """지역·제외어 조건에 맞는가. 마감 여부는 보지 않는다 — 호출자가 판단한다.

    대시보드는 마감된 공고도 '마감' 탭에 보여줘야 해서 두 판정을 분리했다.
    """
    haystack = _haystack(item)
    exclude = [s for s in (cfg.get("exclude_keywords") or []) if s]
    if any(word in haystack for word in exclude):
        return False

    # 지역 정보가 아예 없는 공고는 지역 필터로 버리지 않는다. 청년전세임대처럼
    # 전국 단위로 뜨는 공고가 여기 해당하는데, 오히려 가장 놓치면 안 되는 것들이다.
    region_include = [s for s in (cfg.get("region_include") or []) if s]
    if region_include and item.region and not any(word in haystack for word in region_include):
        return False
    return True


def is_highlight(item: Listing, cfg: dict) -> bool:
    highlight = [s for s in (cfg.get("highlight_keywords") or []) if s]
    return any(word in _haystack(item) for word in highlight)


def apply_filters(changes: list[tuple[Listing, str]], cfg: dict) -> list[tuple[Listing, str]]:
    skip_closed = bool(cfg.get("skip_closed", True))
    notify_on_update = bool(cfg.get("notify_on_update", True))
    today = date.today().isoformat()

    kept: list[tuple[Listing, str]] = []
    for item, change in changes:
        if change == "updated" and not notify_on_update:
            continue

        if not matches(item, cfg):
            continue

        # 마감일을 못 읽은 공고는 버리지 않는다 (놓치는 것보다 낫다).
        if skip_closed and item.apply_end and item.apply_end < today:
            continue

        item.highlight = is_highlight(item, cfg)
        kept.append((item, change))

    log.info("필터: %s건 중 %s건 통과", len(changes), len(kept))
    return kept
