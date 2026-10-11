"""서울시 청년안심주택 모집공고 (soco.seoul.go.kr).

공개 API는 없다. 목록 페이지가 그리는 XHR JSON 을 그대로 호출한다:
  POST /youth/pgm/home/yohome/bbsListJson.json  (bbsId=BMSR00015, pageIndex=n)
인증 없음. 한 페이지 10건, 최신 공고순.

목록 응답의 optn1=공고게시일, optn4=청약신청일(시작일 하나), optn3=사업자.
주소·접수 마감일은 필드가 없고 본문(content HTML)에만 있어 정규식으로 뽑는다.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta

import requests

from ..config import USER_AGENT
from ..models import Listing, pick
from .base import CollectorError

log = logging.getLogger(__name__)

VIEW_URL = "https://soco.seoul.go.kr/youth/bbs/BMSR00015/view.do?boardId={}&menuNo=400008"

_TAG = re.compile(r"<[^>]+>")
_ADDR = re.compile(r"(?:주택위치|소재지|위치)\s*[:：]\s*([^\n(]+)")
# '26. 10. 13. / 2026.10.13 / (연도 생략) 10. 15. 형태. 접수기간 문구에서 마지막 날짜를 마감일로 본다.
_DATE = re.compile(r"(?<!\d)(?:(\d{4}|\d{2})\s*\.\s*)?(\d{1,2})\s*\.\s*(\d{1,2})(?!\d)")


def collect(cfg: dict, api_key: str) -> list[Listing]:
    name = cfg["name"]
    cutoff = (date.today() - timedelta(days=int(cfg.get("lookback_days", 60)))).isoformat()

    results: list[Listing] = []
    for page in range(1, 31):
        rows = _post(cfg["endpoint"], {"bbsId": "BMSR00015", "pageIndex": page}).get("resultList") or []
        if not rows:
            break
        for row in rows:
            listing = _to_listing(row, name)
            if listing:
                results.append(listing)
        # 최신순이라 페이지 마지막 공고가 cutoff 이전이면 더 볼 필요가 없다.
        if pick(rows[-1], "optn1") < cutoff:
            break
        time.sleep(1)

    results = [x for x in results if x.notice_date >= cutoff]
    log.info("%s: %s건 수집", name, len(results))
    return results


def _post(url: str, data: dict, retries: int = 3) -> dict:
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, data=data, timeout=25, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise CollectorError(f"{url} 요청이 {retries}회 모두 실패: {last_error}")


def _text(html: str) -> str:
    t = _TAG.sub("\n", html or "").replace("&nbsp;", " ")
    return re.sub(r"\n\s*\n+", "\n", t)


def _apply_end(text: str, start: str) -> str:
    """'청약신청 : '26. 10. 13.(화) 09:00 ~ '26. 10. 15.(목) 17:00' → 2026-10-15."""
    i = text.find("청약신청")
    if i < 0:
        i = text.find("접수기간")
    if i < 0:
        return start
    seg = text[i:i + 80].split("■")[0].split("[")[0]
    found = _DATE.findall(seg)
    if not found:
        return start
    y, m, d = found[-1]
    # '~ 10. 15.' 처럼 연도를 생략하면 시작일 연도를 쓴다.
    y = int(start[:4]) if not y else int(y) + 2000 if len(y) == 2 else int(y)
    end = f"{y:04d}-{int(m):02d}-{int(d):02d}"
    return end if end >= start else start


def _to_listing(row: dict, source_name: str) -> Listing | None:
    title = pick(row, "nttSj")
    board_id = pick(row, "boardId")
    if not title or not board_id:
        return None

    text = _text(row.get("content", ""))
    m = _ADDR.search(text)
    region = m.group(1).strip() if m else "서울특별시"
    start = pick(row, "optn4")

    kind = {"1": "공공", "2": "민간"}.get(pick(row, "optn2"), "")
    rnd = {"1": "최초", "2": "추가"}.get(pick(row, "optn5"), "")

    return Listing(
        source=source_name,
        source_id=board_id,
        title=title,
        category="임대",
        region=region,
        supplier=pick(row, "optn3"),
        notice_date=pick(row, "optn1"),
        apply_start=start,
        apply_end=_apply_end(text, start) if start else "",
        status=f"청년안심주택 {kind}{rnd}".strip(),
        url=VIEW_URL.format(board_id),
        raw=row,
    )
