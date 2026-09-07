"""DB에 쌓인 공고를 한 장짜리 HTML 대시보드로 그린다.

디스코드 푸시는 '새로 뜬 것'을 한 번 알리고 끝이라, 접수예정 공고를 계속 추적하거나
지난 공고를 다시 찾아보는 데 맞지 않는다. 대시보드는 DB 전체를 매 실행마다 다시
그리므로 상태(접수중/예정/마감)가 시간이 지나면 저절로 갱신된다.

외부 CDN을 쓰지 않는다 — 인터넷 없이 파일만 열어도 동작해야 한다.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta

from .config import DASHBOARD_PATH
from .filters import is_highlight, matches
from .models import Listing

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
        out.append({
            "uid": row["uid"],
            "src": SOURCE_LABEL.get(item.source, item.source),
            "title": item.title,
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


_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>주택공고 대시보드</title>
<style>
:root{
  --bg:#f6f7f9; --card:#fff; --fg:#16181d; --muted:#6b7280; --line:#e3e6ea;
  --accent:#3b6fd4; --star:#c8791b; --danger:#c0392b; --ok:#1f8a54;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#14161a; --card:#1c1f25; --fg:#e6e8ec; --muted:#9aa1ab; --line:#2b2f37;
         --accent:#6f9bec; --star:#e0a34a; --danger:#e8705f; --ok:#4cc38a; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.55 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:19px;margin:0 0 2px}
.sub{color:var(--muted);font-size:12px;margin-bottom:16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card b{display:block;font-size:24px;line-height:1.2}
.card span{color:var(--muted);font-size:12px}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px}
.tabs{display:flex;flex-wrap:wrap;gap:6px}
button.tab{background:var(--card);border:1px solid var(--line);color:var(--fg);
  padding:6px 12px;border-radius:999px;cursor:pointer;font-size:13px}
button.tab[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
input[type=search],select{background:var(--card);border:1px solid var(--line);color:var(--fg);
  padding:7px 10px;border-radius:8px;font-size:13px}
input[type=search]{min-width:200px;flex:1}
label.chk{display:inline-flex;align-items:center;gap:5px;font-size:13px;
  background:var(--card);border:1px solid var(--line);padding:6px 10px;border-radius:8px;cursor:pointer}
.list{display:flex;flex-direction:column;gap:8px}
.item{background:var(--card);border:1px solid var(--line);border-left-width:4px;
  border-radius:10px;padding:11px 13px;display:grid;
  grid-template-columns:64px 1fr auto;gap:12px;align-items:start}
.item.star{border-left-color:var(--star)}
.item.closed{opacity:.5}
.dday{font-weight:700;font-size:15px;text-align:center;padding-top:2px;white-space:nowrap}
.dday small{display:block;font-weight:400;font-size:11px;color:var(--muted)}
.dday.urgent{color:var(--danger)}
.dday.up{color:var(--accent)}
.t{font-weight:600;margin-bottom:3px;overflow-wrap:anywhere}
.t a{color:inherit;text-decoration:none}
.t a:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:12px;display:flex;flex-wrap:wrap;gap:5px 10px}
.badge{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);white-space:nowrap}
.badge.new{background:var(--ok);border-color:var(--ok);color:#fff}
.badge.mine{border-color:var(--accent);color:var(--accent)}
.right{text-align:right;font-size:12px;color:var(--muted);white-space:nowrap}
.empty{padding:50px 0;text-align:center;color:var(--muted)}
@media(max-width:640px){
  /* 폰에서는 요약 카드가 첫 화면을 다 먹지 않게 눌러 놓는다 — 공고가 먼저 보여야 한다. */
  .wrap{padding:14px 12px 40px}
  .cards{grid-template-columns:repeat(3,1fr);gap:6px}
  .card{padding:8px 9px}
  .card b{font-size:18px}
  .card span{font-size:11px}
  .item{grid-template-columns:52px 1fr;padding:10px 11px}
  .right{grid-column:2;text-align:left}
  input[type=search]{min-width:0}
}
</style>
</head>
<body>
<div class="wrap">
  <h1>주택공고 대시보드</h1>
  <div class="sub">갱신 {{GENERATED}} · 매 실행마다 다시 그려집니다</div>

  <div class="cards" id="cards"></div>

  <div class="controls">
    <div class="tabs" id="tabs"></div>
  </div>
  <div class="controls">
    <input type="search" id="q" placeholder="제목·지역·공급기관 검색">
    <select id="src"><option value="">모든 소스</option></select>
    <label class="chk"><input type="checkbox" id="mine" checked> 내 조건만</label>
    <label class="chk"><input type="checkbox" id="star"> 관심유형만</label>
  </div>

  <div class="list" id="list"></div>
</div>

<script id="data" type="application/json">{{DATA}}</script>
<script>
const ROWS = JSON.parse(document.getElementById('data').textContent);
const TABS = [
  ['open','접수중'], ['upcoming','접수예정'], ['soon','마감임박'],
  ['unknown','시작일미상'], ['closed','마감'], ['all','전체'],
];
let tab = 'open';

const $ = id => document.getElementById(id);

function inTab(r){
  if (tab === 'all') return true;
  if (tab === 'soon') return r.soon;
  return r.state === tab;
}
function visible(){
  const q = $('q').value.trim().toLowerCase();
  const src = $('src').value;
  const onlyMine = $('mine').checked, onlyStar = $('star').checked;
  return ROWS.filter(r => {
    if (!inTab(r)) return false;
    if (onlyMine && !r.mine) return false;
    if (onlyStar && !r.star) return false;
    if (src && r.src !== src) return false;
    if (q && !(r.title + ' ' + r.region + ' ' + r.supplier + ' ' + r.cat).toLowerCase().includes(q))
      return false;
    return true;
  });
}
function dday(r){
  if (r.state === 'upcoming')
    return `<div class="dday up">D${r.dStart > 0 ? '-' + r.dStart : 'DAY'}<small>시작</small></div>`;
  if (r.state === 'closed') return `<div class="dday"><small>마감</small></div>`;
  if (r.dEnd === null) return `<div class="dday"><small>미상</small></div>`;
  const cls = r.dEnd <= 3 ? ' urgent' : '';
  return `<div class="dday${cls}">D${r.dEnd > 0 ? '-' + r.dEnd : 'DAY'}<small>마감</small></div>`;
}
function render(){
  const rows = visible();
  $('list').innerHTML = rows.length ? rows.map(r => `
    <div class="item ${r.star ? 'star' : ''} ${r.state === 'closed' ? 'closed' : ''}">
      ${dday(r)}
      <div>
        <div class="t">${r.star ? '⭐ ' : ''}${r.url
          ? `<a href="${r.url}" target="_blank" rel="noopener">${esc(r.title)}</a>`
          : esc(r.title)}</div>
        <div class="meta">
          ${r.isNew ? '<span class="badge new">신규</span>' : ''}
          ${r.mine ? '<span class="badge mine">내 조건</span>' : ''}
          <span class="badge">${esc(r.src)}</span>
          ${r.cat ? `<span>${esc(r.cat)}</span>` : ''}
          ${r.region ? `<span>${esc(r.region)}</span>` : ''}
          ${r.supplier ? `<span>${esc(r.supplier)}</span>` : ''}
        </div>
      </div>
      <div class="right">${r.start || '?'}<br>~ ${r.end || '?'}</div>
    </div>`).join('') : '<div class="empty">조건에 맞는 공고가 없습니다.</div>';

  document.querySelectorAll('#tabs button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.k === tab)));
}
function esc(s){
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
function counts(){
  const mine = ROWS.filter(r => r.mine);
  return [
    ['접수중', mine.filter(r => r.state === 'open').length],
    ['접수예정', mine.filter(r => r.state === 'upcoming').length],
    ['마감임박', mine.filter(r => r.soon).length],
    ['내 조건 전체', mine.length],
    ['수집 전체', ROWS.length],
  ];
}

$('cards').innerHTML = counts()
  .map(([k, v]) => `<div class="card"><b>${v}</b><span>${k}</span></div>`).join('');
$('tabs').innerHTML = TABS
  .map(([k, l]) => `<button class="tab" data-k="${k}">${l}</button>`).join('');
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
