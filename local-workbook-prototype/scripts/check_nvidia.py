"""Gemini와 보조 AI(NVIDIA)가 같은 페이지 문제를 얼마나 똑같이 푸는지 비교한다.

보조 AI가 정답을 자주 다르게 내면, 아이가 맞게 풀어도 오답 처리될 수 있다.
그래서 실제 책 몇 쪽으로 먼저 비교해보고 쓸지 정하는 용도.

사용법 (local-workbook-prototype 폴더에서, 두 키를 set 해둔 cmd 창에서):
    python scripts/check_nvidia.py --pdf 문제집.pdf --page 50

AI 사용량: Gemini 요청 1번 + NVIDIA 요청 (문제 수 + 처음 모델 찾을 때 몇 번).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.grading import grade_answer  # noqa: E402
from live_engine import prepare_page  # noqa: E402
from subjects.math import nvidia_client  # noqa: E402
from subjects.math.ai_solver import solve_questions  # noqa: E402
from subjects.math.gemini_client import GeminiError, QuotaExceededError  # noqa: E402


def same_answer(a: dict, b: dict) -> bool:
    qtype = a["type"] if a["type"] == b["type"] else "short_answer"
    return grade_answer(qtype, a["answer"], b["answer"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, help="문제집 PDF 파일")
    parser.add_argument("--page", type=int, required=True, help="비교할 페이지 번호")
    args = parser.parse_args()

    if not nvidia_client.nvidia_available():
        print("NVIDIA_API_KEY가 설정되어 있지 않아요. 먼저 set NVIDIA_API_KEY=nvapi-... 를 해주세요.")
        sys.exit(1)

    pdf = Path(args.pdf)
    project_dir = pdf.parent / f"{pdf.stem}_bypage"  # live_app.py와 같은 폴더를 쓴다
    data = prepare_page(project_dir, pdf, args.page)
    paths = [Path(q["image"]) for q in data["questions"]]
    if not paths:
        print(f"{args.page}쪽에서 문제를 찾지 못했어요. 문제가 있는 페이지로 다시 해보세요.")
        sys.exit(1)
    print(f"{args.page}쪽 문제 {len(paths)}개를 비교합니다.\n")

    try:
        print("이미지를 읽을 수 있는 NVIDIA 모델 후보:", ", ".join(nvidia_client.list_vision_candidates()) or "(없음)")
        print("사용할 모델:", nvidia_client.chosen_model(), "\n")
        nv = solve_questions(paths, provider="nvidia")
    except GeminiError as e:
        print("NVIDIA로 풀지 못했어요:", e)
        sys.exit(1)

    try:
        gem = solve_questions(paths, provider="gemini")
    except QuotaExceededError as e:
        print("Gemini는 지금 쓸 수 없어서 NVIDIA 답만 보여드려요:", e, "\n")
        gem = None

    agree = 0
    for i, n in enumerate(nv):
        g = gem[i] if gem else None
        ok = bool(g) and same_answer(g, n)
        agree += ok
        mark = "같음" if ok else ("다름" if g else "-")
        print(f"  {i + 1}번째 문제 | Gemini: {g['answer'] if g else '-':<14} | NVIDIA: {n['answer']:<14} | {mark}")

    if gem:
        print(f"\n{len(nv)}문제 중 {agree}개 답이 같아요.")
        if agree == len(nv):
            print("보조 AI로 써도 괜찮아 보여요. 다른 페이지도 한두 번 더 확인해보세요.")
        else:
            print("답이 다른 문제가 있어요. 책 뒤 정답과 비교해서 어느 쪽이 맞는지 확인해보세요.")


if __name__ == "__main__":
    main()
