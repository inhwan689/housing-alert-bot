"""SH공사(서울주택도시공사) 공고·공지 게시판 (i-sh.co.kr).

공개 API도 XHR JSON도 없다. 목록·상세 모두 서버가 HTML로 그린다:
  POST .../brd/m_247/list.do  (page=n)  한 페이지 10건, 최신순
  POST .../brd/m_247/view.do  (seq=n)   목록의 getDetailView('seq') 값
그래서 HTML 파싱으로 고정한다.

게시판에는 모집공고 외에 당첨자 발표·계약 안내·용역 공고가 섞여 있다.
전부 저장하되, 모집공고가 아닌 글은 category="공지"로 두어 filters.yaml 에서 거른다.
접수기간은 본문에 적힌 경우만 뽑는다. 첨부 PDF에만 있으면 비워 둔다.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta

import requests

from ..config import USER_AGENT
from .base import CollectorError
from ..models import Listing

log = logging.getLogger(__name__)

BASE = "https://www.i-sh.co.kr/main/lay2/program/S1T294C297/www/brd/m_247/"
VIEW_URL = BASE + "view.do?seq={}"

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_SEQ = re.compile(r"getDetailView\('(\d+)'\)")
_TAG = re.compile(r"<[^>]+>")
_GU = re.compile(
    "(종로구|중구|용산구|성동구|광진구|동대문구|중랑구|성북구|강북구|도봉구|노원구|은평구|서대문구|"
    "마포구|양천구|강서구|구로구|금천구|영등포구|동작구|관악구|서초구|강남구|송파구|강동구)"
)
_PERIOD = re.compile(r"모집신청일|신청기간|접수기간|접수일시|접수일|청약신청|접수일정")
_DATE = re.compile(r"(?<!\d)(?:(\d{4}|\d{2})\s*\.\s*)?(\d{1,2})\s*\.\s*(\d{1,2})(?!\d)")

# 제목에 '모집'이 있어도 이 단어가 있으면 결과·후속 안내다.
_NOT_RECRUIT = ("당첨자", "발표", "계약", "서류", "심사", "결과", "순번", "대상자", "경쟁률", "열람", "사전점검", "용역")


def collect(cfg: dict, api_key: str) -> list[Listing]:
    name = cfg["name"]
    cutoff = (date.today() - timedelta(days=int(cfg.get("lookback_days", 30)))).isoformat()

    rows: list[tuple[str, str, str, str]] = []
    for page in range(1, 61):
        page_rows = _parse_list(_post("list.do", {"page": page}))
        if not page_rows:
            break
        rows.extend(r for r in page_rows if r[3] >= cutoff)
        # 상단 고정 공지가 없는 게시판이라 마지막 행 날짜로 멈출 수 있다.
        if page_rows[-1][3] < cutoff:
            break
        time.sleep(1)

    results = []
    for seq, title, dept, posted in rows:
        recruit = _is_recruit(title)
        body = ""
        if recruit:
            time.sleep(1)
            body = _text(_post("view.do", {"seq": seq}))
        results.append(_to_listing(seq, title, dept, posted, recruit, body, name))

    log.info("%s: %s건 수집 (모집공고 %s건)", name, len(results), sum(r.category != "공지" for r in results))
    return results


def _post(path: str, data: dict, retries: int = 3) -> str:
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.post(BASE + path, data=data, timeout=25, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise CollectorError(f"{BASE + path} 요청이 {retries}회 모두 실패: {last_error}")


def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).replace("&nbsp;", " ").strip()


def _text(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return _clean(html)


def _parse_list(html: str) -> list[tuple[str, str, str, str]]:
    """(seq, 제목, 부서, 게시일) 목록. 열 순서: 번호·제목·부서·게시일·조회수."""
    out = []
    for tr in _ROW.findall(html):
        m = _SEQ.search(tr)
        tds = [_clean(x) for x in _TD.findall(tr)]
        if m and len(tds) >= 4:
            out.append((m.group(1), tds[1], tds[2], tds[3]))
    return out


def _is_recruit(title: str) -> bool:
    return "모집" in title and not any(w in title for w in _NOT_RECRUIT)


def _apply_period(body: str, posted: str) -> tuple[str, str]:
    """'접수기간 : 2026.10.08.(목) ~2026.10.22.(목)' / '모집신청일: 2026년 10월 2일(금) ~ 2026년 10월 11일(일)'
    → (시작일, 마감일). 키워드 뒤 80자 안에 날짜가 있는 첫 구간을 쓴다."""
    for m in _PERIOD.finditer(body):
        seg = body[m.end():m.end() + 80]
        seg = re.sub(r"(\d)\s*[년월]\s*", r"\1.", seg)
        seg = re.sub(r"(\d)\s*일", r"\1.", seg)
        dates, year = [], int(posted[:4])
        for y, mo, d in _DATE.findall(seg)[:2]:
            if y:
                year = int(y) + 2000 if len(y) == 2 else int(y)
            try:
                dates.append(date(year, int(mo), int(d)).isoformat())
            except ValueError:
                break
        if dates:
            return dates[0], max(dates[-1], dates[0])
    return "", ""


def _to_listing(seq, title, dept, posted, recruit, body, source_name) -> Listing:
    gu = _GU.search(title)
    start, end = _apply_period(body, posted) if body else ("", "")
    if not recruit:
        category = "공지"
    else:
        category = "분양" if "분양" in title else "임대"
    return Listing(
        source=source_name,
        source_id=seq,
        title=title,
        category=category,
        region=f"서울특별시 {gu.group(1)}" if gu else "서울특별시",
        supplier=f"SH공사 {dept}".strip(),
        notice_date=posted,
        apply_start=start,
        apply_end=end,
        status="SH 모집공고" if recruit else "SH 공지",
        url=VIEW_URL.format(seq),
        raw={"seq": seq, "title": title, "dept": dept, "posted": posted, "body": body[:4000]},
    )
