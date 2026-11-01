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

from subjects.math.gemini_client import GeminiError, QuotaExceededError, generate, use_nvidia_now
from subjects.math.nvidia_client import nvidia_available

AISolveError = GeminiError

ITEM_SCHEMA = """{
  "answer": "정답. 객관식이면 원문자(①~⑤) 중 하나, 단답형이면 값만 간결하게(예: x=4, 15cm, 1/2), 풀이과정을 써야 하는 서술형이면 \"해설참조\"",
  "type": "multiple_choice 또는 short_answer 또는 essay 중 하나",
  "choices": ["객관식이면 보기 내용을 순서대로(원문자 빼고), 객관식이 아니면 빈 배열 []"],
  "hint": "정답은 절대 말하지 말고, 어떤 식이나 개념을 쓰면 되는지 1문장 힌트",
  "explanation": "1~3문장으로 간단한 풀이 과정",
  "steps": ["풀이를 식 위주로 3~6줄. 학생이 직접 채워볼 핵심 값 2~3곳을 [[ ]]로 감싸줘. 예: \"2x = [[8]]\", \"x = [[4]]\""]
}"""

RULES = """steps의 [[ ]] 안에는 짧은 값(숫자, 식, 단어)만 넣고, 첫 줄(문제의 식을 옮겨 적는 줄)에는 빈칸을 넣지 마.

중요: LaTeX(\\frac, \\times, $ 등)를 절대 쓰지 마. 학생이 키보드로 입력하는 것처럼
일반 글자로만 써줘. 예: 분수는 1/2, 거듭제곱은 x^2, 곱하기는 ×, 루트는 √2, 원주율은 π.
"""

# 무료 요금제는 "요청 횟수"로 하루 한도가 정해지므로, 한 페이지의 문제들을 이미지 여러 장으로
# 한 번에 보내 요청 1번으로 푼다 (문제마다 1번씩 부르면 하루 20문제 정도에서 한도가 찬다).
BATCH_PROMPT = """아래 이미지 {n}장은 중학교 수학 문제집의 문제를 1개씩 잘라낸 거야 (1번째 이미지 = 1번째 문제).
각 문제를 직접 풀어서, 이미지 순서 그대로 {n}개짜리 JSON 배열 하나로만 답해줘 (다른 설명 문구 없이).
배열의 각 원소 형식:
""" + ITEM_SCHEMA + "\n" + RULES

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


def _normalize_item(data: dict) -> dict:
    return {
        "answer": _plain_math(str(data.get("answer", ""))),
        "type": data.get("type") if data.get("type") in ("multiple_choice", "short_answer", "essay") else "short_answer",
        "choices": [_plain_math(str(c)) for c in data.get("choices") or [] if str(c).strip()],
        "hint": _plain_math(str(data.get("hint", ""))),
        "explanation": _plain_math(str(data.get("explanation", ""))),
        "steps": [_plain_math(str(s)) for s in data.get("steps") or [] if str(s).strip()],
    }


def _parse_json_response(raw_text: str) -> dict:
    """문제 1개짜리 응답 (객체 하나, 또는 원소 1개짜리 배열)."""
    return _parse_batch_response(raw_text, 1)[0]


def _parse_batch_response(raw_text: str, n: int) -> list[dict]:
    cleaned = _JSON_FENCE_PATTERN.sub("", raw_text.strip()).strip()
    data = _loads_lenient(cleaned)  # 그래도 안 되면 JSONDecodeError를 올려서 호출부가 처리
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or len(data) != n:
        raise AISolveError(f"AI가 문제 {n}개에 대한 답을 제대로 돌려주지 않았어요 (받은 개수: {len(data) if isinstance(data, list) else 0})")
    return [_normalize_item(item if isinstance(item, dict) else {}) for item in data]


def _solve_batch(images: list, provider: str | None) -> list[dict]:
    response = generate([BATCH_PROMPT.replace("{n}", str(len(images))), *images], provider=provider)
    return [{**item, "solver": response.provider} for item in _parse_batch_response(response.text, len(images))]


def _solve_one_by_one(images: list, provider: str | None) -> list[dict]:
    return [_solve_batch([img], provider)[0] for img in images]


def solve_questions(image_paths: list[Path], provider: str | None = None) -> list[dict]:
    """여러 문제를 푼다. 반환 순서 = image_paths 순서.

    Gemini는 요청 1번에 여러 문제를 같이 보낸다(무료 한도가 요청 횟수 기준이라).
    보조 AI(NVIDIA)를 써야 할 때는 이미지를 한 장씩 보낸다(여러 장을 못 받는 모델이 많아서).

    각 원소: {"answer", "type", "choices", "hint", "explanation", "steps", "solver"}
    steps는 빈칸 채우기용 풀이 줄 목록이고, 빈칸 자리는 [[값]]으로 표시되어 있다.
    solver는 답을 낸 AI ("gemini" | "nvidia").
    """
    from PIL import Image

    images = [Image.open(p) for p in image_paths]
    if provider == "nvidia" or (provider is None and len(images) > 1 and use_nvidia_now()):
        return _solve_one_by_one(images, provider)
    try:
        return _solve_batch(images, provider)
    except QuotaExceededError:
        # Gemini가 방금 한도에 걸림 -> NVIDIA 키가 있으면 한 장씩 보조 AI로
        if provider is None and len(images) > 1 and nvidia_available():
            return _solve_one_by_one(images, provider)
        raise


def solve_question(image_path: Path) -> dict:
    """문제 1개 풀기 (solve_questions의 1개짜리 버전)."""
    return solve_questions([image_path])[0]
