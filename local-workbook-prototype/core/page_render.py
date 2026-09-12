"""PDF 페이지를 이미지로 렌더링 (중간 산출물: pages/page_XX.png)."""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

from config import RENDER_DPI


def render_pages(pdf_path: str, out_dir: Path, dpi: int = RENDER_DPI) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    saved: list[Path] = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=matrix)
        out_path = out_dir / f"page_{i + 1:03d}.png"
        pix.save(str(out_path))
        saved.append(out_path)
    doc.close()
    return saved


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("사용법: python core/page_render.py <pdf_path> <out_dir>")
        sys.exit(1)
    paths = render_pages(sys.argv[1], Path(sys.argv[2]))
    print(f"{len(paths)}개 페이지 렌더링 완료 -> {sys.argv[2]}")
