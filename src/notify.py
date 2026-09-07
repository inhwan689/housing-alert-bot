"""디스코드 웹훅 알림."""
from __future__ import annotations

import logging
import time

import requests

from .config import USER_AGENT
from .models import Listing

log = logging.getLogger(__name__)

EMBEDS_PER_MESSAGE = 10  # 디스코드 제한
COLOR_HIGHLIGHT = 0xE8A33D  # 관심 키워드 매치
COLOR_NEW = 0x4C7DF0
COLOR_UPDATED = 0x8E8E93

SOURCE_LABEL = {
    "lh_notice": "LH",
    "myhome_notice": "마이홈포털",
    "applyhome_apt": "청약홈 APT",
    "applyhome_officetel": "청약홈 오피스텔",
    "applyhome_public_rent": "청약홈 공공지원민간임대",
}


def _embed(item: Listing, change: str) -> dict:
    if item.highlight:
        color = COLOR_HIGHLIGHT
    elif change == "new":
        color = COLOR_NEW
    else:
        color = COLOR_UPDATED

    fields = []
    if item.region:
        fields.append({"name": "지역", "value": item.region, "inline": True})
    if item.category:
        fields.append({"name": "구분", "value": item.category, "inline": True})
    period = " ~ ".join(x for x in (item.apply_start, item.apply_end) if x)
    if period:
        fields.append({"name": "접수", "value": period, "inline": False})

    prefix = "🆕" if change == "new" else "🔄"
    if item.highlight:
        prefix = "⭐ " + prefix

    footer = SOURCE_LABEL.get(item.source, item.source)
    if item.notice_date:
        footer += " · 공고일 " + item.notice_date

    embed = {
        "title": f"{prefix} {item.title}"[:250],
        "color": color,
        "fields": fields,
        "footer": {"text": footer},
    }
    if item.url.startswith("http"):
        embed["url"] = item.url
    return embed


def send(webhook_url: str, changes: list[tuple[Listing, str]], *, dry_run: bool = False) -> bool:
    """전송 성공 여부. 실패하면 False — 해당 건은 다음 실행에 다시 시도된다."""
    if not changes:
        log.info("알릴 새 공고 없음")
        return True

    if dry_run or not webhook_url:
        for item, change in changes:
            star = "*" if item.highlight else " "
            print(f"{star} [{change:7}] {item.category:2} {item.region:8} {item.title}")
            print(f"      {item.apply_start or '?'} ~ {item.apply_end or '?'}  {item.url}")
        if not webhook_url and not dry_run:
            log.warning("DISCORD_WEBHOOK_URL 이 없어 콘솔로만 출력했습니다.")
        return dry_run

    ok = True
    batches = [changes[i:i + EMBEDS_PER_MESSAGE]
               for i in range(0, len(changes), EMBEDS_PER_MESSAGE)]

    for idx, batch in enumerate(batches, 1):
        body = {
            "username": "주택공고 알리미",
            "embeds": [_embed(item, change) for item, change in batch],
        }
        if len(batches) > 1:
            body["content"] = f"새 공고 {len(batch)}건 ({idx}/{len(batches)})"

        for _attempt in range(3):
            resp = requests.post(
                webhook_url, json=body, timeout=20,
                headers={"User-Agent": USER_AGENT},
            )
            if resp.status_code == 429:
                wait = float(resp.json().get("retry_after", 5))
                log.warning("디스코드 rate limit, %ss 대기", wait)
                time.sleep(wait + 0.5)
                continue
            if resp.status_code >= 400:
                log.error("디스코드 전송 실패 %s: %s", resp.status_code, resp.text[:300])
                ok = False
            break
        time.sleep(1.0)

    return ok
