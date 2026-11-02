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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf as fitz  # noqa: E402

import practice_engine as engine  # noqa: E402
from bank_builder import BankJob  # noqa: E402
from subjects.math.nvidia_client import nvidia_available  # noqa: E402


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

    job = BankJob(pdf.name, first, last, args.count, args.redo, say=lambda t: print("  " + t, flush=True))
    if job.todo:
        backup = " · Gemini 한도에 걸리면 보조 AI(NVIDIA)로 계속" if nvidia_available() else ""
        print(f"\n{first}~{last}쪽 중 {len(job.todo)}쪽을 만들어요. 쪽당 20~40초{backup}.")
        print("중간에 창을 닫아도 다음에 이어서 해요. (마클리 앱의 ⚙ 관리 화면에서도 할 수 있어요)\n")
    try:
        job.run()
    except KeyboardInterrupt:
        print("\n\n멈췄어요. 다음에 실행하면 이어서 해요.")


if __name__ == "__main__":
    main()
