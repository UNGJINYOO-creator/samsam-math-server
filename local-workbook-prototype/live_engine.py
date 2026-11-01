"""즉석 풀이 모드의 화면 밖 작업: 페이지 준비, 정답 저장소, 백그라운드 미리 풀기.

Streamlit은 버튼을 누를 때마다 live_app.py를 처음부터 다시 실행한다. 그래서 백그라운드
작업 풀과 잠금은 이렇게 따로 import되는 모듈에 둬야 다시 실행돼도 그대로 유지된다.
여기서는 st.* 를 쓰지 않는다 (백그라운드 스레드에서 부르면 안 되기 때문).

미리 풀기: 문제가 화면에 뜨면 그 문제와 다음 PREFETCH_AHEAD개 문제(필요하면 다음 페이지까지)를
뒤에서 AI로 풀어 answers.json에 저장해 둔다. 학생이 채점하기를 누를 때쯤이면 대부분 이미
끝나 있어서 바로 채점된다. 아직 안 끝났으면 그 작업이 끝나기만 기다린다(같은 문제를 두 번
부르지 않음).
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pymupdf as fitz

from config import ANSWERS_FILENAME, PAGES_DIRNAME, QUESTIONS_DIRNAME
from core.answer_parser import load_answers, save_answers
from core.page_questions import crop_questions, detect_questions_by_text, render_page
from subjects.math.ai_layout import detect_questions_by_ai
from subjects.math.ai_solver import solve_question
from subjects.math.gemini_client import GeminiError

PAGE_CACHE_DIRNAME = "page_cache"
PREFETCH_AHEAD = 2  # 지금 문제 말고 몇 문제 앞까지 미리 풀지. 늘릴수록 건너뛴 문제 때문에 버려지는 AI 호출이 늘어난다.
CIRCLED = "①②③④⑤"

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prefetch")
_answers_lock = threading.Lock()  # answers.json 읽고-고치고-쓰기를 한 번에 하나만
_inflight_lock = threading.Lock()
_inflight: dict[tuple[str, str, int], Future] = {}
_page_locks: dict[str, threading.Lock] = {}
_generation: dict[tuple[str, int], int] = {}  # 페이지 문제를 다시 찾을 때마다 +1 -> 옛 결과가 새 결과를 덮지 않게


def page_png_path(project_dir: Path, page_no: int) -> Path:
    return project_dir / PAGES_DIRNAME / f"page_{page_no:03d}.png"


def answer_key(page: int, idx: int) -> str:
    return f"p{page}-{idx + 1}"


def _page_of_key(key: str) -> int:
    return int(key[1:].split("-")[0])


def _gen(project_dir: Path, page_no: int) -> int:
    return _generation.get((str(project_dir), page_no), 0)


# ───────────────────────── 정답 저장소 ─────────────────────────


def get_answer(project_dir: Path, key: str) -> dict | None:
    with _answers_lock:
        return load_answers(project_dir / ANSWERS_FILENAME).get(key)


def set_answer_field(project_dir: Path, key: str, field: str, value) -> None:
    with _answers_lock:
        path = project_dir / ANSWERS_FILENAME
        answers = load_answers(path)
        if key in answers:
            answers[key][field] = value
            save_answers(path, answers)


def _store_answer(project_dir: Path, key: str, entry: dict, gen: int) -> None:
    with _answers_lock:
        if _gen(project_dir, _page_of_key(key)) != gen:
            return  # 그사이 이 페이지 문제를 다시 찾았음 -> 옛 문제 기준 정답이라 버린다
        path = project_dir / ANSWERS_FILENAME
        answers = load_answers(path)
        answers[key] = entry
        save_answers(path, answers)


def _forget_page_answers(project_dir: Path, page_no: int) -> None:
    with _answers_lock:
        _generation[(str(project_dir), page_no)] = _gen(project_dir, page_no) + 1
        path = project_dir / ANSWERS_FILENAME
        answers = load_answers(path)
        stale = [k for k in answers if k.startswith(f"p{page_no}-")]
        if stale:
            for k in stale:
                del answers[k]
            save_answers(path, answers)


def _solve(project_dir: Path, key: str, img_path: Path, gen: int) -> dict:
    cached = get_answer(project_dir, key)
    if cached and cached.get("answer"):
        return cached
    entry = {**solve_question(img_path), "shape_dependent": False}
    _store_answer(project_dir, key, entry, gen)
    return entry


def _submit_solve(project_dir: Path, key: str, img_path) -> Future:
    gen = _gen(project_dir, _page_of_key(key))
    ident = (str(project_dir), key, gen)
    with _inflight_lock:
        fut = _inflight.get(ident)
        if fut is None or (fut.done() and fut.exception() is not None):  # 실패한 작업은 다시 시도
            fut = _executor.submit(_solve, project_dir, key, Path(img_path), gen)
            _inflight[ident] = fut
        return fut


def ensure_answer(project_dir: Path, key: str, img_path) -> dict:
    """저장된 정답이 있으면 바로, 미리 풀기가 진행 중이면 그걸 기다려서, 둘 다 아니면 지금 풀어서 돌려준다."""
    cached = get_answer(project_dir, key)
    if cached and cached.get("answer"):
        return cached
    return _submit_solve(project_dir, key, img_path).result()


# ───────────────────────── 페이지 준비 ─────────────────────────


def _page_lock(project_dir: Path, page_no: int) -> threading.Lock:
    with _inflight_lock:
        return _page_locks.setdefault(f"{project_dir}|{page_no}", threading.Lock())


def _mc_hints(pdf_path: Path, page_no: int, bboxes: list, method: str) -> list[bool | None]:
    """문제 영역 글자에 ①~⑤가 있으면 객관식(True). 글자로 찾은 페이지인데 없으면 False,
    AI로 찾은 페이지(스캔본 등)는 글자로 판단할 수 없으니 None(모름)."""
    hints: list[bool | None] = []
    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_no - 1]
        for bbox in bboxes:
            text = page.get_text("text", clip=fitz.Rect(*bbox))
            if any(c in text for c in CIRCLED):
                hints.append(True)
            else:
                hints.append(False if method == "text" else None)
    return hints


def cached_page(project_dir: Path, page_no: int) -> dict | None:
    path = project_dir / PAGE_CACHE_DIRNAME / f"p{page_no:03d}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def prepare_page(project_dir: Path, pdf_path: Path, page_no: int, force_ai: bool = False) -> dict:
    """이 페이지의 문제 목록. 한 번 처리한 페이지는 page_cache에 저장해두고 재사용한다.

    반환: {"method": "text"|"ai"|"none", "questions": [{"no", "image", "mc_hint"}], "error": str|None}
    """
    with _page_lock(project_dir, page_no):
        cache_path = project_dir / PAGE_CACHE_DIRNAME / f"p{page_no:03d}.json"
        if cache_path.exists() and not force_ai:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        page_png = render_page(str(pdf_path), page_no, page_png_path(project_dir, page_no))

        questions, method, error = [], "none", None
        if not force_ai:
            questions = detect_questions_by_text(str(pdf_path), page_no)
            method = "text" if questions else "none"
        if force_ai or not questions:
            with fitz.open(str(pdf_path)) as doc:
                rect = doc[page_no - 1].rect
            try:
                questions = detect_questions_by_ai(page_png, rect.width, rect.height)
                method = "ai" if questions else "none"
            except GeminiError as e:
                error = str(e)
            except Exception as e:
                error = f"AI로 문제를 찾는 중 오류가 발생했어요: {e}"

        if error is not None:
            # AI 호출 자체가 실패(키 없음 등)했으면 기존 결과를 그대로 두고 저장도 안 한다 -> 고친 뒤 재시도됨
            if cache_path.exists():
                return {**json.loads(cache_path.read_text(encoding="utf-8")), "error": error}
            return {"method": "none", "questions": [], "error": error}

        questions_dir = project_dir / QUESTIONS_DIRNAME
        for old in questions_dir.glob(f"p{page_no:03d}_q*.png"):
            old.unlink()
        cropped = crop_questions(page_png, questions, questions_dir, page_no)
        _forget_page_answers(project_dir, page_no)  # 문제 순서가 바뀌었을 수 있으니 이 페이지 정답은 새로 푼다
        hints = _mc_hints(pdf_path, page_no, [q.bbox for q, _ in cropped], method)

        data = {
            "method": method,
            "questions": [
                {"no": q.no, "image": str(path), "mc_hint": hint} for (q, path), hint in zip(cropped, hints)
            ],
            "error": None,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data


# ───────────────────────── 미리 풀기 ─────────────────────────


def _prefetch_job(project_dir: Path, pdf_path: Path, page_no: int, idx: int, total_pages: int) -> None:
    try:
        remaining = PREFETCH_AHEAD + 1  # 지금 문제 포함
        page, i = page_no, idx
        while remaining > 0 and page <= min(total_pages, page_no + 1):
            questions = prepare_page(project_dir, pdf_path, page)["questions"]
            while i < len(questions) and remaining > 0:
                _submit_solve(project_dir, answer_key(page, i), questions[i]["image"])
                i += 1
                remaining -= 1
            page, i = page + 1, 0
    except Exception:
        pass  # 미리 풀기는 실패해도 괜찮다. 화면에서 필요할 때 다시 시도한다.


def prefetch_around(project_dir: Path, pdf_path: Path, page_no: int, idx: int, total_pages: int) -> None:
    """지금 문제 + 다음 PREFETCH_AHEAD 문제를 백그라운드에서 미리 푼다. 바로 돌아온다."""
    _executor.submit(_prefetch_job, project_dir, pdf_path, page_no, idx, total_pages)
