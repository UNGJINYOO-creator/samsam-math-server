"""AI(Gemini)가 문제 이미지를 직접 보고 정답을 생성 (출판사 정답표 없이 대체 경로).

answer_parser.py는 출판사 "정답표" 페이지의 텍스트를 정규식으로 파싱하는 방식이라,
다단 표/괄호 설명이 섞인 복잡한 레이아웃에서 깨지기 쉽다. 이 모듈은 반대 접근이다:
정답표를 아예 보지 않고, 문제 이미지 자체를 Gemini에게 보여줘서 "직접 풀어서
답하라"고 요청한다. 중학교 수준 문제는 Gemini가 충분히 안정적으로 풀 수 있고,
정답표 레이아웃 문제에 전혀 영향을 받지 않는다는 장점이 있다.

주의: 100% 정확하지 않을 수 있으므로(특히 도형/그래프처럼 그림 해석이 까다로운
문제), 여전히 1분 눈 검수에서 확인하는 걸 권장한다. 다만 "레이아웃이 깨져서
전부 다시 쳐야 하는" 상황보다는 훨씬 적은 수정만 필요할 가능성이 높다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from subjects.math.gemini_client import GeminiError, get_gemini_model

AISolveError = GeminiError

SOLVE_PROMPT = """이 이미지는 중학교 수학 문제집의 문제 1개야. 이 문제를 직접 풀어서
정답을 구해줘.

반드시 아래 JSON 형식으로만 답해줘 (다른 설명 문구 없이 JSON 하나만):
{
  "answer": "정답. 객관식이면 원문자(①~⑤) 중 하나, 단답형이면 값만 간결하게(예: x=4, 15cm, 1/2), 풀이과정을 써야 하는 서술형이면 \"해설참조\"",
  "type": "multiple_choice 또는 short_answer 또는 essay 중 하나",
  "explanation": "1~3문장으로 간단한 풀이 과정"
}

중요: LaTeX(\\frac, \\times, $ 등)를 절대 쓰지 마. 학생이 키보드로 입력하는 것처럼
일반 글자로만 써줘. 예: 분수는 1/2, 거듭제곱은 x^2, 곱하기는 ×, 루트는 √2, 원주율은 π.
"""

_JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# AI가 JSON 문자열 안에 LaTeX(\frac, \times, \sqrt ...)를 그대로 넣으면 두 가지 문제가 생긴다.
#   - "\s", "\{" 처럼 JSON에 없는 이스케이프 -> 파싱 오류 ("Invalid \escape")
#   - "\frac", "\times" -> \f, \t 가 정상 이스케이프로 읽혀서 오류 없이 글자가 깨짐
# 그래서 파싱 전에 항상, "\ + 영문자 2개 이상"(LaTeX 명령)이거나 JSON에 없는 이스케이프인
# 역슬래시를 글자 그대로의 "\"로 바꾼다. 이미 "\\"로 올바르게 쓴 쌍은 먼저 통째로 건너뛴다.
_BACKSLASH_PATTERN = re.compile(r'\\\\|\\(?=[A-Za-z]{2,}|[^"\\/bfnrtu])')


def _loads_lenient(text: str) -> dict:
    return json.loads(_BACKSLASH_PATTERN.sub(lambda m: m.group(0) if m.group(0) == "\\\\" else "\\\\", text))


_LATEX_FRAC = re.compile(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}")
_LATEX_SQRT = re.compile(r"\\sqrt\{([^{}]*)\}")
_LATEX_SYMBOLS = {
    r"\times": "×", r"\cdot": "×", r"\div": "÷", r"\pi": "π", r"\le": "≤", r"\ge": "≥",
    r"\leq": "≤", r"\geq": "≥", r"\neq": "≠", r"\pm": "±", r"\circ": "°", r"\degree": "°",
    r"\left": "", r"\right": "",
}


def _plain_math(text: str) -> str:
    """지시를 어기고 LaTeX로 답했을 때를 대비해, 정답을 학생이 입력하는 형태로 바꾼다.
    (정답이 "\\frac{1}{2}"로 저장되면 학생이 "1/2"를 써도 오답 처리되기 때문)"""
    text = text.replace("$", "")
    if "\\" not in text:
        return text.strip()  # LaTeX가 없으면 중괄호도 그대로 둔다 (집합 {1, 2, 3} 같은 정답 보호)
    text = _LATEX_FRAC.sub(r"\1/\2", text)
    text = _LATEX_SQRT.sub(r"√\1", text)
    for latex, plain in sorted(_LATEX_SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(latex, plain)
    text = re.sub(r"\^\{([^{}]*)\}", r"^\1", text)
    return text.replace("{", "").replace("}", "").strip()


def _parse_json_response(raw_text: str) -> dict:
    cleaned = _JSON_FENCE_PATTERN.sub("", raw_text.strip()).strip()
    data = _loads_lenient(cleaned)  # 그래도 안 되면 JSONDecodeError를 올려서 호출부가 처리
    return {
        "answer": _plain_math(str(data.get("answer", ""))),
        "type": data.get("type") if data.get("type") in ("multiple_choice", "short_answer", "essay") else "short_answer",
        "explanation": _plain_math(str(data.get("explanation", ""))),
    }


def solve_question(image_path: Path) -> dict:
    """image_path: 문제 크롭 이미지(q_XX.png) 경로.

    반환: {"answer": str, "type": str, "explanation": str}
    """
    from PIL import Image

    model = get_gemini_model()
    image = Image.open(image_path)
    response = model.generate_content([SOLVE_PROMPT, image])
    return _parse_json_response(response.text)
