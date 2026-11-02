"""한 페이지 안에서만 문제를 찾아 자르는 모듈 (페이지 단위 즉석 모드용).

책 전체를 한 번에 나누는 question_split.rule_based_split은 "1번부터 끝까지 번호가
이어진다"고 가정하기 때문에, 앞쪽 설명 페이지나 번호가 한 번 어긋나는 곳이 있으면
그 뒤 전체가 밀린다. 여기서는 열어본 페이지 하나만 보고 판단하므로 그런 오류가
다른 페이지로 번지지 않는다.

2단(좌/우 칸) 레이아웃도 처리한다: 페이지 가운데보다 오른쪽에서 시작하는 문제
번호가 있으면 2단으로 보고, 각 문제를 자기 칸 너비만큼만 자른다.

좌표는 모두 PDF 포인트(72dpi) 기준 (x0, y0, x1, y1).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz
from PIL import Image

from config import RENDER_DPI
import re

from core.question_split import QUESTION_START_PATTERN

TOP_PADDING_PT = 4.0  # 번호 글자 윗부분이 잘리지 않도록 살짝 위에서부터 자름
RIGHT_COLUMN_RATIO = 0.95  # 가운데선의 95% 지점보다 오른쪽에서 시작하면 오른쪽 칸으로 본다

# 마침표 없는 두 자리 번호: "01 다음 중 옳은 것은?", "12 두 분수..." (리피트 개념 같은 책)
# 이런 모양은 분수의 분모("27을 소수로")나 표 안 숫자와 헷갈리기 쉬워서, 칸의 왼쪽 끝에서
# 시작하는 줄만 문제 번호로 인정한다. 번호만 따로 한 줄로 잡히는 PDF도 있어서 뒤 글자는 없어도 된다.
LOOSE_NUMBER_PATTERN = re.compile(r"^\s*(\d{2})(?:\s*$|\s*(?=[가-힣(]))")
LEFT_EDGE_TOLERANCE_PT = 12.0


@dataclass
class PageQuestion:
    no: int
    bbox: tuple[float, float, float, float]


def _lines(page: fitz.Page) -> list[tuple[str, float, float]]:
    """페이지의 모든 글자 줄 (글자, x0, y0)."""
    out: list[tuple[str, float, float]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            if text.strip():
                out.append((text, line["bbox"][0], line["bbox"][1]))
    return out


def _number_candidates(lines: list[tuple[str, float, float]]) -> list[tuple[int, float, float, bool]]:
    """줄 맨 앞이 "1." "2)" 또는 "01 " 같은 번호인 줄들의 (번호, x0, y0, 마침표없는번호인지)."""
    out: list[tuple[int, float, float, bool]] = []
    for text, x0, y0 in lines:
        m = QUESTION_START_PATTERN.match(text)
        if m:
            out.append((int(m.group(1)), x0, y0, False))
            continue
        m = LOOSE_NUMBER_PATTERN.match(text)
        if m and int(m.group(1)) >= 1:
            out.append((int(m.group(1)), x0, y0, True))
    return out


def _longest_consecutive_chain(ordered: list[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """읽는 순서로 정렬된 (번호, 칸, y0) 중에서 번호가 1씩 증가하는 가장 긴 연쇄를 고른다.

    본문 속 "3. 개념 정리" 같은 우연한 번호 하나 때문에 진짜 문제 번호들이
    버려지지 않도록, 시작점을 하나로 고정하지 않고 가장 긴 연쇄를 택한다.
    """
    best: list[tuple[int, int, float]] = []
    for i in range(len(ordered)):
        chain = [ordered[i]]
        for cand in ordered[i + 1 :]:
            if cand[0] == chain[-1][0] + 1:
                chain.append(cand)
        if len(chain) > len(best):
            best = chain
    return best


def detect_questions_by_text(pdf_path: str, page_no: int) -> list[PageQuestion]:
    """page_no는 1-based. 텍스트 레이어가 없거나 번호를 못 찾으면 빈 리스트."""
    doc = fitz.open(pdf_path)
    page = doc[page_no - 1]
    width, height = page.rect.width, page.rect.height
    lines = _lines(page)
    candidates = _number_candidates(lines)
    doc.close()

    if not candidates:
        return []

    mid = width / 2

    def col_of(x0: float) -> int:
        return 1 if x0 >= mid * RIGHT_COLUMN_RATIO else 0

    # 마침표 없는 번호는 같은 칸의 번호 후보 중 가장 왼쪽 줄에 맞춰 있을 때만 인정한다.
    # 진짜 문제 번호는 칸 왼쪽에 세로로 나란하고, 분모("27을")나 표 숫자는 그보다 오른쪽에 있다.
    left_edge: dict[int, float] = {}
    for _no, x0, _y0, _loose in candidates:
        c = col_of(x0)
        left_edge[c] = min(left_edge.get(c, x0), x0)
    candidates = [
        (no, x0, y0) for no, x0, y0, loose in candidates
        if not loose or x0 <= left_edge[col_of(x0)] + LEFT_EDGE_TOLERANCE_PT
    ]
    if not candidates:
        return []

    cols = [col_of(x0) for _, x0, _ in candidates]
    two_columns = 0 in cols and 1 in cols
    if not two_columns:
        cols = [0] * len(candidates)

    ordered = sorted(
        ((no, col, y0) for (no, _x0, y0), col in zip(candidates, cols)),
        key=lambda t: (t[1], t[2]),
    )
    chain = _longest_consecutive_chain(ordered)

    def column_x_range(col: int) -> tuple[float, float]:
        if not two_columns:
            return 0.0, width
        return (0.0, mid) if col == 0 else (mid, width)

    questions: list[PageQuestion] = []
    for i, (no, col, y0) in enumerate(chain):
        nxt = chain[i + 1] if i + 1 < len(chain) else None
        y1 = nxt[2] if nxt and nxt[1] == col else height
        x0, x1 = column_x_range(col)
        questions.append(PageQuestion(no=no, bbox=(x0, max(0.0, y0 - TOP_PADDING_PT), x1, y1)))
    return questions


def render_page(pdf_path: str, page_no: int, out_path: Path, dpi: int = RENDER_DPI) -> Path:
    if out_path.exists():
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    tmp = out_path.with_name(f"tmp_{out_path.name}")
    doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(str(tmp))
    doc.close()
    tmp.replace(out_path)
    return out_path


def crop_questions(
    page_png: Path, questions: list[PageQuestion], out_dir: Path, page_no: int, dpi: int = RENDER_DPI
) -> list[tuple[PageQuestion, Path]]:
    """잘라낸 문제와 이미지 경로 쌍. 크기가 0인(잘못 잡힌) 영역은 빠진다.
    파일명은 p014_q01.png, p014_q02.png ... (페이지 안 순서)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72.0
    img = Image.open(page_png)
    result: list[tuple[PageQuestion, Path]] = []
    for q in questions:
        x0, y0, x1, y1 = (int(v * scale) for v in q.bbox)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(img.width, x1), min(img.height, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        out = out_dir / f"p{page_no:03d}_q{len(result) + 1:02d}.png"
        img.crop((x0, y0, x1, y1)).save(out)
        result.append((q, out))
    return result
