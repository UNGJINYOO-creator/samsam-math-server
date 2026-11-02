"""문제 은행 만들기 작업 (앱의 ⚙ 관리 화면과 run_bank.bat이 같이 쓴다).

한 쪽씩 넘기면서:
  - PDF 글자로 문제 없는 쪽(목차·개념 설명)을 먼저 걸러 AI를 안 쓰고,
  - 문제 있는 쪽은 Gemini가 새 문제 + 정답을 만들어 practice_data/bank/ 에 저장한다.
  - 무료 한도에 걸리면 풀릴 때까지 기다렸다가 이어서 한다 (멈추기를 누르면 바로 멈춤).
  - 이미 만든 쪽은 건너뛰므로 언제 멈춰도 다음에 이어서 할 수 있다.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from pathlib import Path

import pymupdf as fitz
from PIL import Image

import practice_engine as engine
from core.page_questions import detect_questions_by_text
from subjects.math.gemini_client import GeminiError, QuotaExceededError, quota_blocked_seconds
from subjects.math.problem_maker import make_bank_page

QUESTION_WORDS = re.compile(r"구하시오|고르시오|하시오|것은\s*\?|인가\s*\?|\?\s*$", re.MULTILINE)
DEFAULT_COUNT = 8


class Stopped(Exception):
    pass


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


class BankJob:
    """쪽 범위 하나를 만드는 작업. run()은 끝날 때까지 돌고, 다른 스레드에서 stop()으로 멈출 수 있다."""

    def __init__(self, book: str, first: int, last: int, count: int = DEFAULT_COUNT, redo: bool = False, say=print):
        self.book, self.first, self.last, self.count, self.redo = book, first, last, count, redo
        self.say = say
        self.pdf = engine._book_path(book)
        self.todo = [p for p in range(first, last + 1)
                     if redo or (engine.bank_page(book, p) or {}).get("status") not in ("ok", "skip")]
        self.done = 0
        self.made = 0
        self.current: int | None = None
        self.waiting_until: float | None = None
        self.running = False
        self.finished = False
        self.stopped = False
        self.log: list[str] = []
        self.ai_secs: list[float] = []
        self._stop = threading.Event()

    # ── 상태 ──
    def stop(self) -> None:
        self._stop.set()

    def _line(self, text: str) -> None:
        self.log.append(f"{datetime.now():%H:%M} {text}")
        del self.log[:-40]
        self.say(text)

    def eta_minutes(self) -> int | None:
        if not self.ai_secs:
            return None
        return round(sum(self.ai_secs) / len(self.ai_secs) * (len(self.todo) - self.done) / 60)

    def status(self) -> dict:
        return {
            "book": self.book, "first": self.first, "last": self.last,
            "total": len(self.todo), "done": self.done, "made": self.made, "current": self.current,
            "running": self.running, "finished": self.finished, "stopped": self.stopped,
            "waiting_seconds": max(0, int(self.waiting_until - time.time())) if self.waiting_until else 0,
            "eta_minutes": self.eta_minutes(), "log": self.log[-12:],
        }

    # ── 작업 ──
    def _wait_for_quota(self) -> None:
        secs = quota_blocked_seconds() or 3600
        self.waiting_until = time.time() + secs + 30
        until = time.strftime("%H:%M", time.localtime(self.waiting_until))
        self._line(f"Gemini 무료 한도를 다 썼어요. {until}쯤 자동으로 이어서 할게요.")
        try:
            while time.time() < self.waiting_until:
                if self._stop.wait(timeout=min(5, max(0.1, self.waiting_until - time.time()))):
                    raise Stopped
        finally:
            self.waiting_until = None

    def build_one(self, page_no: int) -> tuple[str, str]:
        book = self.book
        if has_problems_by_text(self.pdf, page_no) is False:
            engine.save_bank_page(book, page_no, None, "skip", "글자로 보니 문제가 없는 쪽")
            return "skip", "문제 없는 쪽 (AI 안 씀)"
        image = Image.open(engine.page_image_path(book, page_no))
        with fitz.open(str(self.pdf)) as doc:
            text = doc[page_no - 1].get_text("text")
        errors = 0
        while True:
            if self._stop.is_set():
                raise Stopped
            try:
                made = make_bank_page(image, text, known_concepts(book, page_no), self.count)
                break
            except QuotaExceededError:
                self._wait_for_quota()  # 한도 대기는 실패로 세지 않는다
            except GeminiError as e:
                errors += 1
                if errors >= 3:
                    engine.save_bank_page(book, page_no, None, "error", str(e))
                    return "error", f"실패: {e} (다음에 다시 시도)"
                time.sleep(10)
        if not made["has_problems"]:
            engine.save_bank_page(book, page_no, None, "skip", "AI가 보니 문제가 없는 쪽")
            return "skip", "문제 없는 쪽"
        engine.save_bank_page(book, page_no, made, "ok")
        self.made += len(made["problems"])
        dropped = f", 검산에서 {len(made['dropped'])}개 버림" if made["dropped"] else ""
        solver = " [보조 AI]" if made["solver"] == "nvidia" else ""
        return "ok", f"{made['unit']} · 문제 {len(made['problems'])}개{dropped}{solver}"

    def run(self) -> None:
        self.running = True
        try:
            if not self.todo:
                self._line(f"{self.first}~{self.last}쪽은 이미 다 만들었어요.")
            for page_no in self.todo:
                if self._stop.is_set():
                    raise Stopped
                self.current = page_no
                t0 = time.time()
                status, note = self.build_one(page_no)
                took = time.time() - t0
                if status == "ok":
                    self.ai_secs.append(min(took, 300))
                self.done += 1
                self._line(f"{page_no}쪽: {note} ({took:.0f}초)")
            summary = engine.bank_summary(self.book)
            self._line(f"끝! 이번에 문제 {self.made}개. 은행에 있는 쪽: {summary['ranges'] or '없음'}")
        except Stopped:
            self.stopped = True
            self._line("멈췄어요. 다시 시작하면 이어서 해요.")
        except Exception as e:  # 예상 못 한 오류도 화면에 보이게
            self._line(f"오류로 멈췄어요: {e}")
        finally:
            self.current = None
            self.running = False
            self.finished = True


# ───────────────────────── 앱(서버)에서 쓰는 작업 하나 ─────────────────────────

_job: BankJob | None = None
_job_lock = threading.Lock()


def start(book: str, first: int, last: int, count: int = DEFAULT_COUNT) -> dict:
    global _job
    with _job_lock:
        if _job and _job.running:
            raise engine.PracticeError("이미 문제 은행을 만들고 있어요. 멈춘 뒤 다시 시작해주세요.")
        with fitz.open(str(engine._book_path(book))) as doc:
            total = doc.page_count
        if not 1 <= first <= last <= total:
            raise engine.PracticeError(f"쪽 범위를 확인해주세요 (1~{total}쪽).")
        _job = BankJob(book, first, last, count, say=lambda _t: None)
        threading.Thread(target=_job.run, name="bank-builder", daemon=True).start()
        return _job.status()


def stop() -> dict:
    with _job_lock:
        if _job:
            _job.stop()
        return status()


def status() -> dict:
    return _job.status() if _job else {"running": False, "finished": False, "log": []}
