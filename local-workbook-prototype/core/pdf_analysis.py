"""0단계: PDF에 텍스트 레이어가 있는지 확인.

이 결과에 따라 이후 파이프라인 경로가 갈린다:
  - 텍스트 레이어 있음  -> 좌표/텍스트 기반 규칙 분할 (question_split.rule_based_split)
  - 텍스트 레이어 없음  -> OCR 필요 (Tesseract/PaddleOCR 경로, 이 프로토타입에서는
                          이미지 크롭 자체는 동일하게 동작하되 텍스트 추출만 OCR로 대체)

실행:
    python core/pdf_analysis.py /path/to/source.pdf
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

import pymupdf as fitz


@dataclass
class PageTextInfo:
    page_no: int  # 1-based
    char_count: int
    has_text_layer: bool


def check_text_layer(pdf_path: str, min_chars_per_page: int = 20) -> list[PageTextInfo]:
    """페이지별로 get_text()의 글자 수를 확인해 텍스트 레이어 존재 여부를 판단한다.

    min_chars_per_page: 이보다 적은 글자만 나오면 "실질적으로 텍스트 레이어 없음"으로 간주.
    (스캔 PDF에 페이지 번호 같은 워터마크 텍스트 몇 글자만 실제 텍스트로 박혀있는 경우가 있어서
     0 초과 여부만으로는 판단하지 않는다.)
    """
    doc = fitz.open(pdf_path)
    results: list[PageTextInfo] = []
    for i, page in enumerate(doc):
        text = page.get_text()
        char_count = len(text.strip())
        results.append(
            PageTextInfo(
                page_no=i + 1,
                char_count=char_count,
                has_text_layer=char_count >= min_chars_per_page,
            )
        )
    doc.close()
    return results


def summarize(results: list[PageTextInfo]) -> str:
    total = len(results)
    with_text = sum(1 for r in results if r.has_text_layer)
    without_text = total - with_text

    if without_text == 0:
        verdict = (
            "✅ 모든 페이지에 텍스트 레이어가 있습니다.\n"
            "   -> question_split.py의 규칙 기반(텍스트 좌표) 분할을 그대로 사용하면 됩니다."
        )
    elif with_text == 0:
        verdict = (
            "⚠️ 텍스트 레이어가 전혀 없는 순수 이미지 스캔본입니다.\n"
            "   -> OCR(Tesseract/PaddleOCR) 경로가 반드시 필요합니다.\n"
            "   -> question_split.py 호출 시 use_ocr=True 로 실행하세요."
        )
    else:
        verdict = (
            f"⚠️ 일부 페이지({without_text}/{total})만 텍스트 레이어가 없습니다.\n"
            "   -> 페이지별로 분기 처리가 필요합니다 (혼합 모드).\n"
            "   -> question_split.py는 페이지 단위로 자동 판단하여 텍스트/OCR을 선택합니다."
        )

    lines = [f"총 {total}페이지 중 텍스트 레이어 있음: {with_text} / 없음: {without_text}", "", verdict, ""]
    lines.append("페이지별 상세:")
    for r in results:
        mark = "TEXT" if r.has_text_layer else "IMAGE-ONLY"
        lines.append(f"  p.{r.page_no:>3}  chars={r.char_count:<6}  [{mark}]")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python core/pdf_analysis.py <pdf_path>")
        sys.exit(1)
    results = check_text_layer(sys.argv[1])
    print(summarize(results))
