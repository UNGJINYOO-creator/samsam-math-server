"""즉석 풀이 모드 (Streamlit) — 고른 페이지부터 한 장씩 처리하면서 바로 푼다. (화면 시안 B: 좌우 분할)

화면: 왼쪽에 문제 + 보기 목록 + 채점하기, 오른쪽 전체가 풀이 공간. PDF 선택/페이지 이동 같은
설정은 접혀 있는 사이드바에 둔다.

채점 흐름:
  보기 선택(또는 답 입력) -> 채점하기
  1번 틀림 -> 힌트 보고 다시
  2번 틀림 -> 풀이식 빈칸 채우기 -> 확인하면 정답 공개
문제가 뜨면 그 문제와 다음 문제들을 뒤에서 미리 풀어두므로(live_engine), 채점은 대부분 즉시 끝난다.

실행:
    streamlit run live_app.py --server.address 0.0.0.0
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import pymupdf as fitz
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import LOG_FILENAME
from core.cloze import BLANK_MARKS, build_cloze
from core.grading import grade_answer
from core.page_questions import render_page
from core.session_log import append_log, unit_review_summary
from live_engine import (
    answer_key,
    cached_page,
    ensure_answer,
    get_answer,
    page_png_path,
    prefetch_around,
    prepare_page,
    set_answer_field,
)
from subjects.math.hint_explain import generate_hint
from ui_theme import inject_toss_style

try:
    from streamlit_drawable_canvas import st_canvas

    HAS_CANVAS = True
except ImportError:
    HAS_CANVAS = False

POSITION_FILE = ".position.json"
CHOICE_MARKS = ["①", "②", "③", "④", "⑤"]

LAYOUT_CSS = """
<style>
[data-testid="stAppViewContainer"] { background: #F2F4F6; }
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stAppDeployButton"], #MainMenu, footer { display: none !important; }
.block-container { padding-top: 1.4rem; padding-bottom: 1.2rem; max-width: 1400px; }

.st-key-leftpane { background: #FFFFFF; border-radius: 22px; padding: 18px 22px 22px; }
.st-key-rightpane { background: #FFFFFF; border-radius: 22px; padding: 14px; }

.where { font-size: 15px; color: #4E5968; font-weight: 600; }
.where b { color: #191F28; }

/* 보기 목록: 시안 B처럼 한 줄씩, 고른 보기는 옅은 파랑 + 파란 테두리 */
[class*="st-key-choice_"] .stButton > button {
  justify-content: flex-start; min-height: 56px; font-size: 19px; border-radius: 14px;
  border: 1.5px solid #E5E8EB; background: #FFFFFF; color: #191F28; box-shadow: none;
}
[class*="st-key-choice_"] .stButton > button[kind="primary"],
[class*="st-key-choice_"] .stButton > button[data-testid="stBaseButton-primary"] {
  background: #E8F3FF; border-color: #3182F6; color: #3182F6; font-weight: 700;
}
[class*="st-key-grade_"] .stButton > button { min-height: 58px; font-size: 19px; font-weight: 700; border-radius: 16px; }

.fb { border-radius: 14px; padding: 14px 16px; font-size: 16px; line-height: 1.55; margin: 2px 0 8px; }
.fb.wrong { background: #FFF0F0; color: #E42939; }
.fb.right { background: #E6F7EF; color: #03A35F; }
.fb.reveal { background: #E8F3FF; color: #191F28; }
.fb b { font-weight: 700; }
.fb .sub { display: block; color: #4E5968; margin-top: 4px; font-size: 15px; }

.steps { background: #F9FAFB; border-radius: 14px; padding: 14px 18px; font-size: 19px; line-height: 1.9; margin-bottom: 8px; }
.steps .mk { color: #3182F6; font-weight: 700; }
.steps .ok { color: #03A35F; font-weight: 700; }
.steps .no { color: #E42939; font-weight: 700; }
</style>
"""


# ───────────────────────── 파일/경로 ─────────────────────────


def find_pdfs(root: Path) -> list[Path]:
    return sorted(root.glob("*.pdf"))


def project_dir_for_pdf(pdf_path: Path) -> Path:
    return pdf_path.parent / f"{pdf_path.stem}_bypage"


def page_count(pdf_path: Path) -> int:
    with fitz.open(str(pdf_path)) as doc:
        return len(doc)


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


def load_page(project_dir: Path, pdf_path: Path, page_no: int, force_ai: bool = False) -> dict:
    """처음 여는 페이지만 기다림 표시를 하고, 이미 준비된 페이지(미리 풀기가 준비해둔 것 포함)는 바로 돌려준다."""
    if not force_ai:
        cached = cached_page(project_dir, page_no)
        if cached:
            return cached
    with st.spinner("이 페이지에서 문제를 찾는 중..."):
        return prepare_page(project_dir, pdf_path, page_no, force_ai=force_ai)


# ───────────────────────── 세션 상태 ─────────────────────────


def reset_question_state():
    st.session_state.stage = "try1"  # try1 -> try2 -> cloze / right / revealed / essay_check
    st.session_state.selected = None
    st.session_state.last_answer = ""
    st.session_state.cloze_typed = None  # 빈칸 확인하기를 누른 순간의 입력값 (입력칸이 사라진 뒤에도 유지)


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
    st.session_state.page = pos["page"] if pos else 1
    st.session_state.idx = pos["idx"] if pos else 0
    st.session_state.started = bool(pos)
    reset_question_state()


# ───────────────────────── 사이드바(설정) ─────────────────────────


def render_sidebar() -> tuple[Path, Path, int] | None:
    st.sidebar.header("문제집")
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
        col1, col2 = st.sidebar.columns(2)
        if col1.button("◀ 이전 쪽", width="stretch", disabled=st.session_state.page <= 1):
            go_to(project_dir, st.session_state.page - 1)
        if col2.button("다음 쪽 ▶", width="stretch", disabled=st.session_state.page >= total_pages):
            go_to(project_dir, st.session_state.page + 1)
        jump = st.sidebar.number_input("페이지로 이동", 1, total_pages, st.session_state.page)
        if st.sidebar.button("이동", width="stretch"):
            go_to(project_dir, int(jump))

        summary = unit_review_summary(project_dir / LOG_FILENAME, pdf_path.stem)
        if summary["total"]:
            st.sidebar.caption(f"지금까지 {summary['total']}문제 · 첫 시도 정답률 {summary['accuracy'] * 100:.0f}%")

    return pdf_path, project_dir, total_pages


# ───────────────────────── 화면 ─────────────────────────


def render_start_screen(pdf_path: Path, project_dir: Path, total_pages: int):
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.subheader("어느 페이지부터 풀까요?")
        st.caption("표지·목차·개념 설명 말고, 실제 문제가 시작하는 페이지를 골라주세요.")
        start = st.number_input("시작 페이지", 1, total_pages, 1)
        preview = render_page(str(pdf_path), int(start), page_png_path(project_dir, int(start)))
        st.image(str(preview), caption=f"{int(start)}쪽 미리보기")
        if st.button("이 페이지부터 풀기", type="primary", width="stretch"):
            go_to(project_dir, int(start))


def render_scratch_space(key_suffix: str):
    if HAS_CANVAS:
        tool = st.radio("도구", ["펜", "지우개"], horizontal=True, label_visibility="collapsed", key=f"tool_{key_suffix}")
        st_canvas(
            fill_color="rgba(255,255,255,0)",
            stroke_width=3 if tool == "펜" else 28,
            stroke_color="#24324A" if tool == "펜" else "#FFFFFF",
            background_color="#FFFFFF",
            height=680,
            width=640,
            drawing_mode="freedraw",
            update_streamlit=False,  # 그림 내용은 채점에 안 쓰므로, 획마다 화면을 다시 그리지 않게
            key=f"canvas_{key_suffix}",
        )
    else:
        st.text_area("풀이 공간", key=f"scratch_{key_suffix}", height=640, label_visibility="collapsed",
                     placeholder="여기에 풀이를 써보세요 (손글씨 풀이 공간은 pip install streamlit-drawable-canvas)")


def feedback(kind: str, title: str, sub: str = ""):
    sub_html = f'<span class="sub">{html.escape(sub)}</span>' if sub else ""
    st.markdown(f'<div class="fb {kind}"><b>{html.escape(title)}</b>{sub_html}</div>', unsafe_allow_html=True)


def choice_label(i: int, entry: dict | None) -> str:
    choices = (entry or {}).get("choices") or []
    return f"{CHOICE_MARKS[i]}   {choices[i]}" if i < len(choices) else CHOICE_MARKS[i]


def answer_text(entry: dict) -> str:
    """정답을 "③ x = 4"처럼 보기 내용까지 붙여서."""
    ans = entry["answer"]
    if entry["type"] == "multiple_choice" and ans in CHOICE_MARKS:
        i = CHOICE_MARKS.index(ans)
        choices = entry.get("choices") or []
        return f"{ans} {choices[i]}" if i < len(choices) else ans
    return ans


def render_question(pdf_path: Path, project_dir: Path, total_pages: int):
    page, idx = st.session_state.page, st.session_state.idx

    if page > total_pages:
        st.success("마지막 페이지까지 다 풀었어요!")
        return

    data = load_page(project_dir, pdf_path, page)
    if data.get("error"):
        st.error(data["error"])
    questions = data["questions"]

    if not questions:
        _, mid, _ = st.columns([1, 2, 1])
        with mid:
            st.info(f"{page}쪽에서 문제를 찾지 못했어요. 문제가 없는 페이지면 다음 쪽으로 넘어가세요.")
            st.image(str(page_png_path(project_dir, page)))
            c1, c2 = st.columns(2)
            if c1.button("AI로 다시 찾기", width="stretch"):
                load_page(project_dir, pdf_path, page, force_ai=True)
                go_to(project_dir, page)
            if c2.button("다음 쪽", type="primary", width="stretch"):
                go_to(project_dir, page + 1)
        return

    if idx >= len(questions):
        go_to(project_dir, page + 1)
        return

    # 문제가 바뀔 때 한 번만: 지금 문제 + 다음 문제들을 뒤에서 미리 푼다
    here = (str(project_dir), page, idx)
    if st.session_state.get("prefetched_for") != here:
        prefetch_around(project_dir, pdf_path, page, idx, total_pages)
        st.session_state.prefetched_for = here

    left, right = st.columns([0.43, 0.57], gap="medium")
    with left:
        with st.container(key="leftpane"):
            render_left(pdf_path, project_dir, page, idx, len(questions), questions[idx])
    with right:
        with st.container(key="rightpane"):
            render_scratch_space(f"{page}_{idx}")


def guess_type(project_dir: Path, key: str, img_path: Path, q: dict) -> tuple[str | None, dict | None]:
    """보기 버튼을 보여줄지 정하려면 객관식인지 알아야 한다.
    1) 미리 풀기가 끝났으면 AI 판단, 2) 아직이면 PDF 글자에 ①~⑤가 있는지, 3) 둘 다 모르면 AI를 기다린다."""
    entry = get_answer(project_dir, key)
    if entry:
        return entry["type"], entry
    if q.get("mc_hint") is True:
        return "multiple_choice", None
    if q.get("mc_hint") is False:
        return "short_answer", None
    try:
        with st.spinner("문제 읽는 중..."):
            entry = ensure_answer(project_dir, key, img_path)
        return entry["type"], entry
    except Exception as e:
        st.error(f"문제를 읽지 못했어요: {e}")
        return None, None


def render_left(pdf_path: Path, project_dir: Path, page: int, idx: int, count: int, q: dict):
    key = answer_key(page, idx)
    img_path = Path(q["image"])
    unit = pdf_path.stem
    log_path = project_dir / LOG_FILENAME

    st.markdown(f'<div class="where"><b>{page}쪽</b> · {idx + 1} / {count}</div>', unsafe_allow_html=True)
    st.progress((idx + 1) / count)
    st.image(str(img_path), width="stretch")

    with st.expander("문제가 이상하게 잘렸나요?"):
        st.image(str(page_png_path(project_dir, page)))
        if st.button("AI로 이 페이지 문제 다시 찾기", key=f"redetect_{page}"):
            load_page(project_dir, pdf_path, page, force_ai=True)
            go_to(project_dir, page)

    qtype, entry = guess_type(project_dir, key, img_path, q)
    if qtype is None:
        return

    stage = st.session_state.stage
    if stage == "essay_check":
        render_essay_check(project_dir, key, entry, log_path, unit, page, idx)
        return
    if stage == "cloze":
        render_choices_locked(qtype, key, entry)
        render_cloze(project_dir, key, entry)
        return

    locked = stage in ("right", "revealed")

    # ── 답 고르기/입력 ──
    answer_value = None
    if qtype == "multiple_choice":
        if not locked:
            st.caption("다시 골라주세요" if stage == "try2" else "정답을 골라주세요")
        n = len((entry or {}).get("choices") or []) or 5
        n = max(4, min(5, n))
        for i in range(n):
            picked = st.session_state.selected == str(i + 1)
            if st.button(choice_label(i, entry), key=f"choice_{key}_{i}", width="stretch",
                         type="primary" if picked else "secondary", disabled=locked):
                st.session_state.selected = str(i + 1)
                st.rerun()
        answer_value = st.session_state.selected
    elif not locked:
        answer_value = st.text_input("다시 답 입력" if stage == "try2" else "답 입력", key=f"txt_{key}_{stage}",
                                     placeholder="답을 입력하세요", label_visibility="collapsed")

    # ── 결과/힌트 ──
    if stage == "try2":
        hint = (entry or {}).get("hint")
        if not hint:  # 예전에 저장된 정답에는 힌트가 없을 수 있음
            try:
                with st.spinner("힌트 만드는 중..."):
                    hint = generate_hint(img_path)
                set_answer_field(project_dir, key, "hint", hint)
            except Exception as e:
                hint = f"(힌트를 불러오지 못했어요: {e})"
        feedback("wrong", "아쉬워요. 힌트를 보고 다시 풀어볼까요?", hint)
    elif stage == "right":
        feedback("right", "정답이에요!")
    elif stage == "revealed":
        feedback("reveal", f"정답은 {answer_text(entry)} 예요", entry.get("explanation") or "")

    # ── 채점하기 / 다음 문제 ──
    if locked:
        if st.button("다음 문제", key=f"grade_next_{key}", type="primary", width="stretch"):
            go_to(project_dir, page, idx + 1)
        return

    no_choice = qtype == "multiple_choice" and not answer_value
    if st.button("채점하기", key=f"grade_{key}_{stage}", type="primary", width="stretch", disabled=no_choice):
        if not answer_value:
            st.warning("답을 입력해주세요.")
            return
        try:
            with st.spinner("채점 중..."):
                entry = ensure_answer(project_dir, key, img_path)
        except Exception as e:
            st.error(f"채점하지 못했어요: {e}")
            return

        st.session_state.last_answer = answer_value
        if entry["type"] == "essay":
            st.session_state.stage = "essay_check"
            st.rerun()

        ok = grade_answer(entry["type"], entry["answer"], answer_value)
        append_log(log_path, unit, key, entry["type"], answer_value, ok, is_retry=(stage == "try2"))
        if ok:
            st.session_state.stage = "right"
        elif stage == "try1":
            st.session_state.stage = "try2"
            st.session_state.selected = None
        else:
            has_blanks = bool(build_cloze(entry.get("steps") or []).answers)
            st.session_state.stage = "cloze" if has_blanks else "revealed"
        st.rerun()


def render_choices_locked(qtype: str, key: str, entry: dict):
    if qtype != "multiple_choice":
        return
    n = max(4, min(5, len(entry.get("choices") or []) or 5))
    for i in range(n):
        st.button(choice_label(i, entry), key=f"choice_{key}_{i}", width="stretch", disabled=True)


def render_cloze(project_dir: Path, key: str, entry: dict):
    cloze = build_cloze(entry.get("steps") or [])
    typed = st.session_state.cloze_typed  # None이면 아직 확인 전
    feedback("wrong", "이번에도 아쉬워요. 풀이를 따라가며 빈칸을 채워볼까요?")

    results = [grade_answer("short_answer", a, t) for a, t in zip(cloze.answers, typed)] if typed else []

    lines_html = []
    for line in cloze.lines:
        safe = html.escape(line)
        for i, mark in enumerate(BLANK_MARKS[: len(cloze.answers)]):
            slot = f"( {mark} )"
            if typed:
                cls = "ok" if results[i] else "no"
                safe = safe.replace(slot, f'<span class="{cls}">{html.escape(cloze.answers[i])}</span>')
            else:
                safe = safe.replace(slot, f'( <span class="mk">{mark}</span> )')
        lines_html.append(safe)
    st.markdown(f'<div class="steps">{"<br>".join(lines_html)}</div>', unsafe_allow_html=True)

    if not typed:
        cols = st.columns(len(cloze.answers))
        for i, col in enumerate(cols):
            col.text_input(BLANK_MARKS[i], key=f"blank_{key}_{i}")
        if st.button("빈칸 확인하기", key=f"grade_cloze_{key}", type="primary", width="stretch"):
            st.session_state.cloze_typed = [st.session_state.get(f"blank_{key}_{i}", "") for i in range(len(cloze.answers))]
            st.rerun()
        return

    right = sum(results)
    feedback("right" if right == len(results) else "reveal",
             f"빈칸 {len(results)}개 중 {right}개 맞혔어요", f"이 문제의 정답: {answer_text(entry)}")
    if st.button("다음 문제", key=f"grade_next_{key}", type="primary", width="stretch"):
        go_to(project_dir, st.session_state.page, st.session_state.idx + 1)


def render_essay_check(project_dir: Path, key: str, entry: dict, log_path: Path, unit: str, page: int, idx: int):
    st.info("서술형 문제라 직접 확인해주세요.")
    st.write(f"**입력한 답:** {st.session_state.last_answer}")
    with st.expander("AI 풀이 보기", expanded=True):
        st.write((entry or {}).get("explanation") or "")
    c1, c2 = st.columns(2)
    if c1.button("맞았어요", key=f"essay_o_{key}", type="primary", width="stretch"):
        append_log(log_path, unit, key, "essay", "manual_O", True, is_retry=False)
        go_to(project_dir, page, idx + 1)
    if c2.button("틀렸어요", key=f"essay_x_{key}", width="stretch"):
        append_log(log_path, unit, key, "essay", "manual_X", False, is_retry=False)
        go_to(project_dir, page, idx + 1)


def main():
    started = st.session_state.get("started", False)
    st.set_page_config(page_title="문제 풀기", layout="wide", initial_sidebar_state="collapsed" if started else "expanded")
    inject_toss_style()
    st.markdown(LAYOUT_CSS, unsafe_allow_html=True)

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
