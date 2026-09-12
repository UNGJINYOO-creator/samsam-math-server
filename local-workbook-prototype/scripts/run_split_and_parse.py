"""1일차: 정답표 파싱(answers.json) + 문제 이미지 분할(반자동 보정 포함).

프로젝트에서 가장 시간이 오래 걸릴 것으로 예상되는 단계이므로,
자동 분할/파싱을 먼저 돌려두고 나머지는 correct_app.py(1분 눈 검수 도구)에서
사람이 보정하는 흐름을 전제로 한다.

사용법:
    python scripts/run_split_and_parse.py \
        --project ./math_grade1_project \
        --pdf ./math_grade1_project/source.pdf \
        --answer-pages 41,42

--answer-pages는 책 뒤편 "빠른 정답표"가 있는 실제 PDF 페이지 번호(1-based)를
사람이 직접 넘겨준다 (표지/목차 등으로 정답표 위치가 책마다 달라 자동 탐지가
불안정하므로, 이 값은 자동화하지 않고 사용자가 지정하는 것으로 설계했다).

--explanation-pages (선택)는 "정답과 해설"처럼 문항별 풀이 과정 전체가 있는
페이지를 추가로 넘기면, 그 해설을 answers.json에 저장해둔다. 태블릿 풀이
화면에서 오답을 2번 틀렸을 때 이 해설을 즉시 보여주므로 Gemini 호출 없이
빠르게 뜬다 (해설이 없는 문항만 Gemini로 생성).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ANSWERS_FILENAME, BOUNDARIES_FILENAME, PAGES_DIRNAME, QUESTIONS_DIRNAME  # noqa: E402
from core.answer_parser import (  # noqa: E402
    merge_explanations,
    parse_answer_pages,
    parse_explanation_pages,
    save_answers,
)
from core.page_render import render_pages  # noqa: E402
from core.pdf_analysis import check_text_layer  # noqa: E402
from core.question_split import build_all_boundaries, export_question_crops, save_boundaries  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="프로젝트 폴더 (예: ./math_grade1_project)")
    parser.add_argument("--pdf", required=True, help="스캔 PDF 경로")
    parser.add_argument(
        "--answer-pages",
        required=True,
        help="정답표가 있는 PDF 페이지 번호(1-based), 콤마로 구분. 예: 41,42",
    )
    parser.add_argument(
        "--answer-ocr",
        action="store_true",
        help="정답표 페이지에 텍스트 레이어가 없을 때(순수 이미지) 지정",
    )
    parser.add_argument(
        "--explanation-pages",
        default=None,
        help="(선택) 문항별 풀이 과정 전체가 있는 '정답과 해설' 페이지 번호, 콤마로 구분",
    )
    args = parser.parse_args()

    project_dir = Path(args.project)
    project_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = project_dir / PAGES_DIRNAME
    questions_dir = project_dir / QUESTIONS_DIRNAME

    print("1/4 페이지 이미지 렌더링 중...")
    render_pages(args.pdf, pages_dir)

    print("2/4 텍스트 레이어 확인 중...")
    text_results = check_text_layer(args.pdf)
    image_only_pages = [r.page_no - 1 for r in text_results if not r.has_text_layer]
    if image_only_pages:
        print(
            f"  -> 텍스트 레이어 없는 페이지 {len(image_only_pages)}개 발견 "
            "(이 페이지의 문제는 자동 분할이 안 되어 있으니 correct_app.py에서 직접 경계를 지정하세요)"
        )

    print("3/4 문제 경계 자동 분할 중...")
    boundaries = build_all_boundaries(args.pdf, image_only_pages=image_only_pages)
    boundaries_path = project_dir / BOUNDARIES_FILENAME
    save_boundaries(boundaries_path, boundaries)
    saved = export_question_crops(boundaries, pages_dir, questions_dir)
    print(f"  -> {len(saved)}개 문제 이미지 생성 -> {questions_dir}")

    print("4/4 정답표 파싱 중...")
    answer_pages = [int(p) for p in args.answer_pages.split(",")]
    answers = parse_answer_pages(args.pdf, answer_pages, use_ocr=args.answer_ocr)

    if args.explanation_pages:
        explanation_pages = [int(p) for p in args.explanation_pages.split(",")]
        explanations = parse_explanation_pages(args.pdf, explanation_pages, use_ocr=args.answer_ocr)
        answers = merge_explanations(answers, explanations)
        print(f"  -> {len(explanations)}개 문항 해설도 함께 저장 (Gemini 호출 없이 즉시 표시됨)")

    save_answers(project_dir / ANSWERS_FILENAME, answers)
    print(f"  -> {len(answers)}개 문항 정답 파싱 -> {project_dir / ANSWERS_FILENAME}")

    print(
        "\n완료. 다음 단계: "
        "`streamlit run correct_app.py -- --project "
        f"{project_dir}` 로 1분 눈 검수를 진행하세요."
    )


if __name__ == "__main__":
    main()
