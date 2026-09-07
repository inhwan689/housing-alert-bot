"""새 소스를 붙이기 전에 응답 구조를 눈으로 확인하는 도구.

사용:
    python scripts/probe.py lh_notice
    python scripts/probe.py applyhome_apt

원본 응답이 data/raw/<소스>.json 에 저장된다. 필드명이 예상과 다르면
그 파일을 보고 collectors/*.py 의 pick(...) 후보 키를 고치면 된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.collectors.base import http_get_json  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("사용 가능한 소스:", ", ".join(s["name"] for s in config.SOURCES))
        return 2

    name = sys.argv[1]
    cfg = next((s for s in config.SOURCES if s["name"] == name), None)
    if cfg is None:
        print(f"config/sources.yaml 에 '{name}' 소스가 없습니다.")
        return 2

    if name.startswith("applyhome"):
        params = {"serviceKey": config.DATA_GO_KR_KEY, "page": 1, "perPage": 5}
    elif name == "myhome_notice":
        params = {
            "serviceKey": config.DATA_GO_KR_KEY, "numOfRows": 5, "pageNo": 1,
            "yearMtBegin": "202501", "yearMtEnd": "209912",
        }
    else:
        params = {
            "serviceKey": config.DATA_GO_KR_KEY, "PG_SZ": 5, "PAGE": 1,
            "UPP_AIS_TP_CD": "06", "PAN_NT_ST_DT": "20250101", "CLSG_DT": "20991231",
        }

    payload = http_get_json(cfg["endpoint"], params)

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = config.RAW_DIR / f"{name}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"저장: {out}\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
