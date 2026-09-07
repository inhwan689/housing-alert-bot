"""마이홈포털 공공주택 모집공고 조회 (공공데이터포털 1613000/HWSPR02).

2026-09-07 실측: 336건 중 306건이 LH, 나머지는 지방도시공사.

**이 API는 아카이브가 아니라 '지금 접수 가능한 공고'만 준다.** 336건 중 이미 마감된
건이 0건이고, `yearMt` 를 어떻게 주든(생략 포함) totalCount 가 336으로 같다.
공고가 마감되면 응답에서 사라지므로 **한 번 놓치면 되돌릴 방법이 없다.**

시군구명·전체주소가 들어오지만 전국단위 공고는 둘 다 비어 있다.
"""
from __future__ import annotations

import logging
import time

from ..models import Listing, norm_date, pick
from .base import CollectorError, find_rows, http_get_json

log = logging.getLogger(__name__)

NAME = "myhome_notice"

HINT_KEYS = ("pblancId", "pblancNm", "houseTyNm", "suplyTyNm")


def collect(cfg: dict, api_key: str) -> list[Listing]:
    if not api_key:
        raise CollectorError("DATA_GO_KR_KEY 가 비어 있습니다. .env 를 확인하세요.")

    endpoint = cfg["endpoint"]
    page_size = int(cfg.get("page_size", 100))

    # 기간 파라미터는 '모집공고월'(YYYYMM)이지 접수기간이 아니다. 여기에 lookback을
    # 걸면 안 된다 — '2026년 청년 전세임대 수시모집'처럼 공고는 2월에 나고 접수는
    # 연말까지 열려 있는 건이 통째로 잘린다 (2026-09-07 실측으로 확인한 버그).
    # 전 범위를 받아도 335건/4페이지 수준이라 비용이 없다. 오래된 건을 거르는 일은
    # filters.yaml 의 skip_closed(마감일 기준)가 맡는다.
    begin, end = "200001", "209912"

    # 지역/유형 코드값은 참고문서 xlsx에만 있어 확인 전이다. 코드를 넘기지 않고
    # 전체를 받은 뒤 응답의 이름 필드로 거른다 (filters.yaml 이 담당).
    results: list[Listing] = []
    for page in range(1, 21):  # 안전장치: 최대 20페이지
        params = {
            "serviceKey": api_key,
            "numOfRows": page_size,
            "pageNo": page,
            "yearMtBegin": begin,
            "yearMtEnd": end,
        }
        payload = http_get_json(endpoint, params)
        rows = find_rows(payload, HINT_KEYS)
        if not rows:
            break

        for row in rows:
            listing = _to_listing(row)
            if listing:
                results.append(listing)

        if len(rows) < page_size:
            break
        time.sleep(0.4)  # 공공 API 예의

    log.info("마이홈포털: %s건 수집", len(results))
    return results


def _to_listing(row: dict) -> Listing | None:
    title = pick(row, "pblancNm", "hsmpNm")
    if not title:
        return None

    # 한 공고에 단지가 여러 개 딸려 오므로 공고ID만으로는 서로 덮어쓴다.
    pblanc_id = pick(row, "pblancId")
    house_sn = pick(row, "houseSn")
    source_id = "-".join(x for x in (pblanc_id, house_sn) if x) or str(abs(hash(title)))

    return Listing(
        source=NAME,
        source_id=source_id,
        title=title,
        # 주택유형명(행복주택/국민임대/…)이 '임대' 한 단어보다 훨씬 쓸모 있다.
        category=pick(row, "houseTyNm", "suplyTyNm", default="임대"),
        # filters.yaml 의 지역 조건이 자치구까지 볼 수 있도록 시군구를 붙인다.
        region=" ".join(x for x in (pick(row, "brtcNm"), pick(row, "signguNm")) if x),
        supplier=pick(row, "suplyInsttNm"),
        notice_date=norm_date(pick(row, "rcritPblancDe")),
        apply_start=norm_date(pick(row, "beginDe")),
        apply_end=norm_date(pick(row, "endDe")),
        status=pick(row, "sttusNm"),
        url=pick(row, "pcUrl", "url", "mobileUrl"),
        raw=row,
    )
