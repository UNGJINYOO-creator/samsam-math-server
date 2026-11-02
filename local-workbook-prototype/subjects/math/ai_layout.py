"""AI(Gemini)가 페이지 이미지를 보고 문제 영역을 직접 찾는다 (페이지 단위 즉석 모드의 대체 경로).

텍스트 레이어가 없는 스캔본이거나, 문제 번호 형식이 "1." "1)"이 아니라서 글자 기반
탐지가 아무것도 못 찾았을 때 쓴다. 사용자가 "AI로 다시 찾기"를 눌렀을 때도 쓴다.

Gemini는 영역을 [ymin, xmin, ymax, xmax] (0~1000 정규화) 형식으로 돌려주는 데
익숙하므로 그 형식으로 받아서 PDF 포인트 좌표로 바꾼다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.page_questions import PageQuestion
from subjects.math.gemini_client import GeminiError, generate

AILayoutError = GeminiError

LAYOUT_PROMPT = """이 이미지는 수학 문제집의 한 페이지야.
학생이 풀어야 하는 "문제"들만 찾아서, 각 문제의 영역(문항 번호부터 보기/그림/답 쓰는 칸 끝까지)을 알려줘.
개념 설명, 예제와 그 풀이, 머리말, 쪽 번호, 광고는 문제가 아니니 빼줘.
읽는 순서(2단이면 왼쪽 칸 위->아래, 그다음 오른쪽 칸 위->아래)대로 나열해줘.

반드시 아래 형식의 JSON 배열 하나만 답해줘 (설명 문구 없이):
[{"no": 문항번호(정수, 번호가 안 보이면 0), "box_2d": [ymin, xmin, ymax, xmax]}]
좌표는 이미지 기준 0~1000으로 정규화한 정수야. 문제가 하나도 없으면 [] 만 답해줘.
"""

_JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_layout_response(raw_text: str, page_width_pt: float, page_height_pt: float) -> list[PageQuestion]:
    cleaned = _JSON_FENCE_PATTERN.sub("", raw_text.strip()).strip()
    items = json.loads(cleaned)
    questions: list[PageQuestion] = []
    for item in items:
        box = item.get("box_2d")
        if not isinstance(box, list) or len(box) != 4:
            continue
        ymin, xmin, ymax, xmax = (max(0, min(1000, float(v))) for v in box)
        if ymax <= ymin or xmax <= xmin:
            continue
        questions.append(
            PageQuestion(
                no=int(item.get("no") or 0),
                bbox=(
                    xmin / 1000 * page_width_pt,
                    ymin / 1000 * page_height_pt,
                    xmax / 1000 * page_width_pt,
                    ymax / 1000 * page_height_pt,
                ),
            )
        )
    return questions


def detect_questions_by_ai(page_png: Path, page_width_pt: float, page_height_pt: float) -> list[PageQuestion]:
    from PIL import Image

    response = generate([LAYOUT_PROMPT, Image.open(page_png)])
    return parse_layout_response(response.text, page_width_pt, page_height_pt)
