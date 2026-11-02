"""0일차 (30분 이내): 실제 PDF 샘플로 텍스트 레이어 유무를 확인한다.

이 결과로 1일차 계획(순수 좌표 분할 vs OCR 병행)을 확정한다.

사용법:
    python scripts/run_step0_check_text_layer.py <pdf_path> [--min-chars 20]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pdf_analysis import check_text_layer, summarize  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument(
        "--save-image-only-pages",
        default=None,
        help="이미지 전용(텍스트 레이어 없음) 페이지 번호 목록을 저장할 json 경로 "
        "(run_split_and_parse.py가 이후 단계에서 참조)",
    )
    args = parser.parse_args()

    results = check_text_layer(args.pdf_path, min_chars_per_page=args.min_chars)
    print(summarize(results))

    if args.save_image_only_pages:
        image_only = [r.page_no - 1 for r in results if not r.has_text_layer]  # 0-based
        Path(args.save_image_only_pages).write_text(
            json.dumps(image_only, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n이미지 전용 페이지 인덱스(0-based) 저장됨 -> {args.save_image_only_pages}")


if __name__ == "__main__":
    main()
