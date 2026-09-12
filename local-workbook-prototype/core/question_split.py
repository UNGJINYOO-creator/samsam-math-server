"""문제 경계 자동 분할 + 수동 보정 데이터 모델.

설계 원칙:
  - "페이지 크기/여백이 일정하다"는 가정으로 좌표를 하드코딩하지 않는다.
  - 텍스트 레이어가 있는 페이지는 "줄 시작이 문항 번호 패턴 + 이전 번호 다음 값"인
    라인을 문제 시작점으로 탐지한다 (rule_based_split).
  - 텍스트 레이어가 없는 페이지, 혹은 자동 탐지가 실패한 문제는 boundaries.json에
    "미확정" 상태로 남기고, 사람이 correct_app.py에서 클릭 2번(문제 시작/끝)으로
    직접 지정하도록 한다. 즉 "자동 분할이 실패할 수 있다"를 정상 경로로 취급한다.

boundaries.json 스키마:
{
  "1": {"parts": [{"page": 1, "y0": 120.3, "y1": 480.0}], "auto": true},
  "2": {"parts": [{"page": 1, "y0": 480.0, "y1": 760.0},
                   {"page": 2, "y0": 0.0,   "y1": 140.0}], "auto": true},
  ...
}
  - "parts"는 페이지를 넘어가는 문제(다음 페이지로 이어짐)를 표현하기 위해 리스트로 둔다.
  - "auto": true 는 규칙 기반으로 자동 탐지된 경계, false는 사람이 수동으로 확정한 경계.
  - y0/y1은 PDF 포인트 좌표(72dpi 기준, PyMuPDF 좌표계) 기준값. 이미지 크롭 시
    render DPI에 맞춰 픽셀 좌표로 환산한다 (scale = dpi / 72).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import pymupdf as fitz
from PIL import Image

from config import RENDER_DPI

# 문항 시작으로 인식할 줄 패턴: "1.", "1)", "01." 등 줄 맨 앞의 번호
QUESTION_START_PATTERN = re.compile(r"^\s*0?(\d{1,3})[.\)]\s")


def _line_starts(page: fitz.Page) -> list[tuple[int, float, str]]:
    """페이지 내 각 텍스트 라인의 (번호, y0, 원문) 목록을 y0 기준 정렬해서 반환."""
    raw = page.get_text("dict")
    candidates: list[tuple[int, float, str]] = []
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            m = QUESTION_START_PATTERN.match(text)
            if not m:
                continue
            y0 = line["bbox"][1]
            candidates.append((int(m.group(1)), y0, text.strip()))
    candidates.sort(key=lambda t: t[1])
    return candidates


def rule_based_split(pdf_path: str, max_question_no: int = 200) -> dict:
    """텍스트 레이어를 이용해 문항 시작점을 탐지하고 경계(boundaries)를 구성한다.

    "이전에 발견한 번호 + 1"과 일치하는 경우만 채택하여 본문 속 우연한 숫자
    (예: "3cm", "2배") 오탐을 크게 줄인다. 첫 문항은 1로 시작한다고 가정한다.
    """
    doc = fitz.open(pdf_path)
    expected = 1
    starts: list[tuple[int, int, float]] = []  # (question_no, page_no(0-based), y0)

    for page_index, page in enumerate(doc):
        for no, y0, _text in _line_starts(page):
            if no == expected and expected <= max_question_no:
                starts.append((no, page_index, y0))
                expected += 1

    page_heights = [p.rect.height for p in doc]
    doc.close()

    boundaries: dict = {}
    for i, (no, page_idx, y0) in enumerate(starts):
        parts = []
        if i + 1 < len(starts):
            next_no, next_page_idx, next_y0 = starts[i + 1]
        else:
            next_no, next_page_idx, next_y0 = None, page_idx, page_heights[page_idx]

        if next_page_idx == page_idx:
            parts.append({"page": page_idx + 1, "y0": y0, "y1": next_y0})
        else:
            # 다음 문제가 다른 페이지에서 시작 -> 현재 페이지 끝까지 + 중간 페이지 전체 + 다음 페이지 시작 전까지
            parts.append({"page": page_idx + 1, "y0": y0, "y1": page_heights[page_idx]})
            for mid in range(page_idx + 1, next_page_idx):
                parts.append({"page": mid + 1, "y0": 0.0, "y1": page_heights[mid]})
            parts.append({"page": next_page_idx + 1, "y0": 0.0, "y1": next_y0})

        boundaries[str(no)] = {"parts": parts, "auto": True}

    return boundaries


def fallback_whole_page_split(pdf_path: str, image_only_pages: list[int]) -> dict:
    """텍스트 레이어가 없는(이미지 전용) 페이지는 자동 분할이 불가능하므로,
    페이지 전체를 "미확정 문제 1개"로 등록해 사람이 correct_app.py에서
    직접 경계를 나누도록 한다. key는 임시로 'page{N}_unsplit' 형태."""
    doc = fitz.open(pdf_path)
    boundaries: dict = {}
    for page_idx in image_only_pages:
        height = doc[page_idx].rect.height
        boundaries[f"page{page_idx + 1}_unsplit"] = {
            "parts": [{"page": page_idx + 1, "y0": 0.0, "y1": height}],
            "auto": False,
        }
    doc.close()
    return boundaries


def load_boundaries(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_boundaries(path: Path, boundaries: dict) -> None:
    path.write_text(json.dumps(boundaries, ensure_ascii=False, indent=2), encoding="utf-8")


def export_question_crops(
    boundaries: dict,
    pages_dir: Path,
    out_dir: Path,
    dpi: int = RENDER_DPI,
) -> list[str]:
    """boundaries.json 내용을 바탕으로 pages/*.png에서 문제별 이미지를 잘라
    questions/q_XX.png 로 저장한다. 여러 파트로 나뉜 문제(페이지 넘어감)는
    세로로 이어붙인다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72.0
    saved: list[str] = []

    def sort_key(qno: str):
        m = re.match(r"^\d+$", qno)
        return (0, int(qno)) if m else (1, qno)

    for qno in sorted(boundaries.keys(), key=sort_key):
        entry = boundaries[qno]
        crops = []
        for part in entry["parts"]:
            page_png = pages_dir / f"page_{part['page']:03d}.png"
            if not page_png.exists():
                continue
            img = Image.open(page_png)
            y0_px = max(0, int(part["y0"] * scale))
            y1_px = min(img.height, int(part["y1"] * scale))
            if y1_px <= y0_px:
                continue
            crops.append(img.crop((0, y0_px, img.width, y1_px)))

        if not crops:
            continue

        total_h = sum(c.height for c in crops)
        max_w = max(c.width for c in crops)
        combined = Image.new("RGB", (max_w, total_h), "white")
        y_cursor = 0
        for c in crops:
            combined.paste(c, (0, y_cursor))
            y_cursor += c.height

        safe_name = qno if qno.isdigit() else qno.replace("/", "_")
        out_name = f"q_{int(safe_name):02d}.png" if safe_name.isdigit() else f"q_{safe_name}.png"
        out_path = out_dir / out_name
        combined.save(out_path)
        saved.append(str(out_path))

    return saved


def build_all_boundaries(pdf_path: str, image_only_pages: Optional[list[int]] = None) -> dict:
    """텍스트 레이어 있는 부분은 규칙 기반, 이미지 전용 페이지는 미확정 fallback으로
    합쳐서 최종 boundaries dict를 만든다."""
    boundaries = rule_based_split(pdf_path)
    if image_only_pages:
        boundaries.update(fallback_whole_page_split(pdf_path, image_only_pages))
    return boundaries
