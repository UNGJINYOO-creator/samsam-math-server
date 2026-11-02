"""책 한 쪽의 개념을 바탕으로 AI가 '새 문제'를 만든다 (연습 모드, practice_app.py).

책 문제를 잘라서 그대로 보여주는 대신(잘림·OCR 깨짐·정답 불일치 문제가 생김), AI가 그 쪽을
한 번 보고 어떤 개념·유형인지 파악한 다음 숫자와 상황을 바꾼 새 문제를 정답·힌트·풀이와 함께 만든다.
문제와 정답을 같은 AI가 한 번에 만들기 때문에 서로 어긋나지 않는다.

AI가 만든 정답도 틀릴 수 있어서, 계산으로 확인할 수 있는 문제는 AI가 같이 적어준 계산식(calc)을
파이썬이 직접 계산해서 정답과 다르면 그 문제를 버린다.

AI 요청 횟수: 쪽을 처음 열 때 1번(페이지 이미지 + 글자 -> 개념 + 문제 여러 개),
"문제 더 만들기"마다 1번(글자만 보냄, 이미지 없음).
"""
from __future__ import annotations

import ast
import json
import operator
import re
from fractions import Fraction

from core.grading import UNIT_SUFFIX_PATTERN
from subjects.math.ai_solver import _JSON_FENCE_PATTERN, _loads_lenient, _plain_math
from subjects.math.gemini_client import GeminiError, generate

ProblemMakerError = GeminiError

CHOICE_MARKS = "①②③④⑤"

PROBLEM_SCHEMA = """{
  "concept": "위 concepts의 name 중 하나를 글자 그대로",
  "type": "multiple_choice 또는 short_answer",
  "question": "문제 문장. 수식은 $...$ 안에 LaTeX로 써줘 (예: $\\\\frac{7}{250}$, $2^2 \\\\times 5$, $0.\\\\dot{4}0\\\\dot{7}$). 객관식 보기는 여기 쓰지 말고 choices에",
  "choices": ["객관식이면 보기 5개 (원문자 빼고, 수식은 $...$ LaTeX). 주관식이면 빈 배열 []"],
  "answer": "정답. 객관식이면 ①~⑤ 중 하나만, 주관식이면 학생이 키보드로 칠 수 있는 형태 (예: 3, -2, 3/8, x=4, 0.25). LaTeX 금지",
  "calc": "정답 값을 계산하는 식. 숫자와 + - * / ** 괄호만 (예: 5/12 + 1/3, 2**3 * 5). 객관식이면 정답 보기의 값을 계산하는 식. 계산으로 확인할 수 없는 문제면 빈 문자열",
  "hint": "정답은 말하지 말고, 어떤 개념이나 식을 쓰면 되는지 1문장",
  "explanation": "1~3문장 풀이",
  "steps": ["풀이를 식 위주로 3~6줄. 학생이 직접 채워볼 핵심 값 2~3곳을 [[ ]]로 감싸줘. 예: \\"2x = [[8]]\\", \\"x = [[4]]\\""]
}"""

RULES = """규칙:
- 책 문제를 그대로 베끼지 말고, 숫자·상황을 바꾼 새 문제로. 같은 학년, 비슷한 난이도로.
- 정답이 딱 하나인 문제만. 그림·도형·그래프를 봐야 풀 수 있는 문제는 만들지 마.
- 주관식 정답은 키보드로 칠 수 있어야 해. 순환소수 점 표기처럼 칠 수 없는 답이 나오는 문제는 객관식으로 만들어.
- 객관식과 주관식을 섞어줘.
- hint, explanation, steps, answer, calc에는 LaTeX를 쓰지 말고 일반 글자로 (분수 1/2, 거듭제곱 x^2, 곱하기 ×, 루트 √2).
- question과 choices에서만 $...$ LaTeX를 써.
"""

FIRST_PROMPT = """이 이미지는 중학교 수학 문제집의 한 쪽이야.
1) 이 쪽이 다루는 단원 이름과, 핵심 개념/문제 유형을 2~5개 뽑아줘.
2) 그 개념들로 새 문제 {n}개를 만들어줘. 유형마다 골고루.

JSON 객체 하나로만 답해줘 (다른 설명 문구 없이):
{"unit": "단원 이름", "concepts": [{"name": "짧은 유형 이름", "summary": "이 유형이 무엇을 묻는지 한 문장"}], "problems": [문제, ...]}

문제 하나의 형식:
""" + PROBLEM_SCHEMA + "\n" + RULES

PAGE_TEXT_NOTE = """
참고로 이 쪽의 PDF 글자(OCR)를 붙여둘게. 한글 문장 확인용이고, 수식은 깨져 있을 수 있으니 수식은 이미지를 봐줘.
---
{text}
---
"""

MORE_PROMPT = """중학교 수학 문제집 "{unit}" 단원의 개념/문제 유형들이야:
{concepts}
{focus}
이 개념들로 새 문제 {n}개를 만들어줘. 아래 이미 낸 문제들과 숫자·형태가 겹치지 않게.
{avoid}
JSON 객체 하나로만 답해줘 (다른 설명 문구 없이): {"problems": [문제, ...]}

문제 하나의 형식:
""" + PROBLEM_SCHEMA + "\n" + RULES


# ───────────────────────── 정답 확인 (계산식) ─────────────────────────

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def safe_calc(expr: str) -> Fraction | None:
    """숫자와 사칙연산·거듭제곱만 있는 식을 분수로 정확히 계산한다. 그 밖의 것이 있으면 None.
    (AI가 준 글자를 eval하지 않고 직접 해석한다)"""
    expr = (expr or "").replace("×", "*").replace("÷", "/").replace("^", "**").replace("−", "-").strip()
    if not expr:
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def ev(node) -> Fraction:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return Fraction(str(node.value))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow):
                if right.denominator != 1 or abs(right) > 20:
                    raise ValueError("지수는 작은 정수만")
                return left ** int(right)
            return _OPS[type(node.op)](left, right)
        raise ValueError("허용하지 않는 식")

    try:
        return ev(tree)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def value_of(text: str) -> Fraction | None:
    """"3/8", "-2", "0.25", "x=4", "15cm", "$\\frac{3}{8}$" 같은 답/보기를 수로. 수가 아니면 None."""
    s = _plain_math(text or "").replace(" ", "")
    s = re.sub(r"^[a-zA-Z]=", "", s)
    s = UNIT_SUFFIX_PATTERN.sub("", s, count=1)
    if not re.fullmatch(r"[-+]?\d+(\.\d+)?(/\d+(\.\d+)?)?", s):
        return None
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


def check_problem(p: dict) -> str | None:
    """문제가 쓸 만하면 None, 아니면 버리는 이유."""
    if not p["question"]:
        return "문제 문장이 없음"
    if p["type"] == "multiple_choice":
        if not 4 <= len(p["choices"]) <= 5:
            return "보기 개수가 이상함"
        if p["answer"] not in CHOICE_MARKS[: len(p["choices"])]:
            return "객관식 정답이 보기 번호가 아님"
    elif not p["answer"]:
        return "정답이 없음"

    target = safe_calc(p["calc"])
    if target is None:
        return None  # 계산으로 확인할 수 없는 문제 -> AI를 믿는다
    if p["type"] == "multiple_choice":
        values = [value_of(c) for c in p["choices"]]
        if any(v is None for v in values):
            return None  # 보기가 수가 아님 -> 확인 불가
        matches = [i for i, v in enumerate(values) if v == target]
        if matches != [CHOICE_MARKS.index(p["answer"])]:
            return f"계산 결과({target})와 맞는 보기가 정답 하나뿐이 아님"
        return None
    got = value_of(p["answer"])
    if got is not None and got != target:
        return f"정답 {p['answer']}이 계산 결과 {target}와 다름"
    return None


# ───────────────────────── AI 응답 정리 ─────────────────────────


def _keep_latex(text) -> str:
    return str(text or "").strip()


def _normalize_problem(d: dict) -> dict:
    qtype = d.get("type") if d.get("type") in ("multiple_choice", "short_answer") else "short_answer"
    choices = [_keep_latex(c) for c in d.get("choices") or [] if str(c).strip()] if qtype == "multiple_choice" else []
    choices = [re.sub(r"^[①②③④⑤]\s*", "", c) for c in choices]
    answer = _plain_math(str(d.get("answer", "")))
    if qtype == "multiple_choice":
        marks = [c for c in answer if c in CHOICE_MARKS]
        if not marks and answer.strip().isdigit() and 1 <= int(answer.strip()) <= 5:
            marks = [CHOICE_MARKS[int(answer.strip()) - 1]]
        answer = marks[0] if len(marks) == 1 else answer
    return {
        "concept": str(d.get("concept", "")).strip(),
        "type": qtype,
        "question": _keep_latex(d.get("question")),
        "choices": choices,
        "answer": answer,
        "calc": str(d.get("calc", "") or "").strip(),
        "hint": _plain_math(str(d.get("hint", ""))),
        "explanation": _plain_math(str(d.get("explanation", ""))),
        "steps": [_plain_math(str(s)) for s in d.get("steps") or [] if str(s).strip()],
    }


def _parse(raw_text: str) -> dict:
    cleaned = _JSON_FENCE_PATTERN.sub("", raw_text.strip()).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise ProblemMakerError("AI가 문제를 정해진 형식으로 만들지 않았어요. 다시 시도해주세요.")
    try:
        data = _loads_lenient(cleaned[start: end + 1])
    except json.JSONDecodeError as e:
        raise ProblemMakerError(f"AI가 만든 문제를 읽지 못했어요 ({e}). 다시 시도해주세요.") from e
    if not isinstance(data, dict):
        raise ProblemMakerError("AI가 문제를 정해진 형식으로 만들지 않았어요. 다시 시도해주세요.")
    return data


def _clean_problems(raw: list, concept_names: list[str]) -> tuple[list[dict], list[str]]:
    kept, dropped = [], []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        p = _normalize_problem(item)
        if concept_names and p["concept"] not in concept_names:
            p["concept"] = next((c for c in concept_names if c in p["concept"] or p["concept"] in c), concept_names[0])
        why = check_problem(p)
        if why:
            dropped.append(why)
        else:
            kept.append(p)
    return kept, dropped


# ───────────────────────── 공개 함수 ─────────────────────────


def make_first_set(page_image, page_text: str, count: int = 6) -> dict:
    """쪽을 처음 열 때: 개념 파악 + 새 문제.
    반환: {"unit", "concepts": [{"name","summary"}], "problems": [...], "dropped": [버린 이유...], "solver"}"""
    prompt = FIRST_PROMPT.replace("{n}", str(count))
    if page_text.strip():
        prompt += PAGE_TEXT_NOTE.replace("{text}", page_text.strip()[:4000])
    response = generate([prompt, page_image])
    data = _parse(response.text)
    concepts = [
        {"name": str(c.get("name", "")).strip(), "summary": _plain_math(str(c.get("summary", "")))}
        for c in data.get("concepts") or [] if isinstance(c, dict) and str(c.get("name", "")).strip()
    ]
    problems, dropped = _clean_problems(data.get("problems"), [c["name"] for c in concepts])
    if not problems:
        raise ProblemMakerError("AI가 만든 문제가 모두 검사에서 걸러졌어요. 다시 시도해주세요.")
    return {
        "unit": str(data.get("unit", "")).strip() or "이 쪽",
        "concepts": concepts or [{"name": "이 쪽 개념", "summary": ""}],
        "problems": problems,
        "dropped": dropped,
        "solver": response.provider,
    }


def make_more(unit: str, concepts: list[dict], avoid: list[str], count: int = 6, focus: str | None = None) -> dict:
    """이미 개념을 아는 쪽에서 문제 더 만들기 (이미지 없이 글자만 보냄).
    focus를 주면 그 유형으로만. 반환: {"problems", "dropped", "solver"}"""
    concept_lines = "\n".join(f"- {c['name']}: {c.get('summary', '')}" for c in concepts)
    focus_line = f'이번에는 "{focus}" 유형으로만 만들어줘.' if focus else ""
    avoid_lines = "\n".join(f"- {q[:120]}" for q in avoid[-12:]) or "(아직 없음)"
    prompt = (
        MORE_PROMPT.replace("{unit}", unit).replace("{concepts}", concept_lines).replace("{focus}", focus_line)
        .replace("{n}", str(count)).replace("{avoid}", "이미 낸 문제:\n" + avoid_lines)
    )
    response = generate([prompt])
    data = _parse(response.text)
    names = [c["name"] for c in concepts]
    problems, dropped = _clean_problems(data.get("problems"), names)
    if focus:
        for p in problems:
            p["concept"] = focus
    if not problems:
        raise ProblemMakerError("AI가 만든 문제가 모두 검사에서 걸러졌어요. 다시 시도해주세요.")
    return {"problems": problems, "dropped": dropped, "solver": response.provider}
