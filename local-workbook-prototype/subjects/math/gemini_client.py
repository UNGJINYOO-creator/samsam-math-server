"""Gemini 클라이언트 공용 초기화.

쌍둥이 문제 생성(twin_question.py)과 힌트/해설 생성(hint_explain.py)이
동일한 API 키/모델 설정을 공유하므로 이 모듈로 분리했다.
API 키는 하드코딩하지 않고 환경변수(GEMINI_API_KEY)에서만 읽는다.
"""
from __future__ import annotations

import os

from config import GEMINI_MODEL_NAME


class GeminiError(RuntimeError):
    pass


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
