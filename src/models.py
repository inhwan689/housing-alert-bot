"""소스마다 제각각인 응답을 담을 공통 스키마."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# 이 필드들이 하나라도 바뀌면 '내용이 갱신된 공고'로 판정한다.
HASH_FIELDS = ("title", "region", "category", "apply_start", "apply_end", "status", "url")

_DATE_RE = re.compile(r"(\d{4})[.\-/]?(\d{2})[.\-/]?(\d{2})")


def norm_date(value) -> str:
    """20250101 / 2025-01-01 / 2025.01.01 → 2025-01-01. 못 읽으면 빈 문자열."""
    if not value:
        return ""
    m = _DATE_RE.search(str(value))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def pick(row: dict, *keys, default: str = "") -> str:
    """소스가 필드명을 바꿔도 견디도록 후보 키를 순서대로 시도한다."""
    for k in keys:
        v = row.get(k)
        if v not in (None, "", "null"):
            return str(v).strip()
    return default


@dataclass
class Listing:
    source: str
    source_id: str
    title: str
    category: str = ""      # 분양 | 임대 | 기타
    region: str = ""
    supplier: str = ""
    notice_date: str = ""
    apply_start: str = ""
    apply_end: str = ""
    status: str = ""
    url: str = ""
    raw: dict = field(default_factory=dict)
    highlight: bool = False  # 필터 단계에서 채워짐. 해시에는 포함하지 않는다.

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def content_hash(self) -> str:
        blob = "|".join(str(getattr(self, f) or "") for f in HASH_FIELDS)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
