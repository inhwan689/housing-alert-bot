"""DB에 쌓인 공고를 한 장짜리 HTML 대시보드로 그린다.

디스코드 푸시는 '새로 뜬 것'을 한 번 알리고 끝이라, 접수예정 공고를 계속 추적하거나
지난 공고를 다시 찾아보는 데 맞지 않는다. 대시보드는 DB 전체를 매 실행마다 다시
그리므로 상태(접수중/예정/마감)가 시간이 지나면 저절로 갱신된다.

외부 CDN을 쓰지 않는다 — 인터넷 없이 파일만 열어도 동작해야 한다.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta

from .config import DASHBOARD_PATH
from .filters import is_highlight, matches
from .models import Listing, pick

log = logging.getLogger(__name__)

# 이 날짜 안에 마감이면 '마감임박'
SOON_DAYS = 3
# first_seen 이 이 기간 안이면 '신규' 배지
NEW_DAYS = 3

SOURCE_LABEL = {
    "lh_notice": "LH",
    "myhome_notice": "마이홈",
    "applyhome_apt": "청약홈 APT",
    "applyhome_officetel": "청약홈 오피스텔",
    "applyhome_public_rent": "청약홈 민간임대",
}


def _row_to_listing(row: sqlite3.Row) -> Listing:
    return Listing(
        source=row["source"], source_id=row["source_id"], title=row["title"] or "",
        category=row["category"] or "", region=row["region"] or "",
        supplier=row["supplier"] or "", notice_date=row["notice_date"] or "",
        apply_start=row["apply_start"] or "", apply_end=row["apply_end"] or "",
        status=row["status"] or "", url=row["url"] or "",
    )


# 공고 제목의 행정 문구. 카드에서는 떼고, 원문은 title 속성으로 남긴다.
_NOISE = [
    r"\[[^\]]*\]",                                   # [서울지역본부] [정정공고] [2026.07.15]
    r"[(（][^)）]*\d{2}[.\s]\d{1,2}[^)）]*[)）]",       # ('26.07.30.) (2026.08.18) (26.09.15공고)
    r"(?:20)?\d{2}년\s*\d+차",                        # 26년 3차
    r"(?:20)?\d{2}년\s*\d+월",                        # '26년 9월
    r"20\d{2}년",
    r"입주자격\s*완화|자격\s*완화|소득기준\s*완화|예비\s*입주자|추가\s*입주자|입주자",
    r"(?:정례|상시|수시|추가|완화)?\s*모집\s*(?:공고|안내)?|공고",
]


def _short_title(title: str) -> str:
    t = title
    for p in _NOISE:
        t = re.sub(p, " ", t)
    t = re.sub(r"[‘’'`_/]+\s*$|^\s*[‘’'`]", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -·,_/")
    return t or title


_SIDO = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
    "경기도": "경기", "강원도": "강원", "강원특별자치도": "강원", "충청북도": "충북",
    "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주",
}


def _short_place(addr: str) -> str:
    """'서울특별시 강서구 공항대로81길 14' → '서울 강서구 공항대로81길'. 번지는 뗀다."""
    parts = addr.split()
    if not parts:
        return ""
    parts[0] = _SIDO.get(parts[0], parts[0])
    return " ".join(parts[:3])


def _won(v: str) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _details(source: str, raw: str) -> dict:
    """카드에 쓸 값을 원본 응답에서 꺼낸다. 소스마다 있는 필드가 다르다.

    금액은 마이홈 단지형 공고에만 있다. 매입·전세임대는 공고 하나에 집이 여러 채라
    0 으로 오고, LH·청약홈 목록 API에는 금액 필드가 아예 없다.
    """
    try:
        r = json.loads(raw or "{}")
    except ValueError:
        r = {}
    if source == "myhome_notice":
        return {
            "kind": pick(r, "suplyTyNm"),
            "house": pick(r, "houseTyNm"),
            "complex": pick(r, "hsmpNm"),
            "place": pick(r, "fullAdres") or " ".join(
                x for x in (pick(r, "brtcNm"), pick(r, "signguNm")) if x),
            "deposit": _won(pick(r, "rentGtn")),
            "rent": _won(pick(r, "mtRntchrg")),
            "units": _won(pick(r, "sumSuplyCo")),
        }
    if source == "lh_notice":
        return {"kind": pick(r, "AIS_TP_CD_NM"), "place": pick(r, "CNP_CD_NM")}
    if source.startswith("applyhome"):
        return {
            "kind": pick(r, "HOUSE_DTL_SECD_NM", "HOUSE_DETAIL_SECD_NM", "HOUSE_SECD_NM"),
            "place": pick(r, "HSSPLY_ADRES"),
            "units": _won(pick(r, "TOT_SUPLY_HSHLDCO")),
        }
    return {}


def _state(start: str, end: str, today: str) -> str:
    if end and end < today:
        return "closed"
    if start and start > today:
        return "upcoming"
    if start:
        return "open"
    # LH는 접수 시작일이 응답에 아예 없다. 마감일만 보고 판단한다.
    return "open" if end else "unknown"


def _days_until(iso: str, today_d: date) -> int | None:
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) - today_d).days
    except ValueError:
        return None


def collect_rows(con: sqlite3.Connection, filters_cfg: dict) -> list[dict]:
    today_d = date.today()
    today = today_d.isoformat()
    fresh_after = (datetime.now() - timedelta(days=NEW_DAYS)).isoformat(timespec="seconds")

    out: list[dict] = []
    for row in con.execute("SELECT * FROM listings"):
        item = _row_to_listing(row)
        state = _state(item.apply_start, item.apply_end, today)
        d_end = _days_until(item.apply_end, today_d)
        d_start = _days_until(item.apply_start, today_d)
        det = _details(item.source, row["raw"])
        out.append({
            "uid": row["uid"],
            "src": SOURCE_LABEL.get(item.source, item.source),
            "title": item.title,
            "name": det.get("complex") or _short_title(item.title),
            "kind": det.get("kind", ""),
            "house": det.get("house", ""),
            "place": _short_place(det.get("place", "")),
            "deposit": det.get("deposit", 0),
            "rent": det.get("rent", 0),
            "units": det.get("units", 0),
            "url": item.url if item.url.startswith("http") else "",
            "cat": item.category,
            "region": item.region,
            "supplier": item.supplier,
            "notice": item.notice_date,
            "start": item.apply_start,
            "end": item.apply_end,
            "state": state,
            "dEnd": d_end,
            "dStart": d_start,
            "soon": state == "open" and d_end is not None and d_end <= SOON_DAYS,
            "mine": matches(item, filters_cfg),
            "star": is_highlight(item, filters_cfg),
            "isNew": (row["first_seen"] or "") >= fresh_after,
        })

    # 마이홈은 LH 공고를 그대로 다시 싣는다. 같은 공고가 두 장 뜨지 않게, 금액·단지
    # 정보가 있는 마이홈 쪽만 남긴다. 화면에서만 숨기고 DB에는 둘 다 남아 있다.
    myhome_keys = {(r["title"], r["end"]) for r in out if r["src"] == "마이홈"}
    out = [r for r in out if not (r["src"] == "LH" and (r["title"], r["end"]) in myhome_keys)]

    # 급한 것부터: 접수중 → 접수예정 → 시작일미상 → 마감.
    order = {"open": 0, "upcoming": 1, "unknown": 2, "closed": 3}
    out.sort(key=lambda r: (
        order[r["state"]],
        r["end"] or "9999-99-99" if r["state"] != "upcoming" else r["start"] or "9999-99-99",
        r["title"],
    ))
    return out


def build(con: sqlite3.Connection, filters_cfg: dict):
    rows = collect_rows(con, filters_cfg)
    payload = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PATH.write_text(
        _TEMPLATE.replace("{{DATA}}", payload).replace("{{GENERATED}}", generated),
        encoding="utf-8",
    )
    log.info("대시보드: %s건 → %s", len(rows), DASHBOARD_PATH)
    return DASHBOARD_PATH


_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>주택공고</title>
<style>
/* 카드 하나의 초점은 '얼마에 사는가' 한 줄. 나머지는 크기·굵기·색으로 한 단계씩 낮춘다.
   깊이는 테두리 하나로만 낸다(그림자 없음). 강조색은 --key 하나, 상태색은 마감/신규뿐. */
:root{
  --paper:#f4f5f7; --sheet:#fff;
  --ink:#15171c; --ink-2:#3d424c; --ink-3:#6b717c; --ink-4:#9aa0aa;
  --rule:rgba(16,24,40,.09); --rule-strong:rgba(16,24,40,.18);
  --key:#2459d6; --key-wash:rgba(36,89,214,.08);
  --deadline:#d23c2a; --fresh:#16865a;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#111317; --sheet:#181b20;
    --ink:#eceef2; --ink-2:#c3c8d0; --ink-3:#8c929c; --ink-4:#626873;
    --rule:rgba(255,255,255,.08); --rule-strong:rgba(255,255,255,.16);
    --key:#7aa2f7; --key-wash:rgba(122,162,247,.12);
    --deadline:#f07560; --fresh:#4cc38a;
  }
}
*{box-sizing:border-box}
html{-webkit-font-smoothing:antialiased;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
  font:14px/1.5 Pretendard,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:20px 16px 56px}

header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:14px}
h1{font-size:20px;font-weight:700;margin:0;letter-spacing:-.01em}
.stamp{font-size:12px;color:var(--ink-3)}

.tabs{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none;border-bottom:1px solid var(--rule);margin-bottom:12px}
.tabs::-webkit-scrollbar{display:none}
.tabs button{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
  padding:10px 10px 9px;font:inherit;font-size:14px;font-weight:500;color:var(--ink-3);
  cursor:pointer;white-space:nowrap;min-height:44px}
.tabs button[aria-pressed="true"]{color:var(--ink);font-weight:700;border-bottom-color:var(--ink)}
.tabs .n{font-variant-numeric:tabular-nums;margin-left:4px;color:var(--ink-4);font-weight:500}
.tabs button[aria-pressed="true"] .n{color:var(--key)}

.tools{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.tools input[type=search],.tools select{font:inherit;font-size:14px;color:var(--ink);background:var(--sheet);
  border:1px solid var(--rule-strong);border-radius:8px;padding:0 12px;height:40px}
.tools input[type=search]{flex:1 1 220px;min-width:0}
.chip{display:inline-flex;align-items:center;height:40px;padding:0 14px;border-radius:8px;
  border:1px solid var(--rule-strong);background:var(--sheet);color:var(--ink-2);
  font-size:13px;font-weight:500;cursor:pointer;user-select:none}
.chip input{position:absolute;opacity:0;pointer-events:none}
.chip:has(input:checked){border-color:var(--key);color:var(--key);background:var(--key-wash)}
.chip:has(input:focus-visible){outline:2px solid var(--key);outline-offset:2px}

.list{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:10px}
.home{display:flex;flex-direction:column;gap:6px;min-width:0;padding:14px 16px;
  background:var(--sheet);border:1px solid var(--rule);border-radius:12px;
  color:inherit;text-decoration:none;
  transition:border-color .15s cubic-bezier(.23,1,.32,1),transform .12s cubic-bezier(.23,1,.32,1)}
a.home:hover{border-color:var(--rule-strong)}
a.home:active{transform:scale(.98)}
a.home:focus-visible{outline:2px solid var(--key);outline-offset:2px}
.home.closed{opacity:.55}

.top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.price{font-size:20px;font-weight:700;line-height:1.3;letter-spacing:-.01em;font-variant-numeric:tabular-nums}
.price .won{font-size:12px;font-weight:500;color:var(--ink-3);margin-left:3px}
.price .kind{font-size:14px;font-weight:600;color:var(--ink-2);margin-right:5px}
.lead{font-size:17px;font-weight:700;line-height:1.35;text-wrap:balance}
.name{font-size:15px;font-weight:600;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row{display:flex;align-items:baseline;justify-content:space-between;gap:10px;font-size:13px;color:var(--ink-3)}
.place{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.when{flex:none;font-size:12px;font-variant-numeric:tabular-nums}
.src{margin-left:auto;align-self:center;font-size:11px;color:var(--ink-4)}

.due{flex:none;font-size:13px;font-weight:700;font-variant-numeric:tabular-nums;
  padding:3px 8px;border-radius:6px;color:var(--ink-2);background:var(--paper)}
.due.hot{color:var(--deadline);background:color-mix(in srgb,var(--deadline) 10%,transparent)}
.due.later{color:var(--key);background:var(--key-wash)}
.due.gone{color:var(--ink-4);font-weight:500}

.tags{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin-top:auto;padding-top:2px}
.tag{font-size:12px;font-weight:500;line-height:1;padding:5px 7px;border-radius:5px;
  color:var(--ink-2);background:var(--paper)}
.tag.new{color:var(--fresh);background:color-mix(in srgb,var(--fresh) 12%,transparent)}


.empty{grid-column:1/-1;padding:56px 16px;text-align:center;color:var(--ink-3)}
.empty b{display:block;color:var(--ink-2);font-size:15px;margin-bottom:4px}

@media (max-width:640px){
  .wrap{padding:14px 12px 40px}
  .list{grid-template-columns:1fr;gap:8px}
  .home{padding:12px 14px}
  .tools input[type=search]{flex-basis:100%}
  .tools select{flex:1}
}
@media (prefers-reduced-motion:reduce){ .home{transition:none} a.home:active{transform:none} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>주택공고</h1>
    <span class="stamp">{{GENERATED}} 갱신</span>
  </header>

  <nav class="tabs" id="tabs" aria-label="접수 상태"></nav>

  <div class="tools">
    <input type="search" id="q" placeholder="단지·지역·유형 검색" aria-label="검색">
    <label class="chip"><input type="checkbox" id="mine" checked>내 조건</label>
    <label class="chip"><input type="checkbox" id="star">관심 유형</label>
    <select id="src" aria-label="출처"><option value="">모든 출처</option></select>
  </div>

  <div class="list" id="list"></div>
</div>

<script id="data" type="application/json">{{DATA}}</script>
<script>
const ROWS = JSON.parse(document.getElementById('data').textContent);
const TABS = [
  ['open','접수중'], ['soon','마감임박'], ['upcoming','접수예정'],
  ['unknown','시작일미상'], ['closed','마감'], ['all','전체'],
];
let tab = 'open';
const $ = id => document.getElementById(id);

function inTab(r, t){
  if (t === 'all') return true;
  if (t === 'soon') return r.soon;
  return r.state === t;
}
function passes(r){
  const q = $('q').value.trim().toLowerCase();
  const src = $('src').value;
  if ($('mine').checked && !r.mine) return false;
  if ($('star').checked && !r.star) return false;
  if (src && r.src !== src) return false;
  if (q && !(r.name + ' ' + r.title + ' ' + r.place + ' ' + r.kind + ' ' + r.supplier)
      .toLowerCase().includes(q)) return false;
  return true;
}

// 원 → 만원. 10만 미만은 소수 한 자리까지(영구임대 월세 5.3만 같은 값).
function man(won){
  const m = won / 10000;
  return m >= 10 ? Math.round(m).toLocaleString('ko-KR') : String(Math.round(m * 10) / 10);
}
function price(r){
  if (r.rent) return `<span class="kind">월세</span>${man(r.deposit)}/${man(r.rent)}<span class="won">만원</span>`;
  if (r.deposit) return `<span class="kind">전세</span>${man(r.deposit)}<span class="won">만원</span>`;
  return '';
}
function md(iso){ return iso ? iso.slice(5).replace('-', '.') : '?'; }
function due(r){
  if (r.state === 'closed') return '<span class="due gone">마감</span>';
  if (r.state === 'upcoming')
    return `<span class="due later">${r.dStart > 0 ? r.dStart + '일 뒤 시작' : '오늘 시작'}</span>`;
  if (r.dEnd === null) return '';
  const cls = r.dEnd <= 3 ? ' hot' : '';
  return `<span class="due${cls}">${r.dEnd > 0 ? 'D-' + r.dEnd : '오늘 마감'}</span>`;
}
function card(r){
  const p = price(r);
  const tags = [
    r.isNew ? '<span class="tag new">신규</span>' : '',
    r.kind ? `<span class="tag">${esc(r.kind)}</span>` : '',
    r.house ? `<span class="tag">${esc(r.house)}</span>` : '',
    r.units ? `<span class="tag">${r.units.toLocaleString('ko-KR')}세대</span>` : '',
  ].join('');
  const when = r.start ? `${md(r.start)} – ${md(r.end)}` : `~ ${md(r.end)}`;
  const tagName = r.url ? 'a' : 'div';
  const href = r.url ? ` href="${esc(r.url)}" target="_blank" rel="noopener"` : '';
  return `<${tagName} class="home${r.state === 'closed' ? ' closed' : ''}"${href} title="${esc(r.title)}">
    <div class="top">
      ${p ? `<div class="price">${p}</div>` : `<div class="lead">${esc(r.name)}</div>`}
      ${due(r)}
    </div>
    ${p ? `<div class="name">${esc(r.name)}</div>` : ''}
    <div class="row"><span class="place">${esc(r.place)}</span><span class="when">${when}</span></div>
    <div class="tags">${tags}<span class="src">${esc(r.src)}</span></div>
  </${tagName}>`;
}
function render(){
  const base = ROWS.filter(passes);
  $('tabs').innerHTML = TABS.map(([k, l]) =>
    `<button type="button" data-k="${k}" aria-pressed="${k === tab}">${l}<span class="n">${
      base.filter(r => inTab(r, k)).length}</span></button>`).join('');
  const rows = base.filter(r => inTab(r, tab));
  $('list').innerHTML = rows.length ? rows.map(card).join('')
    : '<div class="empty"><b>이 조건에 맞는 공고가 없어요</b>다른 탭을 보거나 \'내 조건\'을 꺼 보세요.</div>';
}
function esc(s){
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

[...new Set(ROWS.map(r => r.src))].sort().forEach(s => {
  const o = document.createElement('option'); o.value = o.textContent = s; $('src').append(o);
});
$('tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  tab = b.dataset.k; render();
});
['q', 'src', 'mine', 'star'].forEach(id => $(id).addEventListener('input', render));
render();
</script>
</body>
</html>
"""
