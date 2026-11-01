"""즉석 풀이 모드 (Streamlit) — PDF를 열면 바로 문제를 한 개씩 풀 수 있는 올인원 화면.

기존 흐름(run_split.bat -> run_ai_solve.bat -> run_correct.bat -> run_app.bat)을
한 화면으로 합쳤다:
  1) PDF를 고르면 (처음 여는 파일일 때만) 자동으로 문제 분할까지 끝낸다.
  2) 문제를 한 개씩 보여주고, 밑에 풀이 공간(선택) + 최종 답 입력창을 둔다.
  3) 제출하면 "그 문제만" 그 순간 AI가 풀어서 채점한다(처음 보는 문제만 AI 호출,
     이미 푼 적 있으면 answers.json에 저장된 걸 즉시 재사용).
  4) 오답이면 기존 app.py와 동일한 힌트 -> 2차 재도전 -> 정답/해설 흐름을 그대로 쓴다.

이 화면이 만드는 프로젝트 폴더는 run_split.bat이 만드는 것과 완전히 같은 구조
(pages/, questions/, boundaries.json, answers.json, log.csv)이므로, 나중에
run_correct.bat(세밀한 보정)이나 app.py(결과 요약)로 열어도 그대로 호환된다.

실행:
    streamlit run live_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ANSWERS_FILENAME, BOUNDARIES_FILENAME, LOG_FILENAME, PAGES_DIRNAME, QUESTIONS_DIRNAME
from core.answer_parser import load_answers, save_answers
from core.grading import grade_answer
from core.page_render import render_pages
from core.pdf_analysis import check_text_layer
from core.question_split import build_all_boundaries, export_question_crops, save_boundaries
from core.session_log import append_log, unit_review_summary
from subjects.math.ai_solver import AISolveError, solve_question
from subjects.math.hint_explain import HintExplainError, generate_explanation, generate_hint
from ui_theme import inject_centered_card_width, inject_toss_style

try:
    from streamlit_drawable_canvas import st_canvas

    HAS_CANVAS = True
except ImportError:
    HAS_CANVAS = False


# ───────────────────────── 프로젝트 자동 준비 ─────────────────────────


def find_pdfs(root: Path) -> list[Path]:
    return sorted(root.glob("*.pdf"))


def project_dir_for_pdf(pdf_path: Path) -> Path:
    return pdf_path.parent / f"{pdf_path.stem}_live"


def ensure_project_ready(pdf_path: Path) -> Path:
    """처음 여는 PDF면 자동으로 문제 분할까지 끝내고, 이미 분할되어 있으면 그대로 재사용."""
    project_dir = project_dir_for_pdf(pdf_path)
    questions_dir = project_dir / QUESTIONS_DIRNAME
    boundaries_path = project_dir / BOUNDARIES_FILENAME

    if boundaries_path.exists() and any(questions_dir.glob("q_*.png")):
        return project_dir

    project_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = project_dir / PAGES_DIRNAME

    with st.spinner("처음 여는 파일이라 문제를 나누고 있어요 (몇 초~몇십 초 정도 걸려요)..."):
        render_pages(str(pdf_path), pages_dir)
        text_results = check_text_layer(str(pdf_path))
        image_only_pages = [r.page_no - 1 for r in text_results if not r.has_text_layer]
        boundaries = build_all_boundaries(str(pdf_path), image_only_pages=image_only_pages)
        save_boundaries(boundaries_path, boundaries)
        export_question_crops(boundaries, pages_dir, questions_dir)

        answers_path = project_dir / ANSWERS_FILENAME
        if not answers_path.exists():
            save_answers(answers_path, {})

    return project_dir


def question_sort_key(p: Path):
    stem = p.stem.replace("q_", "")
    return (0, int(stem)) if stem.isdigit() else (1, stem)


def list_question_images(project_dir: Path) -> list[Path]:
    return sorted((project_dir / QUESTIONS_DIRNAME).glob("q_*.png"), key=question_sort_key)


def qno_from_path(p: Path) -> str:
    stem = p.stem.replace("q_", "")
    return str(int(stem)) if stem.isdigit() else stem


def ensure_answer(project_dir: Path, qno: str, img_path: Path) -> dict:
    """이미 AI가 풀어둔 적 있으면 그걸 쓰고, 처음 보는 문제만 지금 AI를 부른다."""
    answers_path = project_dir / ANSWERS_FILENAME
    answers = load_answers(answers_path)
    cached = answers.get(qno)
    if cached and cached.get("answer"):
        return cached

    result = solve_question(img_path)  # AISolveError는 호출부에서 처리
    entry = {
        "answer": result["answer"],
        "type": result["type"],
        "shape_dependent": (cached or {}).get("shape_dependent", False),
        "explanation": result["explanation"],
    }
    answers[qno] = entry
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
    st.session_state.solve_error = None


def reset_for_pdf(pdf_name: str, start_index: int = 0):
    st.session_state.pdf_name = pdf_name
    st.session_state.q_index = start_index
    st.session_state.show_summary = False
    reset_question_state()


def advance_to_next():
    st.session_state.q_index += 1
    reset_question_state()
    st.rerun()


# ───────────────────────── 화면 ─────────────────────────


def render_sidebar() -> Path | None:
    st.sidebar.header("📂 문제집 선택")
    root = st.sidebar.text_input("PDF가 있는 폴더", value=".")
    pdfs = find_pdfs(Path(root))
    if not pdfs:
        st.sidebar.warning("이 폴더에 PDF 파일이 없습니다.")
        return None

    names = [p.name for p in pdfs]
    chosen = st.sidebar.selectbox("PDF 파일", names)
    pdf_path = pdfs[names.index(chosen)]

    if st.session_state.get("pdf_name") != chosen:
        reset_for_pdf(chosen, 0)

    if st.sidebar.button("처음부터 다시 풀기", use_container_width=True):
        reset_for_pdf(chosen, 0)
        st.rerun()

    return pdf_path


def render_summary(project_dir: Path, unit: str):
    st.header("🎉 결과 요약")
    summary = unit_review_summary(project_dir / LOG_FILENAME, unit)
    st.metric("첫 시도 정답률", f"{summary['accuracy'] * 100:.0f}%", f"{summary['correct_first']}/{summary['total']}")
    if summary["wrong_qnos"]:
        st.subheader(f"틀린 문제 ({len(summary['wrong_qnos'])}개)")
        st.write(", ".join(summary["wrong_qnos"]))
        st.markdown(f"- ✅ 재도전 후 이해함: {', '.join(summary['understood_after_retry']) or '없음'}")
        st.markdown(f"- ❌ 여전히 틀림: {', '.join(summary['still_wrong']) or '없음'}")
    else:
        st.success("틀린 문제가 없습니다!")
    if st.button("다시 처음부터 풀기", type="primary"):
        reset_for_pdf(st.session_state.pdf_name, 0)
        st.rerun()


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
        st.text_area("풀이 공간 (손글씨를 쓰려면 pip install streamlit-drawable-canvas)", key=f"scratch_{key_suffix}", height=100)


def render_question(project_dir: Path, unit: str, image_files: list[Path]):
    idx = st.session_state.q_index
    if idx >= len(image_files):
        st.session_state.show_summary = True
        st.rerun()
        return

    img_path = image_files[idx]
    qno = qno_from_path(img_path)
    log_path = project_dir / LOG_FILENAME

    st.progress(idx / len(image_files), text=f"{idx + 1} / {len(image_files)} 문제")
    st.subheader(f"문제 {qno}")
    try:
        st.image(str(img_path))
    except Exception:
        st.error("이 문제 이미지가 손상되었거나 비정상적으로 큽니다. 다음 문제로 넘어가 주세요.")

    render_scratch_space(f"{qno}_{idx}")

    stage = st.session_state.stage

    # ── 1차 시도 ──────────────────────────────────────────────
    if stage == "answering":
        submitted = st.text_input("최종 답을 입력하세요", key=f"ans_{qno}_{idx}")
        if st.button("제출", key=f"submit_{qno}_{idx}", type="primary") and submitted:
            try:
                with st.spinner("채점 중..."):
                    entry = ensure_answer(project_dir, qno, img_path)
            except AISolveError as e:
                st.error(str(e))
                return
            except Exception as e:
                st.error(f"채점 중 오류가 발생했어요: {e}")
                return

            if entry["type"] == "essay":
                st.session_state.pending_essay_answer = submitted
                st.session_state.essay_explanation = entry.get("explanation", "")
                st.session_state.stage = "essay_check"
                st.rerun()
                return

            ok = grade_answer(entry["type"], entry["answer"], submitted)
            append_log(log_path, unit, qno, entry["type"], submitted, ok, is_retry=False)
            if ok:
                st.success("정답입니다! ✅")
                advance_to_next()
            else:
                st.session_state.last_wrong_1 = submitted
                st.session_state.stage = "hint"
                st.rerun()
        return

    # ── 서술형: 자동채점 대신 AI 풀이 보고 직접 O/X ──────────────
    if stage == "essay_check":
        st.info("서술형 문제예요. AI가 정리한 풀이를 보고 직접 O/X를 선택하세요.")
        st.write(f"**입력하신 답:** {st.session_state.pending_essay_answer}")
        with st.expander("AI 풀이 보기"):
            st.write(st.session_state.get("essay_explanation", ""))
        col_o, col_x = st.columns(2)
        if col_o.button("맞았어요 (O)", key=f"essay_o_{qno}_{idx}", type="primary", use_container_width=True):
            append_log(log_path, unit, qno, "essay", "manual_O", True, is_retry=False)
            advance_to_next()
        if col_x.button("틀렸어요 (X)", key=f"essay_x_{qno}_{idx}", use_container_width=True):
            append_log(log_path, unit, qno, "essay", "manual_X", False, is_retry=False)
            advance_to_next()
        return

    # ── 2차 시도 (1차 힌트 제공) ──────────────────────────────
    if stage == "hint":
        entry = load_answers(project_dir / ANSWERS_FILENAME)[qno]
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

        submitted2 = st.text_input("다시 답 입력", key=f"ans2_{qno}_{idx}")
        if st.button("제출 (2차)", key=f"submit2_{qno}_{idx}", type="primary") and submitted2:
            ok = grade_answer(entry["type"], entry["answer"], submitted2)
            append_log(log_path, unit, qno, entry["type"], submitted2, ok, is_retry=True)
            if ok:
                st.success("정답입니다! 이번엔 이해했네요 🎉")
                advance_to_next()
            else:
                st.session_state.last_wrong_2 = submitted2
                st.session_state.stage = "revealed"
                st.rerun()
        return

    # ── 정답 + 해설 공개 ──────────────────────────────────────
    if stage == "revealed":
        entry = load_answers(project_dir / ANSWERS_FILENAME)[qno]
        st.error("이번에도 오답이에요. 정답과 풀이를 확인해보세요.")
        st.markdown(f"### 정답: `{entry['answer']}`")

        if st.session_state.explanation_text is None:
            cached = entry.get("explanation")
            if cached:
                st.session_state.explanation_text = cached
            else:
                with st.spinner("해설 만드는 중..."):
                    try:
                        explanation = generate_explanation(
                            img_path,
                            st.session_state.get("last_wrong_2") or st.session_state.get("last_wrong_1") or "",
                            entry["answer"],
                        )
                        st.session_state.explanation_text = explanation
                        answers = load_answers(project_dir / ANSWERS_FILENAME)
                        answers[qno]["explanation"] = explanation
                        save_answers(project_dir / ANSWERS_FILENAME, answers)
                    except HintExplainError as e:
                        st.session_state.explanation_text = f"(해설을 불러올 수 없어요: {e})"
                    except Exception as e:
                        st.session_state.explanation_text = f"(해설을 불러오는 중 오류가 발생했어요: {e})"
        st.markdown("**📝 어디서 틀렸을까요?**")
        st.write(st.session_state.explanation_text)

        if st.button("다음 문제로", key=f"next_{qno}_{idx}", type="primary"):
            advance_to_next()
        return


def main():
    st.set_page_config(page_title="즉석 풀이 모드", layout="centered")
    inject_toss_style()
    inject_centered_card_width()
    st.title("📘 PDF 열고 바로 풀기")
    st.caption("문제집 PDF를 고르면 바로 한 문제씩 풀 수 있어요. 처음 여는 파일만 분석에 조금 걸립니다.")

    pdf_path = render_sidebar()
    if pdf_path is None:
        return

    project_dir = ensure_project_ready(pdf_path)
    unit = pdf_path.stem
    image_files = list_question_images(project_dir)

    if not image_files:
        st.warning("이 PDF에서 문제를 하나도 찾지 못했어요. 텍스트 레이어가 없는 스캔본일 수 있습니다.")
        return

    if "pdf_name" not in st.session_state:
        reset_for_pdf(pdf_path.name, 0)

    if st.session_state.get("show_summary"):
        render_summary(project_dir, unit)
    else:
        render_question(project_dir, unit, image_files)


if __name__ == "__main__":
    main()
