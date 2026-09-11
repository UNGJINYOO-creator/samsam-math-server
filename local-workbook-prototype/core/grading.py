"""정답 정규화 및 채점 로직.

단순 문자열 완전일치가 아니라 다음과 같은 표기 차이를 흡수한다:
  - 공백 차이:            "x=4, y=-2"      == "x = 4,  y = -2"
  - 원문자 <-> 숫자:        "③"              == "3"   (객관식)
  - 위첨자 표기:            "12cm²"          == "12cm^2" == "12 cm^2"
  - 변수=값 쌍의 순서:      "x=4, y=-2"      == "y=-2, x=4"
  - pi <-> π 표기:         "9pi"            == "9π"
  - 단위 생략:              "65°"            == "65"   ("cm", "개", "도" 등 흔한 단위는
                                                        숫자만 입력해도 정답 처리)

서술형/풀이과정 문항은 자동채점 대상이 아니므로 app.py에서 type == "essay"
(또는 subjects/math에서 자동채점 불가로 태깅된 유형)일 때 이 모듈을 호출하지
않고 수동 O/X 체크로 처리한다.
"""
from __future__ import annotations

import re

CIRCLED_TO_DIGIT = {"①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5"}
DIGIT_TO_CIRCLED = {v: k for k, v in CIRCLED_TO_DIGIT.items()}

SUPERSCRIPT_MAP = {"²": "^2", "³": "^3", "⁴": "^4"}

VAR_VALUE_PATTERN = re.compile(r"^([a-zA-Z])=(-?\d+(?:\.\d+)?)$")

# 정답 끝에 붙는 흔한 단위/표기. 학생이 숫자만 입력해도 정답으로 인정하기 위해
# 채점 시 양쪽 문자열에서 이 접미사를 떼어내고 한 번 더 비교한다.
UNIT_SUFFIX_PATTERN = re.compile(
    r"(cm\^?2|cm\^?3|cm|mm|km|kg|도|개|명|살|점|원|초|분|시간|g|m)$"
)


def _normalize_superscripts(s: str) -> str:
    for sup, repl in SUPERSCRIPT_MAP.items():
        s = s.replace(sup, repl)
    return s


def normalize_short_answer(s: str) -> str:
    """공백 제거 + 위첨자 통일 + pi/π 통일 + 소문자화 + °<->도 통일."""
    s = s.strip()
    s = _normalize_superscripts(s)
    s = re.sub(r"\s+", "", s)
    s = s.lower()
    s = re.sub(r"pi", "π", s)
    s = s.replace("°", "도")
    return s


def _strip_unit_suffix(s: str) -> str:
    """문자열 끝에서 흔한 단위 접미사를 한 번만 제거."""
    return UNIT_SUFFIX_PATTERN.sub("", s, count=1)


def normalize_multiple_choice(s: str) -> str:
    """원문자/숫자를 모두 "숫자 하나" 형태로 통일."""
    s = s.strip()
    if s in CIRCLED_TO_DIGIT:
        return CIRCLED_TO_DIGIT[s]
    return s


def _parse_var_value_pairs(normalized: str) -> set | None:
    """"x=4,y=-2" 형태를 {(var,value), ...} 집합으로 변환. 매칭 실패 시 None."""
    parts = normalized.split(",")
    pairs = set()
    for part in parts:
        m = VAR_VALUE_PATTERN.match(part)
        if not m:
            return None
        pairs.add((m.group(1), m.group(2)))
    return pairs


def grade_short_answer(correct: str, submitted: str) -> bool:
    norm_correct = normalize_short_answer(correct)
    norm_submitted = normalize_short_answer(submitted)
    if norm_correct == norm_submitted:
        return True

    pairs_correct = _parse_var_value_pairs(norm_correct)
    pairs_submitted = _parse_var_value_pairs(norm_submitted)
    if pairs_correct is not None and pairs_submitted is not None:
        return pairs_correct == pairs_submitted

    # 단위 생략 허용: "65도"(정답) vs "65"(제출) 처럼 숫자/기호 값은 같고
    # 단위만 빠진 경우도 정답으로 인정한다. 값 자체가 다르면(예: "9π" vs "9") 여기서도 불일치.
    if _strip_unit_suffix(norm_correct) == _strip_unit_suffix(norm_submitted):
        return True

    return False


def grade_multiple_choice(correct: str, submitted: str) -> bool:
    return normalize_multiple_choice(correct) == normalize_multiple_choice(submitted)


def grade_answer(question_type: str, correct: str, submitted: str) -> bool:
    """question_type: "multiple_choice" | "short_answer" (그 외 유형은 자동채점 대상 아님)."""
    if not submitted or not submitted.strip():
        return False
    if question_type == "multiple_choice":
        return grade_multiple_choice(correct, submitted)
    return grade_short_answer(correct, submitted)
