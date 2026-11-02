"""PDF 글자(OCR)에서 AI 없이 알 수 있는 것들: 객관식인지, 정답을 몇 개 골라야 하는지.

글자는 수식이 깨져 있을 수 있어서(분수는 위아래 줄로 쪼개지고, 2²는 "22"가 되고,
순환소수 위의 점은 사라진다) 정답을 구하는 데는 쓰지 않는다. 대신 "문제의 모양"은
글자로 정확하고 빠르게 알 수 있으니, 그건 여기서 정하고 AI에게도 참고로 같이 보낸다.
"""
from __future__ import annotations

import re

CIRCLED = "①②③④⑤"
_PICKS_PATTERN = re.compile(r"정답\s*(\d)\s*개")
_PICK_ALL_PATTERN = re.compile(r"모두\s*(?:고르|찾)")


def is_multiple_choice(text: str) -> bool:
    return any(c in text for c in CIRCLED)


def pick_count(text: str) -> int | None:
    """골라야 할 정답 개수. "(정답 2개)"면 2, 개수 없이 "모두 고르면"이면 None(여러 개, 개수 모름), 보통은 1."""
    m = _PICKS_PATTERN.search(text)
    if m:
        return max(1, int(m.group(1)))
    if _PICK_ALL_PATTERN.search(text):
        return None
    return 1


def answer_count(answer: str) -> int:
    """AI 정답 "③, ④" 안의 보기 개수."""
    return sum(1 for c in answer if c in CIRCLED)
