"""연습 모드(마클리 앱)의 화면 밖 작업: 문제 세트 만들기·저장, 채점 기록, 복습, 미리 만들기.

문제 세트(set) 3종류:
  - book   : 문제집 한 쪽의 개념으로 만든 새 문제 (쪽마다 하나, 계속 이어서 더 만들 수 있음)
  - free   : 오늘의 문제 (학년 교육과정에서 골고루)
  - review : 지금까지 틀린 유형만 모아서 새로 만든 문제

저장 위치: local-workbook-prototype/practice_data/
  sets/<세트id>.json   문제 + 각 문제의 결과(right/wrong)
  pages/<책이름>/page_NNN.png   쪽 미리보기/AI에게 보낼 이미지
  log.csv              풀이 기록 (부모님용 기록 화면에서 사용)

웹 서버(main.py)는 요청마다 스레드에서 이 함수들을 부른다. 그래서 세트 파일 읽고-고치고-쓰기는
잠금 안에서만 하고, 오래 걸리는 AI 호출은 잠금 밖에서 한다.
"""
from __future__ import annotations

import csv
import json
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pymupdf as fitz

from core.cloze import build_cloze
from core.grading import grade_answer
from core.page_questions import render_page
from subjects.math.problem_maker import make_first_set, make_free_set, make_more

BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = BASE_DIR  # 문제집 PDF는 지금처럼 local-workbook-prototype 폴더에 둔다
DATA_DIR = BASE_DIR / "practice_data"
SETS_DIR = DATA_DIR / "sets"
PAGES_DIR = DATA_DIR / "pages"
LOG_PATH = DATA_DIR / "log.csv"
BANK_DIR = DATA_DIR / "bank"  # 문제 은행: bank/<책>/pNNN.json (scripts/build_bank.py가 미리 만들어 둔다)
LOG_FIELDS = ["time", "set_id", "kind", "book", "page", "unit", "concept", "problem_id", "type", "answer", "ok", "attempt"]

FIRST_COUNT = 6  # 은행에 없는 쪽을 열 때 AI가 그 자리에서 만드는 문제 수
MIX_COUNT = 6  # 섞어 풀기 문제 수
MORE_COUNT = 6  # "문제 더 만들기"마다 만드는 문제 수
SAME_TYPE_COUNT = 2  # "같은 유형 한 문제 더"를 눌렀는데 남은 같은 유형 문제가 없을 때 만드는 수
PREFETCH_WHEN_LEFT = 2  # 안 푼 문제가 이만큼 이하로 남으면 뒤에서 미리 더 만든다

_lock = threading.Lock()  # 세트 파일·기록 파일 쓰기는 한 번에 하나만
_inflight_lock = threading.Lock()
_inflight: dict[str, Future] = {}
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="practice")


class PracticeError(RuntimeError):
    pass


# ───────────────────────── 문제집(PDF) ─────────────────────────


def list_books() -> list[dict]:
    books = []
    for pdf in sorted(PDF_DIR.glob("*.pdf")):
        try:
            with fitz.open(str(pdf)) as doc:
                pages = doc.page_count
        except Exception:
            continue
        last = _last_page(pdf.stem)
        books.append({"name": pdf.name, "pages": pages, "last_page": last, "bank": bank_summary(pdf.name)})
    return books


def _book_path(name: str) -> Path:
    path = (PDF_DIR / name).resolve()
    if path.parent != PDF_DIR or path.suffix.lower() != ".pdf" or not path.exists():
        raise PracticeError("그 문제집 파일을 찾지 못했어요.")
    return path


def page_image_path(book: str, page: int) -> Path:
    pdf = _book_path(book)
    with fitz.open(str(pdf)) as doc:
        if not 1 <= page <= doc.page_count:
            raise PracticeError(f"이 문제집은 {doc.page_count}쪽까지 있어요.")
    return render_page(str(pdf), page, PAGES_DIR / pdf.stem / f"page_{page:03d}.png")


def _page_text(book: str, page: int) -> str:
    with fitz.open(str(_book_path(book))) as doc:
        return doc[page - 1].get_text("text")


# ───────────────────────── 문제 은행 ─────────────────────────


def _bank_book_dir(book: str) -> Path:
    return BANK_DIR / _safe(Path(book).stem)


def bank_page(book: str, page: int) -> dict | None:
    path = _bank_book_dir(book) / f"p{page:03d}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_bank_page(book: str, page: int, made: dict | None, status: str, note: str = "") -> dict:
    """status: ok(문제 있음) / skip(문제 없는 쪽) / error(실패, 다음에 다시 시도)."""
    stem = _safe(Path(book).stem)
    data = {"book": book, "page": page, "status": status, "note": note,
            "made_at": datetime.now().isoformat(timespec="seconds"),
            "unit": "", "concepts": [], "problems": [], "dropped": [], "solver": ""}
    if made:
        data.update({k: made[k] for k in ("unit", "concepts", "dropped", "solver")})
        data["problems"] = [{**p, "unit": made["unit"], "book": book, "page": page, "bank_id": f"bk_{stem}_p{page:03d}_{i + 1}"}
                            for i, p in enumerate(made["problems"])]
    path = _bank_book_dir(book) / f"p{page:03d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return data


def bank_pages(book: str | None = None) -> list[dict]:
    dirs = [_bank_book_dir(book)] if book else (sorted(BANK_DIR.iterdir()) if BANK_DIR.exists() else [])
    out = []
    for d in dirs:
        for f in sorted(d.glob("p*.json")) if d.exists() else []:
            out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def _ranges(pages: list[int]) -> str:
    parts, start = [], None
    for i, p in enumerate(pages):
        if start is None:
            start = p
        if i + 1 == len(pages) or pages[i + 1] != p + 1:
            parts.append(f"{start}" if start == p else f"{start}~{p}")
            start = None
    return ", ".join(parts)


def bank_summary(book: str) -> dict:
    pages = bank_pages(book)
    ready = sorted(p["page"] for p in pages if p["status"] == "ok")
    done = sorted(p["page"] for p in pages if p["status"] in ("ok", "skip"))
    return {
        "ready_pages": ready,
        "ranges": _ranges(ready),
        "problems": sum(len(p["problems"]) for p in pages if p["status"] == "ok"),
        "last_done": done[-1] if done else None,
        "skipped": sorted(p["page"] for p in pages if p["status"] == "skip"),
    }


def _used_bank_ids() -> set[str]:
    return {p["bank_id"] for s in all_sets() for p in s["problems"] if p.get("bank_id")}


def _bank_problems(concept: str | None = None, book: str | None = None) -> list[dict]:
    """아직 어느 세트에도 안 쓴 은행 문제들."""
    used = _used_bank_ids()
    out = []
    for page in bank_pages(book):
        if page["status"] != "ok":
            continue
        for p in page["problems"]:
            if p["bank_id"] not in used and (concept is None or p["concept"] == concept):
                out.append(p)
    return out


def next_ready_page(book: str, page: int) -> int | None:
    return next((p for p in bank_summary(book)["ready_pages"] if p > page), None)


# ───────────────────────── 세트 저장소 ─────────────────────────


def _safe(name: str) -> str:
    return re.sub(r"[^\w가-힣-]+", "_", name).strip("_")[:60] or "book"


def book_set_id(book: str, page: int) -> str:
    return f"book_{_safe(Path(book).stem)}_p{page:03d}"


def _set_path(set_id: str) -> Path:
    if not re.fullmatch(r"[\w가-힣-]+", set_id):
        raise PracticeError("잘못된 문제 세트예요.")
    return SETS_DIR / f"{set_id}.json"


def _read(set_id: str) -> dict | None:
    path = _set_path(set_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write(data: dict) -> None:
    path = _set_path(data["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def get_set(set_id: str) -> dict:
    with _lock:
        data = _read(set_id)
    if not data:
        raise PracticeError("문제 세트를 찾지 못했어요.")
    return data


def _add_problems(data: dict, problems: list[dict], solver: str, after: int | None = None) -> list[str]:
    """세트에 문제를 붙인다 (after를 주면 그 문제 바로 뒤에 끼운다). 새 문제 id 목록을 돌려준다."""
    new = []
    for p in problems:
        data["made"] = data.get("made", 0) + 1
        new.append({"unit": data["unit"], **p, "id": f"{data['id']}-{data['made']}", "result": None,
                    "solver": p.get("solver") or solver})
    if after is None:
        data["problems"].extend(new)
    else:
        data["problems"][after + 1: after + 1] = new
    return [p["id"] for p in new]


def _new_set(set_id: str, kind: str, title: str, made: dict, **extra) -> dict:
    data = {
        "id": set_id, "kind": kind, "title": title, "unit": made["unit"], "concepts": made["concepts"],
        "problems": [], "made": 0, "created": datetime.now().isoformat(timespec="seconds"), **extra,
    }
    _add_problems(data, made["problems"], made["solver"])
    return data


def open_book_page(book: str, page: int) -> dict:
    """그 쪽 세트가 있으면 그대로, 은행에 있으면 바로 꺼내고, 둘 다 없으면 AI가 지금 만든다 (10~40초)."""
    set_id = book_set_id(book, page)
    with _lock:
        data = _read(set_id)
    if data:
        return data
    banked = bank_page(book, page)
    if banked and banked["status"] == "skip":
        raise PracticeError(f"{page}쪽은 풀 문제가 없는 쪽이라 건너뛰었어요. 다른 쪽을 골라주세요.")
    if banked and banked["status"] == "ok" and banked["problems"]:
        made = {"unit": banked["unit"], "concepts": banked["concepts"], "problems": banked["problems"], "solver": banked["solver"]}
        data = _new_set(set_id, "book", f"{page}쪽 · {banked['unit']}", made, book=book, page=page)
        with _lock:
            _write(data)
        return data
    return _run_once(f"new:{set_id}", lambda: _make_book_set(set_id, book, page))


def _make_book_set(set_id: str, book: str, page: int) -> dict:
    with _lock:
        existing = _read(set_id)
    if existing:
        return existing
    from PIL import Image

    image = Image.open(page_image_path(book, page))
    made = make_first_set(image, _page_text(book, page), FIRST_COUNT)
    data = _new_set(set_id, "book", f"{page}쪽 · {made['unit']}", made, book=book, page=page)
    with _lock:
        _write(data)
    return data


def new_free_set(grade: str) -> dict:
    if grade not in ("중1", "중2", "중3"):
        raise PracticeError("학년은 중1, 중2, 중3 중에서 골라주세요.")
    made = make_free_set(grade, FIRST_COUNT)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = _new_set(f"free_{stamp}", "free", f"오늘의 문제 · {grade}", made, grade=grade)
    with _lock:
        _write(data)
    return data


def new_mix_set(book: str | None = None) -> dict:
    """은행에서 아직 안 푼 문제를 골고루 섞어서 (AI 없이 바로). 이미 공부한 쪽 문제를 먼저."""
    import random

    pool = _bank_problems(book=book)
    if not pool:
        raise PracticeError("문제 은행이 비어 있어요. run_bank.bat으로 먼저 문제 은행을 만들어주세요.")
    studied = {(s.get("book"), s.get("page")) for s in all_sets() if s["kind"] == "book"}
    seen = [p for p in pool if (p["book"], p["page"]) in studied]
    random.shuffle(seen)
    random.shuffle(pool)
    picked, ids = [], set()
    for p in seen + pool:
        if p["bank_id"] not in ids and len(picked) < MIX_COUNT:
            picked.append(p)
            ids.add(p["bank_id"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    title = f"섞어 풀기 · {Path(book).stem}" if book else "섞어 풀기"
    concepts = list({p["concept"]: {"name": p["concept"], "summary": p.get("unit", "")} for p in picked}.values())
    data = _new_set(f"mix_{stamp}", "mix", title,
                    {"unit": "섞어 풀기", "concepts": concepts, "problems": picked, "solver": "bank"}, book=book)
    with _lock:
        _write(data)
    return data


def _run_once(key: str, job) -> dict:
    """같은 작업(같은 쪽 만들기 등)을 동시에 두 번 부르지 않게: 진행 중이면 그 결과를 기다린다."""
    with _inflight_lock:
        fut = _inflight.get(key)
        if fut is None or fut.done():
            fut = _executor.submit(job)
            _inflight[key] = fut
    return fut.result()


def all_sets() -> list[dict]:
    with _lock:
        paths = sorted(SETS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if SETS_DIR.exists() else []
        return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def _last_page(stem: str) -> int | None:
    prefix = f"book_{_safe(stem)}_p"
    if not SETS_DIR.exists():
        return None
    latest = max(SETS_DIR.glob(f"{prefix}*.json"), key=lambda p: p.stat().st_mtime, default=None)
    return int(latest.stem.rsplit("_p", 1)[1]) if latest else None


# ───────────────────────── 문제 더 만들기 ─────────────────────────


def _concepts_for_more(data: dict) -> tuple[str, list[dict]]:
    return data["unit"], data["concepts"]


def add_more(set_id: str, focus: str | None = None, count: int = MORE_COUNT, after: int | None = None) -> list[str]:
    """세트에 새 문제를 더 만든다 (글자만 보내는 AI 요청 1번). 새 문제 id 목록."""
    data = get_set(set_id)
    unit, concepts = _concepts_for_more(data)
    made = make_more(unit, concepts, [p["question"] for p in data["problems"]], count, focus)
    with _lock:
        data = _read(set_id)
        if after is not None:
            after = min(after, len(data["problems"]) - 1)
        ids = _add_problems(data, made["problems"], made["solver"], after)
        _write(data)
    return ids


def prefetch_if_low(set_id: str) -> None:
    """안 푼 문제가 얼마 안 남았으면 뒤에서 미리 더 만든다. 바로 돌아온다."""
    data = get_set(set_id)
    left = sum(1 for p in data["problems"] if p["result"] is None)
    if left > PREFETCH_WHEN_LEFT:
        return
    key = f"more:{set_id}"
    with _inflight_lock:
        fut = _inflight.get(key)
        if fut is not None and not fut.done():
            return
        _inflight[key] = _executor.submit(_quiet, add_more, set_id)


def wait_for_more(set_id: str) -> None:
    """미리 만들기가 진행 중이면 끝날 때까지 기다린다 (없으면 바로 돌아옴)."""
    with _inflight_lock:
        fut = _inflight.get(f"more:{set_id}")
    if fut is not None:
        fut.result()


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass  # 미리 만들기는 실패해도 괜찮다. 화면에서 "문제 더 만들기"로 다시 시도할 수 있다.


def same_type_next(set_id: str, problem_id: str) -> str:
    """방금 문제와 같은 유형 문제를 바로 다음 순서로 가져온다. 남은 게 없으면 새로 만든다. 그 문제 id."""
    with _lock:
        data = _read(set_id)
        idx = next(i for i, p in enumerate(data["problems"]) if p["id"] == problem_id)
        concept = data["problems"][idx]["concept"]
        later = next((i for i in range(idx + 1, len(data["problems"]))
                      if data["problems"][i]["concept"] == concept and data["problems"][i]["result"] is None), None)
        if later is not None:
            data["problems"].insert(idx + 1, data["problems"].pop(later))
            _write(data)
            return data["problems"][idx + 1]["id"]
    book, page = data.get("book"), data.get("page") or 0
    candidates = _bank_problems(concept, book) or _bank_problems(concept)
    if candidates:
        pick = min(candidates, key=lambda p: (p.get("book") != book, abs((p.get("page") or 0) - page)))
        with _lock:
            data = _read(set_id)
            idx = next(i for i, p in enumerate(data["problems"]) if p["id"] == problem_id)
            new_id = _add_problems(data, [pick], pick.get("solver", "bank"), idx)[0]
            _write(data)
        return new_id
    return add_more(set_id, focus=concept, count=SAME_TYPE_COUNT, after=idx)[0]


# ───────────────────────── 채점 ─────────────────────────


def find_problem(data: dict, problem_id: str) -> dict:
    for p in data["problems"]:
        if p["id"] == problem_id:
            return p
    raise PracticeError("문제를 찾지 못했어요.")


def public_problem(p: dict) -> dict:
    """화면에 보내는 문제 (정답·풀이는 빼고. 채점은 서버에서 한다)."""
    return {**{k: p[k] for k in ("id", "concept", "type", "question", "choices", "result")}, "level": p.get("level", "")}


def reveal(p: dict) -> dict:
    cloze = build_cloze(p.get("steps") or [])
    return {
        "answer": p["answer"],
        "answer_text": answer_text(p),
        "explanation": p.get("explanation", ""),
        "cloze": cloze.lines if cloze.answers else [],
        "blanks": len(cloze.answers),
        "solution": cloze.solution,
    }


def answer_text(p: dict) -> str:
    marks = "①②③④⑤"
    if p["type"] == "multiple_choice" and p["answer"] in marks:
        i = marks.index(p["answer"])
        return f"{p['answer']} {p['choices'][i]}" if i < len(p["choices"]) else p["answer"]
    return p["answer"]


def grade(set_id: str, problem_id: str, answer: str, attempt: int) -> dict:
    """attempt 1: 틀리면 힌트, attempt 2: 틀리면 정답·풀이·빈칸. 결과는 마지막 시도에서 기록한다."""
    data = get_set(set_id)
    p = find_problem(data, problem_id)
    ok = grade_answer(p["type"], p["answer"], answer)
    _log(data, p, answer, ok, attempt)
    if ok or attempt >= 2:
        _set_result(set_id, problem_id, "right" if ok and attempt == 1 else ("retry_right" if ok else "wrong"))
    if ok:
        return {"ok": True, **reveal(p)}
    if attempt < 2:
        return {"ok": False, "hint": p.get("hint", "")}
    return {"ok": False, **reveal(p)}


def check_blanks(set_id: str, problem_id: str, typed: list[str]) -> dict:
    p = find_problem(get_set(set_id), problem_id)
    cloze = build_cloze(p.get("steps") or [])
    results = [grade_answer("short_answer", a, t or "") for a, t in zip(cloze.answers, typed)]
    return {"results": results, "answers": cloze.answers, "solution": cloze.solution, "answer_text": answer_text(p)}


def _set_result(set_id: str, problem_id: str, result: str) -> None:
    with _lock:
        data = _read(set_id)
        for p in data["problems"]:
            if p["id"] == problem_id:
                p["result"] = result
        _write(data)


def _log(data: dict, p: dict, answer: str, ok: bool, attempt: int) -> None:
    with _lock:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        new = not LOG_PATH.exists()
        with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow({
                "time": datetime.now().isoformat(timespec="seconds"), "set_id": data["id"], "kind": data["kind"],
                "book": p.get("book") or data.get("book", ""), "page": p.get("page") or data.get("page", ""),
                "unit": p.get("unit") or data["unit"],
                "concept": p["concept"], "problem_id": p["id"], "type": p["type"], "answer": answer,
                "ok": int(ok), "attempt": attempt,
            })


def _read_log() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    with _lock, LOG_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ───────────────────────── 복습 · 기록 ─────────────────────────


MASTERED_STREAK = 3  # 틀린 유형은 최근 이만큼 연속으로 한 번에 맞혀야 복습 목록에서 빠진다


def weak_concepts(limit: int = 4) -> list[dict]:
    """복습이 필요한 유형 (최근에 틀린 순). 최근 MASTERED_STREAK문제 중 하나라도
    한 번에 못 맞힌(틀렸거나 다시 풀어서 맞힌) 게 있으면 복습 대상."""
    finals: dict[tuple[str, str], list[tuple[str, bool]]] = {}
    for row in _read_log():
        if row["ok"] == "1" or row["attempt"] == "2":  # 그 문제의 마지막 결과 줄
            first_try_right = row["ok"] == "1" and row["attempt"] == "1"
            finals.setdefault((row["unit"], row["concept"]), []).append((row["time"], first_try_right))
    weak = []
    for (unit, concept), results in finals.items():
        recent = results[-MASTERED_STREAK:]
        if not all(ok for _, ok in recent):
            last_miss = max(t for t, ok in results if not ok)
            weak.append({"unit": unit, "concept": concept, "wrong": sum(1 for _, ok in results if not ok), "time": last_miss})
    weak.sort(key=lambda v: v["time"], reverse=True)
    return weak[:limit]


def new_review_set() -> dict:
    weak = weak_concepts()
    if not weak:
        raise PracticeError("아직 틀린 유형이 없어요. 문제를 풀다 보면 여기에 모여요.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    picked = []
    for w in weak:
        same = [p for p in _bank_problems(w["concept"]) if p.get("unit") == w["unit"]] or _bank_problems(w["concept"])
        picked += same[:2]
    if len(picked) >= 3:  # 은행에 충분히 있으면 바로
        concepts = [{"name": w["concept"], "summary": w["unit"]} for w in weak]
        data = _new_set(f"review_{stamp}", "review", "틀린 유형 복습",
                        {"unit": "틀린 유형 복습", "concepts": concepts, "problems": picked, "solver": "bank"})
        with _lock:
            _write(data)
        return data
    summaries = {}
    for s in all_sets():
        for c in s.get("concepts", []):
            summaries[(s["unit"], c["name"])] = c.get("summary", "")
    concepts = [{"name": w["concept"], "summary": f"{w['unit']} 단원. {summaries.get((w['unit'], w['concept']), '')}"}
                for w in weak]
    made = make_more("틀린 유형 복습", concepts, [], max(MORE_COUNT, 2 * len(concepts)))
    unit_of = {w["concept"]: w["unit"] for w in weak}
    for p in made["problems"]:
        p["unit"] = unit_of.get(p["concept"], weak[0]["unit"])
    data = _new_set(f"review_{stamp}", "review", "틀린 유형 복습",
                    {**made, "unit": "틀린 유형 복습", "concepts": concepts})
    with _lock:
        _write(data)
    return data


def stats() -> dict:
    rows = _read_log()
    today = datetime.now().strftime("%Y-%m-%d")
    finals = [r for r in rows if r["ok"] == "1" or r["attempt"] == "2"]  # 문제마다 마지막 결과 1줄
    first_try = [r for r in rows if r["attempt"] == "1"]
    by_concept: dict[tuple[str, str], dict] = {}
    for r in first_try:
        c = by_concept.setdefault((r["unit"], r["concept"]), {"unit": r["unit"], "concept": r["concept"], "solved": 0, "right": 0})
        c["solved"] += 1
        c["right"] += int(r["ok"] == "1")
    concepts = sorted(by_concept.values(), key=lambda c: (c["right"] / c["solved"], -c["solved"]))
    today_first = [r for r in first_try if r["time"].startswith(today)]
    return {
        "today_solved": sum(1 for r in finals if r["time"].startswith(today)),
        "today_accuracy": (sum(int(r["ok"] == "1") for r in today_first) / len(today_first)) if today_first else None,
        "total_solved": len(finals),
        "total_accuracy": (sum(int(r["ok"] == "1") for r in first_try) / len(first_try)) if first_try else None,
        "concepts": concepts[:12],
        "weak": weak_concepts(),
    }


def recent_sets(limit: int = 6) -> list[dict]:
    out = []
    for s in all_sets()[:limit]:
        left = sum(1 for p in s["problems"] if p["result"] is None)
        out.append({"id": s["id"], "title": s["title"], "kind": s["kind"], "left": left, "total": len(s["problems"])})
    return out
