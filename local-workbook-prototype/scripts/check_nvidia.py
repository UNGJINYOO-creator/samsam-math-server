"""Gemini와 보조 AI(NVIDIA)가 같은 페이지 문제를 얼마나 똑같이 푸는지 비교한다.

보조 AI가 정답을 자주 다르게 내면, 아이가 맞게 풀어도 오답 처리될 수 있다.
그래서 실제 책 몇 쪽으로 먼저 비교해보고 쓸지 정하는 용도.

사용법 (local-workbook-prototype 폴더에서, 두 키를 등록해둔 cmd 창에서):
    python scripts/check_nvidia.py
PDF는 이 폴더에서 자동으로 찾고(여러 개면 번호로 고름), 페이지는 앱에서 마지막으로 풀던 페이지를 쓴다.
직접 정하려면: python scripts/check_nvidia.py --pdf 문제집.pdf --page 50

AI 사용량: Gemini 요청 1번 + NVIDIA 요청 (문제 수 + 처음 모델 찾을 때 몇 번).
"""
import argparse
import json
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


def pick_pdf(given: str | None) -> Path:
    """--pdf를 안 줬거나 그런 파일이 없으면, 이 폴더의 PDF 중에서 고른다."""
    if given and Path(given).exists():
        return Path(given)
    pdfs = sorted(Path(".").glob("*.pdf"))
    if given:
        matches = [p for p in pdfs if given.replace(".pdf", "") in p.name]
        if len(matches) == 1:
            return matches[0]
        print(f"'{given}' 파일을 찾지 못해서 이 폴더의 PDF 중에서 고를게요.")
    if not pdfs:
        print("이 폴더에 PDF가 없어요. local-workbook-prototype 폴더에서 실행했는지 확인해주세요.")
        sys.exit(1)
    if len(pdfs) == 1:
        return pdfs[0]
    for i, p in enumerate(pdfs, 1):
        print(f"  {i}. {p.name}")
    choice = input("비교할 문제집 번호를 입력하세요: ").strip()
    return pdfs[int(choice) - 1]


def pick_page(given: int | None, project_dir: Path) -> int:
    """--page를 안 줬으면 앱에서 마지막으로 풀던 페이지, 그것도 없으면 물어본다."""
    if given:
        return given
    pos = project_dir / ".position.json"
    if pos.exists():
        return int(json.loads(pos.read_text(encoding="utf-8"))["page"])
    return int(input("비교할 페이지 번호를 입력하세요 (문제가 있는 페이지): ").strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", help="문제집 PDF 파일 (생략하면 이 폴더에서 자동으로 찾음)")
    parser.add_argument("--page", type=int, help="비교할 페이지 번호 (생략하면 앱에서 마지막으로 풀던 페이지)")
    args = parser.parse_args()

    if not nvidia_client.nvidia_available():
        print("NVIDIA_API_KEY가 설정되어 있지 않아요. 먼저 setx NVIDIA_API_KEY \"nvapi-...\" 로 등록하고 새 cmd 창에서 다시 실행해주세요.")
        sys.exit(1)

    pdf = pick_pdf(args.pdf)
    project_dir = pdf.parent / f"{pdf.stem}_bypage"  # live_app.py와 같은 폴더를 쓴다
    args.page = pick_page(args.page, project_dir)
    print(f"문제집: {pdf.name}")
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
