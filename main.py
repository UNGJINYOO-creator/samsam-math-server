"""마클리(삼삼수학) 서버: 문제집 쪽 개념으로 새 문제 출제 + 손글씨 풀이 + 채점 + 복습 + 기록.

집 PC에서 켜고, 같은 와이파이의 태블릿 브라우저로 접속한다.
    python -m uvicorn main:app --host 0.0.0.0 --port 8000      (또는 run_maklee.bat 더블클릭)

문제 만들기·채점 엔진은 local-workbook-prototype 폴더에 있다 (문제집 PDF도 그 폴더에 둔다).
AI 키는 코드에 쓰지 않고 환경변수로만 받는다:  setx GEMINI_API_KEY "키"  (보조 AI: NVIDIA_API_KEY)
"""
from __future__ import annotations

import base64
import io
import socket
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "local-workbook-prototype"))

import practice_engine as engine  # noqa: E402
from subjects.math.gemini_client import GeminiError, QuotaExceededError, quota_blocked_seconds  # noqa: E402
from subjects.math.handwriting import explain_mistake  # noqa: E402
from subjects.math.nvidia_client import nvidia_available  # noqa: E402

app = FastAPI(title="마클리")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.exception_handler(engine.PracticeError)
def _practice_error(_req, e: engine.PracticeError):
    return JSONResponse({"error": str(e)}, status_code=400)


@app.exception_handler(QuotaExceededError)
def _quota_error(_req, e: QuotaExceededError):
    return JSONResponse({"error": str(e), "quota": True}, status_code=429)


@app.exception_handler(GeminiError)
def _ai_error(_req, e: GeminiError):
    return JSONResponse({"error": str(e)}, status_code=502)


@app.on_event("startup")
def _show_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        print(f"\n  태블릿에서 접속: http://{ip}:8000\n  이 PC에서 접속: http://localhost:8000\n", flush=True)
    except OSError:
        pass


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


# ───────────────────────── 홈 ─────────────────────────


@app.get("/api/home")
def home():
    blocked = quota_blocked_seconds()
    return {
        "books": engine.list_books(),
        "recent": engine.recent_sets(),
        "weak": engine.weak_concepts(),
        "quota_minutes": int(blocked // 60) + 1 if blocked > 0 else 0,
        "backup_ai": nvidia_available(),
    }


@app.get("/api/books/{book}/page/{page}.png")
def page_image(book: str, page: int):
    return FileResponse(engine.page_image_path(book, page), media_type="image/png")


# ───────────────────────── 문제 세트 ─────────────────────────


class BookPage(BaseModel):
    book: str
    page: int


class Grade(BaseModel):
    grade: str


def _set_view(data: dict) -> dict:
    return {
        "id": data["id"], "kind": data["kind"], "title": data["title"], "unit": data["unit"],
        "book": data.get("book"), "page": data.get("page"),
        "concepts": data["concepts"],
        "problems": [engine.public_problem(p) for p in data["problems"]],
    }


@app.post("/api/sets/book")
def open_book_page(req: BookPage):
    return _set_view(engine.open_book_page(req.book, req.page))


@app.post("/api/sets/free")
def new_free(req: Grade):
    return _set_view(engine.new_free_set(req.grade))


@app.post("/api/sets/review")
def new_review():
    return _set_view(engine.new_review_set())


@app.get("/api/sets/{set_id}")
def get_set(set_id: str):
    return _set_view(engine.get_set(set_id))


class More(BaseModel):
    wait_prefetch: bool = True


@app.post("/api/sets/{set_id}/more")
def more(set_id: str, req: More):
    """문제 더: 뒤에서 미리 만들던 게 있으면 그걸 기다리고, 그래도 안 푼 문제가 없으면 새로 만든다."""
    if req.wait_prefetch:
        engine.wait_for_more(set_id)
    data = engine.get_set(set_id)
    if not any(p["result"] is None for p in data["problems"]):
        engine.add_more(set_id)
    return _set_view(engine.get_set(set_id))


class SameType(BaseModel):
    problem_id: str


@app.post("/api/sets/{set_id}/same-type")
def same_type(set_id: str, req: SameType):
    new_id = engine.same_type_next(set_id, req.problem_id)
    return {"next_id": new_id, **_set_view(engine.get_set(set_id))}


# ───────────────────────── 채점 ─────────────────────────


class Answer(BaseModel):
    problem_id: str
    answer: str
    attempt: int = 1


@app.post("/api/sets/{set_id}/grade")
def grade(set_id: str, req: Answer):
    if not req.answer.strip():
        raise HTTPException(400, "답을 입력해주세요.")
    return engine.grade(set_id, req.problem_id, req.answer, req.attempt)


class Blanks(BaseModel):
    problem_id: str
    typed: list[str]


@app.post("/api/sets/{set_id}/blanks")
def blanks(set_id: str, req: Blanks):
    return engine.check_blanks(set_id, req.problem_id, req.typed)


class Handwriting(BaseModel):
    problem_id: str
    image: str  # data:image/png;base64,...
    mine: str = ""


@app.post("/api/sets/{set_id}/handwriting")
def handwriting(set_id: str, req: Handwriting):
    """틀린 뒤 '내 풀이 봐줘': 손글씨 풀이 사진을 AI에게 보내서 어디서 틀렸는지 듣는다 (AI 요청 1번)."""
    from PIL import Image

    p = engine.find_problem(engine.get_set(set_id), req.problem_id)
    raw = base64.b64decode(req.image.split(",", 1)[-1])
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    return {"feedback": explain_mistake(p, image, req.mine)}


# ───────────────────────── 기록 ─────────────────────────


@app.get("/api/stats")
def stats():
    return engine.stats()
