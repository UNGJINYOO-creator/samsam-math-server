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


class AIResponse:
    def __init__(self, text: str, provider: str):
        self.text = text
        self.provider = provider  # "gemini" | "nvidia"


def _image_count(contents: list) -> int:
    return sum(1 for c in contents if not isinstance(c, str))


def use_nvidia_now() -> bool:
    """지금 Gemini 대신 NVIDIA(보조 AI)를 써야 하는 상황인지:
    NVIDIA 키가 있고, (Gemini가 한도에 걸렸거나 / Gemini 키가 없거나 / AI_PROVIDER=nvidia로 고정)."""
    from subjects.math.nvidia_client import nvidia_available

    if not nvidia_available():
        return False
    return (
        os.environ.get("AI_PROVIDER", "").lower() == "nvidia"
        or quota_blocked_seconds() > 0
        or not os.environ.get("GEMINI_API_KEY")
    )


def generate(contents: list, provider: str | None = None) -> AIResponse:
    """모든 AI 호출은 이 함수를 거친다.

    평소엔 Gemini. Gemini가 한도에 걸리면 NVIDIA 키가 있을 때 NVIDIA로 자동 전환한다.
    NVIDIA는 요청 1번에 이미지 1장만 보내도록 쓴다(여러 장을 못 받는 모델이 많아서).
    여러 장짜리 요청은 전환하지 않고 QuotaExceededError를 올리며, 부르는 쪽(ai_solver)이
    한 장씩 나눠서 다시 부른다. provider="gemini"/"nvidia"로 강제할 수도 있다(비교 스크립트용).
    """
    global _blocked_until
    from subjects.math import nvidia_client

    if provider == "nvidia" or (provider is None and use_nvidia_now() and _image_count(contents) <= 1):
        resp = nvidia_client.generate(contents)
        return AIResponse(resp.text, "nvidia")

    blocked = quota_blocked_seconds()
    if blocked > 0:
        raise QuotaExceededError(blocked)
    model = get_gemini_model()
    try:
        return AIResponse(model.generate_content(contents).text, "gemini")
    except Exception as e:
        if _is_quota_error(e):
            secs = _retry_seconds(str(e))
            _blocked_until = time.time() + (secs if secs else 60)
            if provider is None and nvidia_client.nvidia_available() and _image_count(contents) <= 1:
                return AIResponse(nvidia_client.generate(contents).text, "nvidia")
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
