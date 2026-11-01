"""Gemini 클라이언트 공용 초기화.

쌍둥이 문제 생성(twin_question.py)과 힌트/해설 생성(hint_explain.py)이
동일한 API 키/모델 설정을 공유하므로 이 모듈로 분리했다.
API 키는 하드코딩하지 않고 환경변수(GEMINI_API_KEY)에서만 읽는다.
"""
from __future__ import annotations

import os
import re
import time

from config import GEMINI_MODEL_NAME


class GeminiError(RuntimeError):
    pass


class QuotaExceededError(GeminiError):
    """사용량 한도(429)에 걸림. retry_seconds는 다시 쓸 수 있을 때까지 남은 시간(모르면 None)."""

    def __init__(self, retry_seconds: float | None):
        self.retry_seconds = retry_seconds
        if retry_seconds:
            mins = int(retry_seconds // 60) + 1
            wait = f"약 {mins // 60}시간 {mins % 60}분" if mins >= 60 else f"약 {mins}분"
            msg = f"AI 사용량 한도를 다 썼어요 (무료 요금제). {wait} 뒤에 다시 쓸 수 있어요."
        else:
            msg = "AI 사용량 한도에 걸렸어요 (무료 요금제). 잠시 뒤에 다시 시도해주세요."
        super().__init__(msg)


# 한도에 한 번 걸리면 풀릴 때까지는 API를 부르지 않는다 (백그라운드 미리 풀기가 계속 두드리지 않게)
_blocked_until = 0.0


def quota_blocked_seconds() -> float:
    return max(0.0, _blocked_until - time.time())


def _retry_seconds(message: str) -> float | None:
    m = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", message)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", message)
    if m:
        h, mi, s = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + s
    return None


def _is_quota_error(e: Exception) -> bool:
    return type(e).__name__ == "ResourceExhausted" or "429" in str(e)[:20] or "exceeded your current quota" in str(e)


def generate(contents: list):
    """모든 Gemini 호출은 이 함수를 거친다. 한도 오류는 QuotaExceededError로 바꿔 올린다."""
    global _blocked_until
    blocked = quota_blocked_seconds()
    if blocked > 0:
        raise QuotaExceededError(blocked)
    model = get_gemini_model()
    try:
        return model.generate_content(contents)
    except Exception as e:
        if _is_quota_error(e):
            secs = _retry_seconds(str(e))
            _blocked_until = time.time() + (secs if secs else 60)
            raise QuotaExceededError(secs) from e
        raise


def get_gemini_model():
    try:
        import google.generativeai as genai
    except ImportError as e:
        raise GeminiError(
            "google-generativeai 패키지가 설치되어 있지 않습니다. pip install google-generativeai"
        ) from e

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiError(
            "환경변수 GEMINI_API_KEY가 설정되어 있지 않습니다. "
            "개인 Gemini API 키를 발급받아 Windows는 `set GEMINI_API_KEY=...`, "
            "macOS/Linux는 `export GEMINI_API_KEY=...` 로 설정한 뒤 "
            "같은 터미널에서 streamlit을 다시 실행하세요."
        )
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(GEMINI_MODEL_NAME)
