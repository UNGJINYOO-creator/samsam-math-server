"""풀이식 빈칸 채우기.

AI가 만든 풀이 줄에서 [[값]]으로 표시된 곳을 빈칸으로 바꾼다.
화면에서는 줄 안에 ㉠ ㉡ ㉢ 표시를 남기고, 입력칸은 그 아래에 따로 둔다
(Streamlit은 글자 사이에 입력칸을 끼워 넣기 어렵기 때문).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BLANK_PATTERN = re.compile(r"\[\[(.+?)\]\]")
BLANK_MARKS = "㉠㉡㉢㉣㉤㉥㉦㉧"


@dataclass
class Cloze:
    lines: list[str]  # 빈칸 자리가 "( ㉠ )" 로 바뀐 풀이 줄
    answers: list[str]  # ㉠, ㉡ ... 순서의 정답
    solution: list[str]  # 빈칸 없이 다 채운 풀이 줄


def build_cloze(steps: list[str], max_blanks: int = len(BLANK_MARKS)) -> Cloze:
    answers: list[str] = []
    lines: list[str] = []

    def to_blank(m: re.Match) -> str:
        if len(answers) >= max_blanks:
            return m.group(1)  # 빈칸이 너무 많으면 나머지는 그냥 값을 보여준다
        answers.append(m.group(1).strip())
        return f"( {BLANK_MARKS[len(answers) - 1]} )"

    for step in steps:
        lines.append(BLANK_PATTERN.sub(to_blank, step))
    solution = [BLANK_PATTERN.sub(lambda m: m.group(1), s) for s in steps]
    return Cloze(lines=lines, answers=answers, solution=solution)
