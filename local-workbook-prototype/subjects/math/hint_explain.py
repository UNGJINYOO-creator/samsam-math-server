"""오답 시 1차 힌트 + 2차 오답 해설 생성 (수학 과목 전용, Gemini 사용).

새 채점 흐름:
  1차 시도 오답 -> 최종 답은 알려주지 않고 "어떤 식/개념을 써야 하는지"만 힌트로 제공
  2차 시도도 오답 -> 정답 + 어디서 틀렸을지 해설을 함께 제공

기존 twin_question.py(쌍둥이 문제 생성)와 동일한 Gemini 클라이언트를 공유한다.
"""
from __future__ import annotations

from pathlib import Path

from subjects.math.gemini_client import GeminiError, get_gemini_model

HintExplainError = GeminiError

HINT_PROMPT = """중1~중2 수준의 수학 문제야. 이 문제를 어떻게 풀어야 하는지
핵심이 되는 식이나 개념/공식만 짧게 알려줘.
최종 숫자 답은 절대로 말하지 말고, "이런 식을 세워서 풀어야 해" 정도로
구체적인 식의 형태(비례식, 방정식, 공식 등)를 포함해서 1~2문장으로만 힌트를 줘."""

EXPLANATION_PROMPT_TEMPLATE = """중1~중2 수준의 수학 문제야. 정답은 '{correct}'인데
학생이 두 번째 시도에서도 '{wrong}'이라고 답해서 틀렸어.
학생이 어느 단계에서 실수했을 가능성이 가장 높은지 한 문장으로 짚어주고,
이어서 올바른 풀이 과정을 순서대로 짧게 설명해줘."""


def generate_hint(image_path: Path) -> str:
    from PIL import Image

    model = get_gemini_model()
    image = Image.open(image_path)
    response = model.generate_content([HINT_PROMPT, image])
    return response.text.strip()


def generate_explanation(image_path: Path, wrong_answer: str, correct_answer: str) -> str:
    from PIL import Image

    model = get_gemini_model()
    image = Image.open(image_path)
    prompt = EXPLANATION_PROMPT_TEMPLATE.format(
        correct=correct_answer or "(정답 미상)",
        wrong=wrong_answer or "(무응답)",
    )
    response = model.generate_content([prompt, image])
    return response.text.strip()
