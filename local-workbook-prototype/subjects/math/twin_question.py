"""Gemini를 이용한 '쌍둥이 문제' 생성 (수학 과목 전용).

설계 방침:
  - 틀린 문제의 원본 이미지를 그대로 멀티모달 입력으로 전달한다.
    (별도 텍스트 추출/재구성 파이프라인 불필요 -> 구현 단순화 + 수식/그림 정보 손실 방지)
  - 도형/그래프/작도처럼 그림이 문제 성립에 필수인 유형은 answer_rules.is_twin_question_eligible
    에서 이미 걸러지므로, 이 모듈이 호출되는 시점에는 "이미지 기반 재생성이 가능한 문제"라고
    가정한다. (app.py가 호출 전에 반드시 체크한다.)
"""
from __future__ import annotations

import re
from pathlib import Path

from subjects.math.gemini_client import GeminiError, get_gemini_model

TwinQuestionError = GeminiError

PROMPT_TEMPLATE = """중1 수준. 이 문제와 숫자/문맥만 바꾼 쌍둥이 문제 1개를 정답과 해설 포함해서 만들어줘.
반드시 아래 형식을 정확히 지켜서 답해줘 (다른 설명 문구 추가하지 말 것):

[문제]
(쌍둥이 문제 내용)

[정답]
(정답만 간결하게)

[해설]
(간단한 풀이 과정)
"""

_SECTION_PATTERN = re.compile(
    r"\[문제\]\s*(?P<problem>.*?)\s*\[정답\]\s*(?P<answer>.*?)\s*\[해설\]\s*(?P<explanation>.*)",
    re.DOTALL,
)


def _parse_response(raw_text: str) -> dict:
    m = _SECTION_PATTERN.search(raw_text)
    if not m:
        # 형식이 안 지켜져도 최소한 원문은 보여줄 수 있게 fallback
        return {"problem": raw_text.strip(), "answer": "", "explanation": "", "raw": raw_text}
    return {
        "problem": m.group("problem").strip(),
        "answer": m.group("answer").strip(),
        "explanation": m.group("explanation").strip(),
        "raw": raw_text,
    }


def generate_twin_question(image_path: Path) -> dict:
    """image_path: 틀린 문제의 원본 크롭 이미지(q_XX.png) 경로.

    반환: {"problem": str, "answer": str, "explanation": str, "raw": str}
    """
    from PIL import Image

    model = get_gemini_model()
    image = Image.open(image_path)
    response = model.generate_content([PROMPT_TEMPLATE, image])
    return _parse_response(response.text)
