"""LH 분양임대공고문 조회 (공공데이터포털 B552555)."""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from ..models import Listing, norm_date, pick
from .base import CollectorError, find_rows, http_get_json

log = logging.getLogger(__name__)

NAME = "lh_notice"

# 공고유형코드 → 공통 카테고리
TYPE_LABEL = {"05": "분양", "06": "임대"}

HINT_KEYS = ("PAN_NM", "PAN_ID", "CNP_CD_NM", "AIS_TP_CD_NM")


def collect(cfg: dict, api_key: str) -> list[Listing]:
    if not api_key:
        raise CollectorError("DATA_GO_KR_KEY 가 비어 있습니다. .env 를 확인하세요.")

    endpoint = cfg["endpoint"]
    page_size = int(cfg.get("page_size", 100))
    lookback = int(cfg.get("lookback_days", 30))

    start = (date.today() - timedelta(days=lookback)).strftime("%Y%m%d")
    # 마감일 상한. 아직 접수 전인 공고까지 포함하려고 넉넉히 잡는다.
    end = (date.today() + timedelta(days=400)).strftime("%Y%m%d")

    results: list[Listing] = []
    for type_code in cfg.get("notice_types", ["05", "06"]):
        for page in range(1, 21):  # 안전장치: 최대 20페이지
            params = {
                "serviceKey": api_key,
                "PG_SZ": page_size,
                "PAGE": page,
                "UPP_AIS_TP_CD": type_code,
                "PAN_NT_ST_DT": start,
                "CLSG_DT": end,
            }
            payload = http_get_json(endpoint, params)
            rows = find_rows(payload, HINT_KEYS)
            if not rows:
                break

            for row in rows:
                listing = _to_listing(row, type_code)
                if listing:
                    results.append(listing)

            if len(rows) < page_size:
                break
            time.sleep(0.4)  # 공공 API 예의

    log.info("LH: %s건 수집", len(results))
    return results


def _to_listing(row: dict, type_code: str) -> Listing | None:
    source_id = pick(row, "PAN_ID", "PAN_SEQ", "SEQ", "AIS_TP_CD")
    title = pick(row, "PAN_NM", "TITLE")
    if not title:
        return None
    if not source_id:
        source_id = str(abs(hash(title)))  # 식별자가 없으면 제목으로라도 고정

    return Listing(
        source=NAME,
        source_id=source_id,
        title=title,
        category=TYPE_LABEL.get(type_code, pick(row, "AIS_TP_CD_NM", default="기타")),
        region=pick(row, "CNP_CD_NM", "AREA_NM", "SIDO_NM"),
        supplier="LH",
        notice_date=norm_date(pick(row, "PAN_NT_ST_DT", "PAN_DT")),
        apply_end=norm_date(pick(row, "CLSG_DT", "PAN_ED_DT")),
        status=pick(row, "PAN_SS", "PAN_SS_NM"),
        url=pick(row, "DTL_URL", "PAN_URL", "URL"),
        raw=row,
    )
