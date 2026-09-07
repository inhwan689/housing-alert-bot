"""수집기 공통 유틸. HTTP 재시도와 '응답 어딘가에 있는 행 목록' 추출."""
from __future__ import annotations

import logging
import re
import time

import requests

from ..config import USER_AGENT

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"(serviceKey|apiKey|authKey)=[^&\s]+", re.IGNORECASE)


def mask(text: str) -> str:
    """로그·예외 메시지에 인증키가 남지 않도록 가린다."""
    return _KEY_RE.sub(r"\1=***", str(text))


class CollectorError(Exception):
    pass


class AuthError(CollectorError):
    """인증키 문제. 재시도해봐야 소용없다."""


def http_get_json(url: str, params: dict, *, retries: int = 3, timeout: int = 25) -> dict | list:
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url, params=params, timeout=timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )

            # 인증 실패는 재시도 대상이 아니다. 즉시 원인을 알려주고 끝낸다.
            if resp.status_code in (401, 403):
                raise AuthError(
                    f"HTTP {resp.status_code} — 인증키가 잘못됐거나 이 API 활용신청이 "
                    f"승인되지 않았습니다. data.go.kr에서 해당 API를 신청했는지, "
                    f".env의 DATA_GO_KR_KEY가 'Decoding' 키인지 확인하세요. "
                    f"({mask(resp.text[:150])})"
                )

            # 공공 API는 과부하 시 500을 흘리는 경우가 많아 재시도 대상으로 본다.
            if resp.status_code == 429 or resp.status_code >= 500:
                raise CollectorError(f"HTTP {resp.status_code}: {mask(resp.text[:200])}")

            if resp.status_code >= 400:
                raise CollectorError(f"HTTP {resp.status_code}: {mask(resp.text[:200])}")

            try:
                return resp.json()
            except ValueError:
                # 정상 응답인데 JSON이 아니면 대개 에러가 XML로 온 것이다.
                raise CollectorError(f"JSON 아님: {mask(resp.text[:300])}")

        except AuthError:
            raise
        except (CollectorError, requests.RequestException) as e:
            last_error = e

        if attempt < retries - 1:
            sleep = 2 ** attempt
            log.warning("요청 실패(%s/%s), %ss 후 재시도: %s",
                        attempt + 1, retries, sleep, mask(last_error))
            time.sleep(sleep)

    raise CollectorError(f"{url} 요청이 {retries}회 모두 실패: {mask(last_error)}")


def find_rows(payload, hint_keys: tuple[str, ...]) -> list[dict]:
    """응답 구조가 바뀌어도 버티도록, hint_keys를 가진 dict 리스트를 재귀로 찾는다.

    LH API는 [{resHeader:...}, {dsList:[...]}] 처럼 감싸는 모양이 제각각이라
    경로를 하드코딩하면 개편 때마다 깨진다.
    """
    if isinstance(payload, list):
        if payload and all(isinstance(x, dict) for x in payload):
            if any(k in payload[0] for k in hint_keys):
                return payload
        for item in payload:
            found = find_rows(item, hint_keys)
            if found:
                return found
    elif isinstance(payload, dict):
        for value in payload.values():
            found = find_rows(value, hint_keys)
            if found:
                return found
    return []
