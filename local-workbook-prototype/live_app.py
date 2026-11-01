"""즉석 풀이 모드 (Streamlit) — 고른 페이지부터 한 장씩 처리하면서 바로 푼다. (화면 시안 B: 좌우 분할)

화면: 왼쪽에 문제 + 보기 + 채점하기, 오른쪽 전체가 풀이 공간. 사이드바는 없다.
PDF와 시작 페이지는 첫 화면에서 고르고, 풀이 중 페이지 이동은 왼쪽 위 ⚙ 버튼에 있다.

채점 흐름:
  보기 선택(또는 답 입력) -> 채점하기
  1번 틀림 -> 힌트 보고 다시
  2번 틀림 -> 풀이식 빈칸 채우기 -> 확인하면 정답 공개
AI 사용량 한도에 걸리면: 책 뒤 정답을 보고 직접 맞았어요/틀렸어요로 채점 (풀이가 멈추지 않게)

문제가 뜨면 그 페이지 문제들을 뒤에서 AI 요청 1번으로 미리 풀어두므로(live_engine), 채점은 대부분 즉시 끝난다.

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
from live_engine import answer_key, cached_page, ensure_answer, get_answer, page_png_path, prefetch_around, prepare_page
from subjects.math.gemini_client import QuotaExceededError, quota_blocked_seconds
from subjects.math.nvidia_client import nvidia_available
from ui_theme import inject_toss_style

try:
    from streamlit_drawable_canvas import st_canvas

    HAS_CANVAS = True
except ImportError:
    HAS_CANVAS = False

POSITION_FILE = ".position.json"
CHOICE_MARKS = ["①", "②", "③", "④", "⑤"]
PDF_DIR = Path(".")  # run_live.bat / 안내대로 local-workbook-prototype 폴더에서 실행하고, PDF도 이 폴더에 둔다

LAYOUT_CSS = """
<style>
[data-testid="stAppViewContainer"] { background: #F2F4F6; }
header[data-testid="stHeader"] { background: transparent; height: 0; }
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stAppDeployButton"],
[data-testid="stToolbar"], #MainMenu, footer { display: none !important; }
.block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 1400px; }

.st-key-leftpane { background: #FFFFFF; border-radius: 22px; padding: 16px 20px 20px; }
.st-key-rightpane { background: #FFFFFF; border-radius: 22px; padding: 12px; }

.where { font-size: 15px; color: #4E5968; font-weight: 600; padding-top: 6px; }
.where b { color: #191F28; }

/* 보기: 목록형(보기 내용을 알 때) / 동그라미 한 줄(모를 때). 고른 보기는 옅은 파랑 + 파란 테두리 */
[class*="st-key-choice"] button {
  border: 1.5px solid #E5E8EB !important; background: #FFFFFF !important; color: #191F28 !important;
  box-shadow: none !important; border-radius: 14px; min-height: 46px; padding: 4px 14px;
}
[class*="st-key-choicerow_"] button { justify-content: flex-start !important; }
[class*="st-key-choicerow_"] button div, [class*="st-key-choicerow_"] button p {
  justify-content: flex-start !important; text-align: left !important; width: 100%; font-size: 18px;
}
[class*="st-key-choicechip_"] button p { font-size: 24px; }
[class*="st-key-choice"] button[kind="primary"],
[class*="st-key-choice"] button[data-testid="stBaseButton-primary"] {
  background: #E8F3FF !important; border-color: #3182F6 !important; color: #3182F6 !important; font-weight: 700;
}
[class*="st-key-grade_"] button { min-height: 56px; font-size: 19px; font-weight: 700; border-radius: 16px; }

.fb { border-radius: 14px; padding: 12px 16px; font-size: 16px; line-height: 1.55; margin: 2px 0 6px; }
.fb.wrong { background: #FFF0F0; color: #E42939; }
.fb.right { background: #E6F7EF; color: #03A35F; }
.fb.reveal { background: #E8F3FF; color: #191F28; }
.fb.notice { background: #FFF8E6; color: #8A5A00; }
.fb b { font-weight: 700; }
.fb .sub { display: block; color: #4E5968; margin-top: 4px; font-size: 15px; }

.steps { background: #F9FAFB; border-radius: 14px; padding: 12px 18px; font-size: 19px; line-height: 1.9; margin-bottom: 6px; }
.steps .mk { color: #3182F6; font-weight: 700; }
.steps .ok { color: #03A35F; font-weight: 700; }
.steps .no { color: #E42939; font-weight: 700; }
</style>
"""


# ───────────────────────── 파일/경로 ─────────────────────────


def find_pdfs() -> list[Path]:
    return sorted(PDF_DIR.glob("*.pdf"))


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
    st.session_state.stage = "try1"  # try1 -> try2 -> cloze / right / revealed / essay_check / self_check
    st.session_state.selected = None
    st.session_state.last_answer = ""
    st.session_state.cloze_typed = None  # 빈칸 확인하기를 누른 순간의 입력값 (입력칸이 사라진 뒤에도 유지)
    st.session_state.notice = ""


def go_to(project_dir: Path, page: int, idx: int = 0):
    st.session_state.page = page
    st.session_state.idx = idx
    st.session_state.started = True
    save_position(project_dir, page, idx)
    reset_question_state()
    st.rerun()


def select_pdf(pdf_name: str, auto_resume: bool):
    """auto_resume=True면 저장된 위치가 있을 때 바로 풀이 화면으로 간다 (앱을 처음 열었을 때)."""
    st.session_state.pdf_name = pdf_name
    pos = load_position(project_dir_for_pdf(PDF_DIR / pdf_name))
    st.session_state.page = pos["page"] if pos else 1
    st.session_state.idx = pos["idx"] if pos else 0
    st.session_state.started = bool(pos) and auto_resume
    reset_question_state()


# ───────────────────────── 화면 ─────────────────────────


def render_start_screen(pdfs: list[Path]):
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        names = [p.name for p in pdfs]
        chosen = st.selectbox("문제집", names, index=names.index(st.session_state.pdf_name))
        if chosen != st.session_state.pdf_name:
            select_pdf(chosen, auto_resume=False)
            st.rerun()
        pdf_path = PDF_DIR / chosen
        project_dir = project_dir_for_pdf(pdf_path)
        total_pages = page_count(pdf_path)

        pos = load_position(project_dir)
        if pos:
            if st.button(f"이어서 풀기 · {pos['page']}쪽", type="primary", width="stretch"):
                go_to(project_dir, pos["page"], pos["idx"])
            st.caption("또는 다른 페이지부터 풀기")

        st.subheader("어느 페이지부터 풀까요?")
        st.caption("표지·목차·개념 설명 말고, 실제 문제가 시작하는 페이지를 골라주세요.")
        start = st.number_input("시작 페이지", 1, total_pages, min(st.session_state.page, total_pages))
        preview = render_page(str(pdf_path), int(start), page_png_path(project_dir, int(start)))
        st.image(str(preview), caption=f"{int(start)}쪽 미리보기")
        if st.button("이 페이지부터 풀기", type="primary" if not pos else "secondary", width="stretch"):
            go_to(project_dir, int(start))


def render_settings(pdf_path: Path, project_dir: Path, total_pages: int):
    with st.popover("⚙", width="stretch"):
        page = st.session_state.page
        c1, c2 = st.columns(2)
        if c1.button("◀ 이전 쪽", width="stretch", disabled=page <= 1):
            go_to(project_dir, page - 1)
        if c2.button("다음 쪽 ▶", width="stretch", disabled=page >= total_pages):
            go_to(project_dir, page + 1)
        jump = st.number_input("페이지로 이동", 1, total_pages, page)
        if st.button("이동", width="stretch"):
            go_to(project_dir, int(jump))
        if st.button("문제집 · 시작 페이지 바꾸기", width="stretch"):
            st.session_state.started = False
            st.rerun()
        summary = unit_review_summary(project_dir / LOG_FILENAME, pdf_path.stem)
        if summary["total"]:
            st.caption(f"지금까지 {summary['total']}문제 · 첫 시도 정답률 {summary['accuracy'] * 100:.0f}%")


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
            render_settings(pdf_path, project_dir, total_pages)
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

    # 문제가 바뀔 때 한 번만: 이 페이지 문제들(필요하면 다음 페이지까지)을 뒤에서 미리 푼다
    here = (str(project_dir), page, idx)
    if st.session_state.get("prefetched_for") != here:
        prefetch_around(project_dir, pdf_path, page, idx, total_pages)
        st.session_state.prefetched_for = here

    left, right = st.columns([0.43, 0.57], gap="medium")
    with left:
        with st.container(key="leftpane"):
            render_left(pdf_path, project_dir, total_pages, page, idx, len(questions), questions[idx])
    with right:
        with st.container(key="rightpane"):
            render_scratch_space(f"{page}_{idx}")


def guess_type(project_dir: Path, key: str, img_path: Path, q: dict) -> tuple[str, dict | None]:
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
    except QuotaExceededError as e:
        st.session_state.notice = str(e)
    except Exception as e:
        st.session_state.notice = f"문제를 읽지 못했어요: {e}"
    return "short_answer", None  # 모르면 입력창으로 받아두고, 채점은 직접 확인으로


def render_choices(key: str, entry: dict | None, disabled: bool):
    """보기 내용을 알면 한 줄씩 목록으로, 모르면 ①~⑤ 동그라미 한 줄로 (화면을 덜 차지하게)."""
    texts = (entry or {}).get("choices") or []
    n = max(4, min(5, len(texts))) if texts else 5
    clicked = None
    if texts:
        for i in range(n):
            label = f"{CHOICE_MARKS[i]}   {texts[i]}" if i < len(texts) else CHOICE_MARKS[i]
            picked = st.session_state.selected == str(i + 1)
            if st.button(label, key=f"choicerow_{key}_{i}", width="stretch",
                         type="primary" if picked else "secondary", disabled=disabled):
                clicked = str(i + 1)
    else:
        cols = st.columns(n)
        for i in range(n):
            picked = st.session_state.selected == str(i + 1)
            if cols[i].button(CHOICE_MARKS[i], key=f"choicechip_{key}_{i}", width="stretch",
                              type="primary" if picked else "secondary", disabled=disabled):
                clicked = str(i + 1)
    if clicked:
        st.session_state.selected = clicked
        st.rerun()


def render_left(pdf_path: Path, project_dir: Path, total_pages: int, page: int, idx: int, count: int, q: dict):
    key = answer_key(page, idx)
    img_path = Path(q["image"])
    unit = pdf_path.stem
    log_path = project_dir / LOG_FILENAME

    head, gear = st.columns([5, 1])
    head.markdown(f'<div class="where"><b>{page}쪽</b> · {idx + 1} / {count}</div>', unsafe_allow_html=True)
    with gear:
        render_settings(pdf_path, project_dir, total_pages)
    st.progress((idx + 1) / count)
    st.image(str(img_path), width="stretch")

    with st.expander("문제가 이상하게 잘렸나요?"):
        st.image(str(page_png_path(project_dir, page)))
        if st.button("AI로 이 페이지 문제 다시 찾기", key=f"redetect_{page}"):
            load_page(project_dir, pdf_path, page, force_ai=True)
            go_to(project_dir, page)

    qtype, entry = guess_type(project_dir, key, img_path, q)

    stage = st.session_state.stage
    if st.session_state.notice and stage not in ("self_check",):
        feedback("notice", st.session_state.notice)
    if stage == "essay_check":
        render_essay_check(project_dir, key, entry, log_path, unit, page, idx)
        return
    if stage == "self_check":
        render_self_check(project_dir, key, qtype, log_path, unit, page, idx)
        return
    if stage == "cloze":
        render_cloze(project_dir, key, entry)
        return

    locked = stage in ("right", "revealed")

    # ── 결과/힌트 (보기 위에 둬서 스크롤 없이 보이게) ──
    if stage == "try2":
        hint = (entry or {}).get("hint")
        feedback("wrong", "아쉬워요. 힌트를 보고 다시 풀어볼까요?" if hint else "아쉬워요. 한 번 더 풀어볼까요?", hint or "")
    elif stage == "right":
        feedback("right", "정답이에요!")
    elif stage == "revealed":
        feedback("reveal", f"정답은 {answer_text(entry)} 예요", entry.get("explanation") or "")
    if stage != "try1" and (entry or {}).get("solver") == "nvidia":
        st.caption("보조 AI(NVIDIA)가 낸 정답으로 채점했어요. 채점이 이상하면 책 뒤 정답을 확인해주세요.")

    # ── 답 고르기/입력 ──
    answer_value = None
    if qtype == "multiple_choice":
        render_choices(key, entry, disabled=locked)
        answer_value = st.session_state.selected
    elif not locked:
        answer_value = st.text_input("답", key=f"txt_{key}_{stage}", placeholder="답을 입력하세요", label_visibility="collapsed")

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
        st.session_state.last_answer = answer_value
        try:
            with st.spinner("채점 중..."):
                entry = ensure_answer(project_dir, key, img_path)
        except QuotaExceededError as e:
            st.session_state.notice = str(e)
            st.session_state.stage = "self_check"
            st.rerun()
        except Exception as e:
            st.error(f"채점하지 못했어요: {e}")
            return

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


def render_self_check(project_dir: Path, key: str, qtype: str, log_path: Path, unit: str, page: int, idx: int):
    """AI를 못 쓰는 동안(사용량 한도): 책 뒤 정답을 보고 직접 채점."""
    feedback("notice", st.session_state.notice or "지금은 AI로 채점할 수 없어요.", "책 뒤 정답을 보고 직접 채점해주세요.")
    mine = st.session_state.last_answer
    if qtype == "multiple_choice" and mine and mine.isdigit():
        mine = CHOICE_MARKS[int(mine) - 1]
    st.markdown(f"**내 답:** {html.escape(mine)}")
    c1, c2 = st.columns(2)
    if c1.button("맞았어요", key=f"grade_self_o_{key}", type="primary", width="stretch"):
        append_log(log_path, unit, key, qtype, f"self_O:{mine}", True, is_retry=False)
        go_to(project_dir, page, idx + 1)
    if c2.button("틀렸어요", key=f"grade_self_x_{key}", width="stretch"):
        append_log(log_path, unit, key, qtype, f"self_X:{mine}", False, is_retry=False)
        go_to(project_dir, page, idx + 1)


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
    feedback("reveal", "서술형 문제라 직접 확인해주세요.", f"입력한 답: {st.session_state.last_answer}")
    with st.expander("AI 풀이 보기", expanded=True):
        st.write((entry or {}).get("explanation") or "")
    c1, c2 = st.columns(2)
    if c1.button("맞았어요", key=f"grade_essay_o_{key}", type="primary", width="stretch"):
        append_log(log_path, unit, key, "essay", "manual_O", True, is_retry=False)
        go_to(project_dir, page, idx + 1)
    if c2.button("틀렸어요", key=f"grade_essay_x_{key}", width="stretch"):
        append_log(log_path, unit, key, "essay", "manual_X", False, is_retry=False)
        go_to(project_dir, page, idx + 1)


def main():
    st.set_page_config(page_title="문제 풀기", layout="wide", initial_sidebar_state="collapsed")
    inject_toss_style()
    st.markdown(LAYOUT_CSS, unsafe_allow_html=True)

    pdfs = find_pdfs()
    if not pdfs:
        st.info("local-workbook-prototype 폴더에 문제집 PDF 파일을 넣고 새로고침해주세요.")
        return
    if st.session_state.get("pdf_name") not in [p.name for p in pdfs]:
        select_pdf(pdfs[0].name, auto_resume=True)

    blocked = quota_blocked_seconds()
    if blocked > 0:
        later = f"약 {int(blocked // 60) + 1}분 뒤 Gemini 다시 사용 가능"
        if nvidia_available():
            st.caption(f"Gemini 사용량 한도 · {later} · 그동안은 보조 AI(NVIDIA)로 채점")
        else:
            st.caption(f"AI 사용량 한도 · {later} · 그동안은 책 뒤 정답으로 직접 채점")

    pdf_path = PDF_DIR / st.session_state.pdf_name
    if not st.session_state.started:
        render_start_screen(pdfs)
    else:
        render_question(pdf_path, project_dir_for_pdf(pdf_path), page_count(pdf_path))


if __name__ == "__main__":
    main()
