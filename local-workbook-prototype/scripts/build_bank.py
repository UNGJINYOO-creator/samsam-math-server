"""문제 은행 만들기: 문제집 PDF를 한 쪽씩 넘기면서 Gemini가 새 문제와 정답을 미리 만들어 쌓아둔다.

아이가 마클리에서 풀 때는 이 은행에서 꺼내기만 해서 기다림이 없다.

    python scripts/build_bank.py                       # 물어보는 대로 답하면 됨 (run_bank.bat)
    python scripts/build_bank.py --from 14 --to 60
    python scripts/build_bank.py --book 문제집.pdf --count 8

- 목차·개념 설명처럼 문제가 없는 쪽은 PDF 글자로 먼저 걸러서 AI를 안 쓴다.
- Gemini 무료 한도(하루 약 20번)에 걸리면, 풀릴 때까지 기다렸다가 자동으로 이어서 한다.
  (NVIDIA_API_KEY가 있으면 그동안 보조 AI로 계속 만든다. 대신 느리다.)
- 중간에 창을 닫아도 다음에 실행하면 이미 만든 쪽은 건너뛰고 이어서 한다.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402

import practice_engine as engine  # noqa: E402
from core.page_questions import detect_questions_by_text  # noqa: E402
from subjects.math.gemini_client import GeminiError, QuotaExceededError, quota_blocked_seconds  # noqa: E402
from subjects.math.nvidia_client import nvidia_available  # noqa: E402
from subjects.math.problem_maker import make_bank_page  # noqa: E402

QUESTION_WORDS = re.compile(r"구하시오|고르시오|하시오|것은\s*\?|인가\s*\?|\?\s*$", re.MULTILINE)


def ask(prompt: str, default: int, lo: int, hi: int) -> int:
    while True:
        raw = input(f"{prompt} [그냥 엔터 = {default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and lo <= int(raw) <= hi:
            return int(raw)
        print(f"  {lo}부터 {hi} 사이 숫자로 입력해주세요.")


def pick_book(given: str | None) -> Path:
    pdfs = sorted(engine.PDF_DIR.glob("*.pdf"))
    if given:
        match = [p for p in pdfs if p.name == given or given.replace(".pdf", "") in p.stem]
        if len(match) == 1:
            return match[0]
        print(f"'{given}' 문제집을 찾지 못했어요.")
    if not pdfs:
        print(f"{engine.PDF_DIR} 폴더에 문제집 PDF가 없어요.")
        sys.exit(1)
    if len(pdfs) == 1:
        return pdfs[0]
    for i, p in enumerate(pdfs, 1):
        print(f"  {i}. {p.stem}")
    return pdfs[ask("어느 문제집으로 만들까요? 번호", 1, 1, len(pdfs)) - 1]


def has_problems_by_text(pdf: Path, page_no: int) -> bool | None:
    """PDF 글자로 문제 있는 쪽인지 미리 판단. 글자가 없는 스캔 쪽이면 None(모름 -> AI가 판단)."""
    with fitz.open(str(pdf)) as doc:
        text = doc[page_no - 1].get_text("text")
    if len(text.strip()) < 30:
        return None
    if any(c in text for c in "①②③④⑤") or QUESTION_WORDS.search(text):
        return True
    return bool(detect_questions_by_text(str(pdf), page_no))


def known_concepts(book: str, page_no: int) -> list[str]:
    """같은 책 바로 앞쪽들에서 이미 쓴 유형 이름 (같은 유형은 같은 이름으로 모으려고)."""
    names: list[str] = []
    for prev in range(page_no - 1, max(0, page_no - 8), -1):
        banked = engine.bank_page(book, prev)
        if banked and banked["status"] == "ok":
            names += [c["name"] for c in banked["concepts"] if c["name"] not in names]
    return names


def wait_for_quota() -> None:
    secs = quota_blocked_seconds() or 3600
    until = time.strftime("%H:%M", time.localtime(time.time() + secs))
    print(f"\n  Gemini 무료 한도를 다 썼어요. 약 {int(secs // 60) + 1}분 뒤({until}쯤) 자동으로 이어서 할게요.")
    print("  (기다리기 싫으면 창을 닫으세요. 다음에 실행하면 여기서부터 이어서 해요.)")
    end = time.time() + secs + 30
    while time.time() < end:
        left = int(end - time.time())
        print(f"\r  다시 시작까지 {left // 60}분 {left % 60:02d}초   ", end="", flush=True)
        time.sleep(min(30, max(1, left)))
    print()


def build_one(pdf: Path, page_no: int, count: int) -> tuple[str, str]:
    """한 쪽 만들기. (상태, 설명)"""
    book = pdf.name
    by_text = has_problems_by_text(pdf, page_no)
    if by_text is False:
        engine.save_bank_page(book, page_no, None, "skip", "글자로 보니 문제가 없는 쪽")
        return "skip", "문제 없는 쪽 (AI 안 씀)"

    image = Image.open(engine.page_image_path(book, page_no))
    with fitz.open(str(pdf)) as doc:
        text = doc[page_no - 1].get_text("text")
    errors = 0
    while True:
        try:
            made = make_bank_page(image, text, known_concepts(book, page_no), count)
            break
        except QuotaExceededError:
            wait_for_quota()  # 한도 대기는 실패로 세지 않는다
        except GeminiError as e:
            errors += 1
            if errors >= 3:
                engine.save_bank_page(book, page_no, None, "error", str(e))
                return "error", f"실패: {e} (다음에 실행하면 다시 시도)"
            time.sleep(10)

    if not made["has_problems"]:
        engine.save_bank_page(book, page_no, None, "skip", "AI가 보니 문제가 없는 쪽")
        return "skip", "문제 없는 쪽"
    engine.save_bank_page(book, page_no, made, "ok")
    dropped = f", 검산에서 {len(made['dropped'])}개 버림" if made["dropped"] else ""
    solver = " [보조 AI]" if made["solver"] == "nvidia" else ""
    return "ok", f"{made['unit']} · 문제 {len(made['problems'])}개{dropped}{solver}"


def main() -> None:
    parser = argparse.ArgumentParser(description="문제 은행 만들기")
    parser.add_argument("--book")
    parser.add_argument("--from", dest="first", type=int)
    parser.add_argument("--to", dest="last", type=int)
    parser.add_argument("--count", type=int, default=8, help="쪽마다 만들 문제 수 (기본 8: 쉬움3·보통3·어려움2)")
    parser.add_argument("--redo", action="store_true", help="이미 만든 쪽도 다시 만들기")
    args = parser.parse_args()

    pdf = pick_book(args.book)
    with fitz.open(str(pdf)) as doc:
        total = doc.page_count
    summary = engine.bank_summary(pdf.name)
    print(f"\n문제집: {pdf.stem} (전체 {total}쪽)")
    if summary["ready_pages"]:
        print(f"이미 만든 쪽: {summary['ranges']} (문제 {summary['problems']}개)")
    last_done = summary["last_done"]
    default_first = last_done + 1 if last_done and last_done < total else min(14, total)
    first = args.first or ask("몇 쪽부터 만들까요?", default_first, 1, total)
    last = args.last or ask("몇 쪽까지 만들까요?", total, first, total)

    todo = []
    for page_no in range(first, last + 1):
        banked = engine.bank_page(pdf.name, page_no)
        if banked and banked["status"] in ("ok", "skip") and not args.redo:
            continue
        todo.append(page_no)
    if not todo:
        print("그 범위는 이미 다 만들었어요.")
        return
    backup = " · Gemini 한도에 걸리면 보조 AI(NVIDIA)로 계속" if nvidia_available() else ""
    print(f"\n{first}~{last}쪽 중 {len(todo)}쪽을 만들어요. 쪽당 20~40초{backup}. 중간에 창을 닫아도 다음에 이어서 해요.\n")

    ai_secs: list[float] = []
    made_total = 0
    for i, page_no in enumerate(todo, 1):
        t0 = time.time()
        print(f"[{i}/{len(todo)}] {page_no}쪽 ...", end=" ", flush=True)
        status, note = build_one(pdf, page_no, args.count)
        took = time.time() - t0
        if status == "ok":
            ai_secs.append(min(took, 300))
            made_total += int(re.search(r"문제 (\d+)개", note).group(1))
        eta = ""
        if ai_secs:
            left_min = sum(ai_secs) / len(ai_secs) * (len(todo) - i) / 60
            eta = f" · 남은 시간 최대 약 {left_min:.0f}분" if left_min >= 1 else ""
        print(f"{note} ({took:.0f}초){eta}")

    summary = engine.bank_summary(pdf.name)
    print(f"\n끝! 이번에 문제 {made_total}개를 만들었어요.")
    print(f"지금 은행에 있는 쪽: {summary['ranges'] or '없음'} (문제 {summary['problems']}개)")
    errors = [p for p in todo if (engine.bank_page(pdf.name, p) or {}).get("status") == "error"]
    if errors:
        print(f"실패한 쪽 {errors} 은 다음에 실행하면 다시 시도해요.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n멈췄어요. 다음에 실행하면 이어서 해요.")
