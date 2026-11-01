"""즉석 풀이 모드 (Streamlit) — PDF를 열고, 고른 페이지부터 한 장씩 처리하면서 바로 푼다.

책 전체를 미리 나누지 않는다. 지금 보고 있는 페이지 하나만 그 자리에서 이미지로 만들고,
그 페이지 안의 문제만 찾아서 자른다. 그래서
  - 앞쪽 설명 페이지는 아예 열지 않으니 문제로 잡히지 않고,
  - 한 페이지에서 번호 인식이 틀려도 다른 페이지로 번지지 않으며,
  - 수백 페이지짜리 책도 기다림 없이 바로 시작할 수 있다.

문제 찾기 순서: 글자 정보로 번호 찾기(무료, 즉시) -> 못 찾으면 AI가 페이지 이미지를 보고 찾기.
정답은 문제를 처음 제출할 때 AI가 그 문제만 풀어서 answers.json에 저장해두고 재사용한다.

실행:
    streamlit run live_app.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf as fitz
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ANSWERS_FILENAME, LOG_FILENAME, PAGES_DIRNAME, QUESTIONS_DIRNAME
from core.answer_parser import load_answers, save_answers
from core.grading import grade_answer
from core.page_questions import crop_questions, detect_questions_by_text, render_page
from core.session_log import append_log, unit_review_summary
from subjects.math.ai_layout import AILayoutError, detect_questions_by_ai
from subjects.math.ai_solver import AISolveError, solve_question
from subjects.math.hint_explain import HintExplainError, generate_explanation, generate_hint
from ui_theme import inject_centered_card_width, inject_toss_style

try:
    from streamlit_drawable_canvas import st_canvas

    HAS_CANVAS = True
except ImportError:
    HAS_CANVAS = False

PAGE_CACHE_DIRNAME = "page_cache"
POSITION_FILE = ".position.json"


# ───────────────────────── 파일/경로 ─────────────────────────


def find_pdfs(root: Path) -> list[Path]:
    return sorted(root.glob("*.pdf"))


def project_dir_for_pdf(pdf_path: Path) -> Path:
    return pdf_path.parent / f"{pdf_path.stem}_bypage"


def page_count(pdf_path: Path) -> int:
    with fitz.open(str(pdf_path)) as doc:
        return len(doc)


def page_png_path(project_dir: Path, page_no: int) -> Path:
    return project_dir / PAGES_DIRNAME / f"page_{page_no:03d}.png"


def load_position(project_dir: Path) -> dict | None:
    path = project_dir / POSITION_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def save_position(project_dir: Path, page: int, idx: int) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / POSITION_FILE).write_text(json.dumps({"page": page, "idx": idx}), encoding="utf-8")


def answer_key(page: int, idx: int) -> str:
    return f"p{page}-{idx + 1}"


# ───────────────────────── 페이지 하나 처리 ─────────────────────────


def load_page(project_dir: Path, pdf_path: Path, page_no: int, force_ai: bool = False) -> dict:
    """이 페이지의 문제 목록. 한 번 처리한 페이지는 page_cache에 저장해두고 재사용한다.

    반환: {"method": "text"|"ai"|"none", "questions": [{"no": int, "image": str}], "error": str|None}
    """
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
            with st.spinner("AI가 이 페이지에서 문제를 찾는 중..."):
                questions = detect_questions_by_ai(page_png, rect.width, rect.height)
            method = "ai" if questions else "none"
        except AILayoutError as e:
            error = str(e)
        except Exception as e:
            error = f"AI로 문제를 찾는 중 오류가 발생했어요: {e}"

    if error is not None:
        # AI 호출 자체가 실패(키 없음 등)했으면 기존 결과를 지우지 않고 그대로 둔다.
        # 캐시도 안 남기므로, 원인을 고친 뒤 다시 열면 재시도된다.
        if cache_path.exists():
            return {**json.loads(cache_path.read_text(encoding="utf-8")), "error": error}
        return {"method": "none", "questions": [], "error": error}

    questions_dir = project_dir / QUESTIONS_DIRNAME
    for old in questions_dir.glob(f"p{page_no:03d}_q*.png"):
        old.unlink()
    cropped = crop_questions(page_png, questions, questions_dir, page_no)

    # 문제를 다시 찾으면 페이지 안 순서가 바뀔 수 있으니, 이 페이지에서 AI가 풀어둔 정답은 지운다
    answers_path = project_dir / ANSWERS_FILENAME
    answers = load_answers(answers_path)
    stale = [k for k in answers if k.startswith(f"p{page_no}-")]
    if stale:
        for k in stale:
            del answers[k]
        save_answers(answers_path, answers)

    data = {
        "method": method,
        "questions": [{"no": q.no, "image": str(path)} for q, path in cropped],
        "error": None,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def ensure_answer(project_dir: Path, key: str, img_path: Path) -> dict:
    """이미 AI가 풀어둔 적 있으면 그걸 쓰고, 처음 보는 문제만 지금 AI를 부른다."""
    answers_path = project_dir / ANSWERS_FILENAME
    answers = load_answers(answers_path)
    cached = answers.get(key)
    if cached and cached.get("answer"):
        return cached

    result = solve_question(img_path)
    entry = {
        "answer": result["answer"],
        "type": result["type"],
        "shape_dependent": False,
        "explanation": result["explanation"],
    }
    answers[key] = entry
    save_answers(answers_path, answers)
    return entry


# ───────────────────────── 세션 상태 ─────────────────────────


def reset_question_state():
    st.session_state.stage = "answering"  # answering -> hint -> revealed / essay_check
    st.session_state.hint_text = None
    st.session_state.explanation_text = None
    st.session_state.last_wrong_1 = None
    st.session_state.last_wrong_2 = None
    st.session_state.pending_essay_answer = None
    st.session_state.essay_explanation = ""


def go_to(project_dir: Path, page: int, idx: int = 0):
    st.session_state.page = page
    st.session_state.idx = idx
    st.session_state.started = True
    save_position(project_dir, page, idx)
    reset_question_state()
    st.rerun()


def select_pdf(pdf_name: str, project_dir: Path):
    st.session_state.pdf_name = pdf_name
    pos = load_position(project_dir)
    if pos:
        st.session_state.page = pos["page"]
        st.session_state.idx = pos["idx"]
        st.session_state.started = True
    else:
        st.session_state.page = 1
        st.session_state.idx = 0
        st.session_state.started = False
    reset_question_state()


# ───────────────────────── 화면 ─────────────────────────


def render_sidebar() -> tuple[Path, Path, int] | None:
    st.sidebar.header("📂 문제집 선택")
    root = st.sidebar.text_input("PDF가 있는 폴더", value=".")
    pdfs = find_pdfs(Path(root))
    if not pdfs:
        st.sidebar.warning("이 폴더에 PDF 파일이 없습니다.")
        return None

    names = [p.name for p in pdfs]
    chosen = st.sidebar.selectbox("PDF 파일", names)
    pdf_path = pdfs[names.index(chosen)]
    project_dir = project_dir_for_pdf(pdf_path)
    total_pages = page_count(pdf_path)

    if st.session_state.get("pdf_name") != chosen:
        select_pdf(chosen, project_dir)

    if st.session_state.started:
        st.sidebar.markdown(f"**지금 {st.session_state.page}쪽** / 전체 {total_pages}쪽")
        col1, col2 = st.sidebar.columns(2)
        if col1.button("◀ 이전 쪽", use_container_width=True, disabled=st.session_state.page <= 1):
            go_to(project_dir, st.session_state.page - 1)
        if col2.button("다음 쪽 ▶", use_container_width=True, disabled=st.session_state.page >= total_pages):
            go_to(project_dir, st.session_state.page + 1)

        jump = st.sidebar.number_input("다른 페이지로 이동", 1, total_pages, st.session_state.page)
        if st.sidebar.button("이 페이지로 이동", use_container_width=True):
            go_to(project_dir, int(jump))

        summary = unit_review_summary(project_dir / LOG_FILENAME, pdf_path.stem)
        if summary["total"]:
            st.sidebar.caption(
                f"지금까지 {summary['total']}문제 · 첫 시도 정답률 {summary['accuracy'] * 100:.0f}%"
            )

    return pdf_path, project_dir, total_pages


def render_start_screen(pdf_path: Path, project_dir: Path, total_pages: int):
    st.subheader("어느 페이지부터 풀까요?")
    st.caption("표지·목차·개념 설명 말고, 실제 문제가 시작하는 페이지를 골라주세요.")
    start = st.number_input("시작 페이지", 1, total_pages, 1)
    preview = render_page(str(pdf_path), int(start), page_png_path(project_dir, int(start)))
    st.image(str(preview), caption=f"{int(start)}쪽 미리보기")
    if st.button("이 페이지부터 풀기", type="primary", use_container_width=True):
        go_to(project_dir, int(start))


def render_scratch_space(key_suffix: str):
    st.caption("✏️ 풀이 공간 (채점에는 안 쓰여요, 자유롭게 써보세요)")
    if HAS_CANVAS:
        st_canvas(
            fill_color="rgba(255,255,255,0)",
            stroke_width=3,
            stroke_color="#191F28",
            background_color="#FFFFFF",
            height=200,
            width=640,
            drawing_mode="freedraw",
            key=f"canvas_{key_suffix}",
        )
    else:
        st.text_area("풀이 공간", key=f"scratch_{key_suffix}", height=100, label_visibility="collapsed")


def render_question(pdf_path: Path, project_dir: Path, total_pages: int):
    page, idx = st.session_state.page, st.session_state.idx
    unit = pdf_path.stem
    log_path = project_dir / LOG_FILENAME

    if page > total_pages:
        st.success("🎉 마지막 페이지까지 다 풀었어요!")
        return

    data = load_page(project_dir, pdf_path, page)
    page_png = page_png_path(project_dir, page)

    if data.get("error"):
        st.error(data["error"])

    questions = data["questions"]
    if not questions:
        st.info(f"{page}쪽에서 문제를 찾지 못했어요. 문제가 없는 페이지면 다음 쪽으로 넘어가세요.")
        st.image(str(page_png))
        col1, col2 = st.columns(2)
        if col1.button("AI로 다시 찾기", use_container_width=True):
            load_page(project_dir, pdf_path, page, force_ai=True)
            go_to(project_dir, page)
        if col2.button("다음 쪽 ▶", type="primary", use_container_width=True):
            go_to(project_dir, page + 1)
        return

    if idx >= len(questions):
        go_to(project_dir, page + 1)
        return

    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(f"직전 문제: {flash}")

    q = questions[idx]
    img_path = Path(q["image"])
    key = answer_key(page, idx)

    label = f"{q['no']}번" if q["no"] else f"{idx + 1}번째 문제"
    st.progress(idx / len(questions), text=f"{page}쪽 · {idx + 1}/{len(questions)}")
    st.subheader(f"{page}쪽 {label}")
    st.image(str(img_path))

    with st.expander("전체 페이지 보기 / 문제가 이상하게 잘렸어요"):
        st.image(str(page_png))
        how = "글자 정보로 찾음" if data["method"] == "text" else "AI가 찾음"
        st.caption(f"이 페이지 문제는 {how}. 잘못 잘렸으면 AI로 다시 찾아보세요.")
        if st.button("AI로 이 페이지 문제 다시 찾기", key=f"redetect_{page}"):
            load_page(project_dir, pdf_path, page, force_ai=True)
            go_to(project_dir, page)

    render_scratch_space(f"{page}_{idx}")
    render_answer_flow(project_dir, unit, log_path, key, img_path, page, idx)


CHOICE_MARKS = ["①", "②", "③", "④", "⑤"]


def answer_input(question_type: str, key_suffix: str, retry: bool = False) -> str | None:
    """객관식이면 ①~⑤ 버튼(누르는 즉시 제출), 그 외는 입력창 + 제출 버튼.
    제출된 값을 돌려주고, 아직 제출 안 했으면 None."""
    if question_type == "multiple_choice":
        st.caption("다시 골라보세요" if retry else "정답을 골라주세요")
        cols = st.columns(len(CHOICE_MARKS))
        for i, mark in enumerate(CHOICE_MARKS):
            if cols[i].button(mark, key=f"mc_{key_suffix}_{i}", use_container_width=True):
                return str(i + 1)
        return None

    value = st.text_input("다시 답 입력" if retry else "최종 답을 입력하세요", key=f"txt_{key_suffix}")
    if st.button("제출 (2차)" if retry else "제출", key=f"btn_{key_suffix}", type="primary") and value:
        return value
    return None


def render_answer_flow(project_dir: Path, unit: str, log_path: Path, key: str, img_path: Path, page: int, idx: int):
    stage = st.session_state.stage

    # ── 1차 시도 ──
    if stage == "answering":
        # 객관식인지 알아야 ①~⑤ 버튼을 보여줄 수 있어서, 문제를 띄우자마자 AI가 먼저 읽는다.
        # (정답은 화면에 안 나오고, AI 호출은 여전히 문제당 1번 - 제출 때 하던 걸 앞당긴 것뿐)
        try:
            if key in load_answers(project_dir / ANSWERS_FILENAME):
                entry = ensure_answer(project_dir, key, img_path)
            else:
                with st.spinner("문제 읽는 중..."):
                    entry = ensure_answer(project_dir, key, img_path)
        except AISolveError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"문제를 읽는 중 오류가 발생했어요: {e}")
            return

        submitted = answer_input(entry["type"], f"1_{key}")
        if submitted:
            if entry["type"] == "essay":
                st.session_state.pending_essay_answer = submitted
                st.session_state.essay_explanation = entry.get("explanation", "")
                st.session_state.stage = "essay_check"
                st.rerun()

            ok = grade_answer(entry["type"], entry["answer"], submitted)
            append_log(log_path, unit, key, entry["type"], submitted, ok, is_retry=False)
            if ok:
                st.session_state.flash = "정답입니다! ✅"
                go_to(project_dir, page, idx + 1)
            st.session_state.last_wrong_1 = submitted
            st.session_state.stage = "hint"
            st.rerun()
        return

    # ── 서술형: AI 풀이 보고 직접 O/X ──
    if stage == "essay_check":
        st.info("서술형 문제예요. AI가 정리한 풀이를 보고 직접 O/X를 선택하세요.")
        st.write(f"**입력하신 답:** {st.session_state.pending_essay_answer}")
        with st.expander("AI 풀이 보기"):
            st.write(st.session_state.essay_explanation)
        col_o, col_x = st.columns(2)
        if col_o.button("맞았어요 (O)", key=f"essay_o_{key}", type="primary", use_container_width=True):
            append_log(log_path, unit, key, "essay", "manual_O", True, is_retry=False)
            go_to(project_dir, page, idx + 1)
        if col_x.button("틀렸어요 (X)", key=f"essay_x_{key}", use_container_width=True):
            append_log(log_path, unit, key, "essay", "manual_X", False, is_retry=False)
            go_to(project_dir, page, idx + 1)
        return

    entry = load_answers(project_dir / ANSWERS_FILENAME)[key]

    # ── 2차 시도 (힌트) ──
    if stage == "hint":
        st.error("오답이에요. 힌트를 보고 한 번 더 풀어볼까요?")
        if st.session_state.hint_text is None:
            with st.spinner("힌트 만드는 중..."):
                try:
                    st.session_state.hint_text = generate_hint(img_path)
                except HintExplainError as e:
                    st.session_state.hint_text = f"(힌트를 불러올 수 없어요: {e})"
                except Exception as e:
                    st.session_state.hint_text = f"(힌트를 불러오는 중 오류가 발생했어요: {e})"
        st.info(f"💡 **힌트**\n\n{st.session_state.hint_text}")

        submitted2 = answer_input(entry["type"], f"2_{key}", retry=True)
        if submitted2:
            ok = grade_answer(entry["type"], entry["answer"], submitted2)
            append_log(log_path, unit, key, entry["type"], submitted2, ok, is_retry=True)
            if ok:
                st.session_state.flash = "정답입니다! 이번엔 이해했네요 🎉"
                go_to(project_dir, page, idx + 1)
            st.session_state.last_wrong_2 = submitted2
            st.session_state.stage = "revealed"
            st.rerun()
        return

    # ── 정답 + 해설 ──
    if stage == "revealed":
        st.error("이번에도 오답이에요. 정답과 풀이를 확인해보세요.")
        st.markdown(f"### 정답: `{entry['answer']}`")
        st.markdown("**📝 풀이**")
        if st.session_state.explanation_text is None:
            st.session_state.explanation_text = entry.get("explanation") or ""
            if not st.session_state.explanation_text:
                with st.spinner("해설 만드는 중..."):
                    try:
                        st.session_state.explanation_text = generate_explanation(
                            img_path, st.session_state.last_wrong_2 or "", entry["answer"]
                        )
                    except Exception as e:
                        st.session_state.explanation_text = f"(해설을 불러올 수 없어요: {e})"
        st.write(st.session_state.explanation_text)
        if st.button("다음 문제로", key=f"next_{key}", type="primary"):
            go_to(project_dir, page, idx + 1)


def main():
    st.set_page_config(page_title="즉석 풀이 모드", layout="centered")
    inject_toss_style()
    inject_centered_card_width()
    st.title("📘 PDF 열고 바로 풀기")

    result = render_sidebar()
    if result is None:
        return
    pdf_path, project_dir, total_pages = result

    if not st.session_state.started:
        render_start_screen(pdf_path, project_dir, total_pages)
    else:
        render_question(pdf_path, project_dir, total_pages)


if __name__ == "__main__":
    main()
