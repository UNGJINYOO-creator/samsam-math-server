"""PDF 페이지를 이미지로 렌더링 (중간 산출물: pages/page_XX.png)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import pymupdf as fitz

from config import RENDER_DPI


def render_pages(
    pdf_path: str,
    out_dir: Path,
    dpi: int = RENDER_DPI,
    on_progress: Optional[Callable[[int, int], None]] = None,
    skip_existing: bool = False,
    start_page: int = 1,
) -> list[Path]:
    """skip_existing=True면 이미 만들어진 페이지 이미지는 다시 그리지 않는다
    (중간에 끊겼다가 다시 열었을 때 처음부터 다시 하지 않도록).
    start_page(1-based) 앞 페이지는 그리지 않는다. 파일 이름은 원래 페이지 번호를 유지한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    first = max(0, start_page - 1)
    total = len(doc) - first

    saved: list[Path] = []
    for i in range(first, len(doc)):
        page = doc[i]
        out_path = out_dir / f"page_{i + 1:03d}.png"
        if not (skip_existing and out_path.exists()):
            # 임시 파일에 다 쓴 뒤 이름을 바꿔서, 중간에 끊겨도 깨진 이미지가 "완성본"으로 남지 않게 함
            tmp_path = out_dir / f"tmp_render_{i + 1:03d}.png"  # "page_*.png" 글롭에 안 걸리는 이름
            page.get_pixmap(matrix=matrix).save(str(tmp_path))
            tmp_path.replace(out_path)
        saved.append(out_path)
        if on_progress:
            on_progress(i - first + 1, total)
    doc.close()
    return saved


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("사용법: python core/page_render.py <pdf_path> <out_dir>")
        sys.exit(1)
    paths = render_pages(sys.argv[1], Path(sys.argv[2]))
    print(f"{len(paths)}개 페이지 렌더링 완료 -> {sys.argv[2]}")
