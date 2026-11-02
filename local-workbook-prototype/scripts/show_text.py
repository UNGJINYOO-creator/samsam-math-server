"""PDF에 들어있는 글자(OCR 텍스트)를 문제별로 꺼내서, AI에게 글자만 보내도 될 만큼 깨끗한지 확인한다.

글자만으로 풀 수 있으면 이미지를 못 읽는 모델(대부분의 Mistral 모델 등)도 쓸 수 있고 훨씬 빠르다.
하지만 수학 문제는 OCR에서 분수·지수·루트 모양이 납작해지거나(예: x² -> x2),
출판사 전용 글꼴 때문에 기호가 엉뚱한 글자로 나오기도 하고, 그림은 아예 글자로 안 남는다.
그래서 실제 책 여러 쪽을 뽑아서 먼저 확인해본다. AI는 쓰지 않는다 (무료, 몇 초).

사용법 (local-workbook-prototype 폴더에서):
    python scripts/show_text.py                      # 14쪽~끝에서 무작위 5쪽
    python scripts/show_text.py --pages 20 74 105    # 원하는 쪽들
    python scripts/show_text.py --random 8 --from 14 # 무작위 8쪽
결과 전체는 ocr_check.txt 에 저장된다 (이 파일을 채팅에 올려주면 같이 볼 수 있음).
"""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf as fitz  # noqa: E402

from core.page_questions import detect_questions_by_text  # noqa: E402
from scripts.check_nvidia import pick_pdf  # noqa: E402

REPORT = Path("ocr_check.txt")
SUSPICIOUS = set("!@`�")  # 전용 글꼴이 기호를 엉뚱한 글자로 바꿀 때 자주 보이는 것들 (예: ° -> !, ² -> @)


def looks_garbled(text: str) -> list[str]:
    reasons = []
    pua = sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF)  # 글꼴 전용 영역 글자 = 화면엔 기호인데 글자로는 의미 없음
    odd = [c for c in text if c in SUSPICIOUS]
    if pua:
        reasons.append(f"읽을 수 없는 글꼴 전용 글자 {pua}개")
    if odd:
        reasons.append(f"수식 기호가 깨진 것으로 보이는 글자({''.join(sorted(set(odd)))}) {len(odd)}개")
    if not any("가" <= c <= "힣" for c in text):
        reasons.append("한글이 하나도 안 읽힘")
    return reasons


def drawing_count(drawings: list[fitz.Rect], rect: fitz.Rect) -> int:
    """문제 영역 안의 선/도형 개수. 많으면 그림·그래프이거나 분수 막대가 있다는 뜻."""
    # 분수 막대 같은 가로선은 높이가 0이라 Rect.intersects가 False를 돌려서, 좌표로 직접 비교한다
    return sum(1 for d in drawings if d.x0 <= rect.x1 and d.x1 >= rect.x0 and d.y0 <= rect.y1 and d.y1 >= rect.y0)


def check_page(doc: fitz.Document, pdf: Path, page_no: int, out: list[str]) -> tuple[int, int]:
    """한 쪽을 검사해서 out에 자세한 내용을 쌓고 (문제 수, 깨끗한 문제 수)를 돌려준다."""
    out.append(f"\n══════════ {page_no}쪽 ══════════")
    questions = detect_questions_by_text(str(pdf), page_no)
    if not questions:
        whole = doc[page_no - 1].get_text("text").strip()
        out.append("글자로 문제 번호를 못 찾았어요 (설명 쪽이거나, 스캔 이미지만 있거나, 번호 모양이 달라요).")
        out.append(f"이 쪽 전체 글자 앞부분: {whole[:200] or '(글자 없음)'}")
        return 0, 0

    page = doc[page_no - 1]
    drawings = [fitz.Rect(d["rect"]) for d in page.get_drawings()]
    clean = 0
    for q in questions:
        rect = fitz.Rect(*q.bbox)
        text = page.get_text("text", clip=rect).strip()
        reasons = looks_garbled(text)
        n = drawing_count(drawings, rect)
        if n >= 3:
            reasons.append(f"선/도형 {n}개 (그림·그래프나 분수가 있을 수 있음 — 글자만으로는 모양이 사라짐)")
        clean += not reasons
        out.append(f"\n─── {q.no}번 : {'글자만으로 풀어도 될 듯' if not reasons else '글자만으로는 위험'}")
        out.append(text or "(글자 없음)")
        out.extend(f"   ⚠ {r}" for r in reasons)
    return len(questions), clean


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", help="문제집 PDF (생략하면 앱에서 풀던 문제집)")
    parser.add_argument("--pages", type=int, nargs="+", help="검사할 쪽 번호들, 예: --pages 20 74 105")
    parser.add_argument("--random", type=int, default=5, help="--pages가 없을 때 무작위로 뽑을 쪽 수 (기본 5)")
    parser.add_argument("--from", dest="first", type=int, default=14, help="무작위로 뽑을 때 시작 쪽 (기본 14)")
    args = parser.parse_args()

    pdf = pick_pdf(args.pdf)
    with fitz.open(str(pdf)) as doc:
        total = doc.page_count
        if args.pages:
            pages = [p for p in args.pages if 1 <= p <= total]
            skipped = sorted(set(args.pages) - set(pages))
            if skipped:
                print(f"이 PDF는 {total}쪽까지라서 {skipped}쪽은 건너뛰어요.")
        else:
            first = min(max(1, args.first), total)
            pool = range(first, total + 1)
            pages = random.sample(pool, min(args.random, len(pool)))
        pages = sorted(set(pages))
        if not pages:
            print("검사할 쪽이 없어요.")
            return

        print(f"문제집: {pdf.name} (전체 {total}쪽)")
        print(f"검사할 쪽: {', '.join(map(str, pages))}\n")
        out = [f"문제집: {pdf.name} / 검사한 쪽: {', '.join(map(str, pages))}"]
        summary = []
        for p in pages:
            found, clean = check_page(doc, pdf, p, out)
            summary.append((p, found, clean))
            print(f"  {p:>4}쪽 : " + (f"문제 {found}개 중 글자 깨끗 {clean}개" if found else "문제 번호 못 찾음"))

    all_found = sum(f for _, f, _ in summary)
    all_clean = sum(c for _, _, c in summary)
    lines = ["", f"합계: 문제 {all_found}개 중 {all_clean}개는 글자가 깨끗해 보여요."]
    if all_found:
        ratio = all_clean / all_found
        if ratio >= 0.8:
            lines.append("→ 글자로 푸는 방식을 붙여볼 만해요. (그래도 분수·지수가 맞게 읽혔는지 ocr_check.txt에서 눈으로 확인)")
        elif ratio >= 0.4:
            lines.append("→ 절반쯤만 깨끗해요. '글자 + 이미지 같이 보내기' 또는 '깨끗한 문제만 글자로' 방식이 맞아 보여요.")
        else:
            lines.append("→ 글자가 많이 깨져 있어요. 지금처럼 이미지로 푸는 게 안전해요.")
    print("\n".join(lines))

    REPORT.write_text("\n".join(out + lines) + "\n", encoding="utf-8")
    print(f"\n문제별 글자 전체는 {REPORT.resolve()} 에 저장했어요. 이 파일을 채팅에 올려주면 같이 볼게요.")


if __name__ == "__main__":
    main()
