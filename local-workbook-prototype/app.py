"""태블릿 풀이 화면 (Streamlit).

실행 (같은 와이파이의 태블릿에서 접속 가능하게):
    streamlit run app.py --server.address 0.0.0.0

화면 구성:
  - 사이드바: 단원(=프로젝트 폴더) 선택, 이어서 풀기 / 처음부터
  - 메인: 문제 이미지 -> 답 입력(객관식 버튼 / 주관식 텍스트) -> 즉시 채점
    -> 오답이면 1차 힌트(정답은 알려주지 않음) 제공 + 2차 재도전
    -> 2차도 오답이면 정답 + 어디서 틀렸는지 해설 표시
  - 한 단원 다 풀면 결과 요약 화면 (정답률, 틀린 문제, 재도전 후 이해/여전히 틀림)
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ANSWERS_FILENAME, LOG_FILENAME, QUESTIONS_DIRNAME
from core.answer_parser import load_answers, save_answers
from core.grading import grade_answer
from core.session_log import append_log, answered_question_nos, unit_review_summary
from subjects.math.answer_rules import is_twin_question_eligible
from subjects.math.hint_explain import HintExplainError, generate_explanation, generate_hint
from subjects.math.twin_question import TwinQuestionError, generate_twin_question
from ui_theme import inject_centered_card_width, inject_toss_style

CIRCLED = ["①", "②", "③", "④", "⑤"]


def numeric_sort_key(qno: str):
    return (0, int(qno)) if qno.isdigit() else (1, qno)


def find_units(root_dir: Path) -> list[Path]:
    if not root_dir.exists():
        return []
    return sorted(d for d in root_dir.iterdir() if d.is_dir() and (d / ANSWERS_FILENAME).exists())


def question_image_path(project_dir: Path, qno: str) -> Path:
    name = f"q_{qno.zfill(2)}.png" if qno.isdigit() else f"q_{qno}.png"
    return project_dir / QUESTIONS_DIRNAME / name


def reset_question_state():
    """새 문제로 넘어갈 때마다 초기화해야 하는 상태."""
    st.session_state.stage = "answering"  # "answering" -> "hint" -> "revealed"
    st.session_state.hint_text = None
    st.session_state.explanation_text = None
    st.session_state.last_wrong_1 = None
    st.session_state.last_wrong_2 = None
    st.session_state.bonus_twin = None


def reset_session_for_unit(unit_name: str, start_index: int):
    st.session_state.unit = unit_name
    st.session_state.index = start_index
    st.session_state.show_summary = False
    reset_question_state()


def advance_to_next():
    st.session_state.index += 1
    reset_question_state()
    st.rerun()


def render_sidebar() -> tuple[Path, list[str]] | None:
    st.sidebar.header("📚 단원 선택")
    root = st.sidebar.text_input("작업 폴더들의 상위 경로", value=".")
    units = find_units(Path(root))
    if not units:
        st.sidebar.warning("answers.json이 있는 프로젝트 폴더를 찾지 못했습니다.")
        return None

    names = [u.name for u in units]
    chosen = st.sidebar.selectbox("단원(파일)", names)
    project_dir = units[names.index(chosen)]

    answers = load_answers(project_dir / ANSWERS_FILENAME)
    qnos = sorted(answers.keys(), key=numeric_sort_key)

    log_path = project_dir / LOG_FILENAME
    answered = answered_question_nos(log_path, chosen)
    remaining = [q for q in qnos if q not in answered]
    resume_index = qnos.index(remaining[0]) if remaining else len(qnos)

    col1, col2 = st.sidebar.columns(2)
    if col1.button("이어서 풀기", use_container_width=True) or "unit" not in st.session_state:
        reset_session_for_unit(chosen, resume_index if remaining else 0)
    if col2.button("처음부터", use_container_width=True):
        reset_session_for_unit(chosen, 0)

    if st.session_state.get("unit") != chosen:
        reset_session_for_unit(chosen, resume_index if remaining else 0)

    return project_dir, qnos


def log_attempt(log_path: Path, unit: str, qno: str, qtype: str, submitted: str, correct: bool, is_retry: bool):
    append_log(log_path, unit, qno, qtype, submitted, correct, is_retry=is_retry)


def persist_field(project_dir: Path, answers: dict, qno: str, field: str, value: str):
    """힌트/해설을 answers.json에 저장해두면, 같은 문제를 다시 만났을 때
    Gemini를 다시 호출하지 않고 바로 보여줄 수 있어 훨씬 빠르다."""
    answers[qno][field] = value
    save_answers(project_dir / ANSWERS_FILENAME, answers)


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
        reset_session_for_unit(unit, 0)
        st.rerun()


def _multiple_choice_input(qno: str, idx: int, attempt: int) -> str | None:
    cols = st.columns(5)
    for i, label in enumerate(CIRCLED):
        if cols[i].button(label, key=f"mc_{qno}_{idx}_{attempt}_{i}", use_container_width=True):
            return str(i + 1)
    return None


def _short_answer_input(qno: str, idx: int, attempt: int) -> str | None:
    value = st.text_input("답 입력", key=f"sa_{qno}_{idx}_{attempt}")
    if st.button("제출", key=f"submit_{qno}_{idx}_{attempt}", type="primary"):
        return value
    return None


def render_question_card(project_dir: Path, unit: str, qnos: list[str]):
    answers = load_answers(project_dir / ANSWERS_FILENAME)
    idx = st.session_state.index

    if idx >= len(qnos):
        st.session_state.show_summary = True
        st.rerun()
        return

    qno = qnos[idx]
    meta = answers[qno]
    qtype = meta.get("type", "short_answer")
    correct_answer = meta.get("answer", "")
    img_path = question_image_path(project_dir, qno)
    log_path = project_dir / LOG_FILENAME

    st.progress(idx / len(qnos), text=f"{idx + 1} / {len(qnos)} 문제")
    st.subheader(f"문제 {qno}")
    if img_path.exists():
        st.image(str(img_path))
    else:
        st.warning(f"문제 이미지가 없습니다: {img_path}")

    stage = st.session_state.stage

    # ── 1차 시도 ──────────────────────────────────────────────
    if stage == "answering":
        if qtype == "essay":
            st.info("서술형/풀이과정 문항입니다. 정답을 보고 직접 O/X를 선택하세요.")
            with st.expander("정답 보기"):
                st.write(correct_answer)
            col_o, col_x = st.columns(2)
            if col_o.button("맞았어요 (O)", key=f"essay_o_{qno}_{idx}", type="primary", use_container_width=True):
                log_attempt(log_path, unit, qno, qtype, "manual_O", True, is_retry=False)
                advance_to_next()
            if col_x.button("틀렸어요 (X)", key=f"essay_x_{qno}_{idx}", use_container_width=True):
                log_attempt(log_path, unit, qno, qtype, "manual_X", False, is_retry=False)
                st.session_state.stage = "revealed"  # 서술형은 힌트 단계 없이 바로 정답 확인
                st.rerun()
            return

        submitted = (
            _multiple_choice_input(qno, idx, 1) if qtype == "multiple_choice" else _short_answer_input(qno, idx, 1)
        )
        if submitted:
            ok = grade_answer(qtype, correct_answer, submitted)
            log_attempt(log_path, unit, qno, qtype, submitted, ok, is_retry=False)
            if ok:
                st.success("정답입니다! ✅")
                advance_to_next()
            else:
                st.session_state.last_wrong_1 = submitted
                st.session_state.stage = "hint"
                st.rerun()
        return

    # ── 2차 시도 (1차 힌트 제공) ──────────────────────────────
    if stage == "hint":
        st.error("오답이에요. 힌트를 보고 한 번 더 풀어볼까요?")
        if st.session_state.hint_text is None:
            cached = meta.get("hint")
            if cached:
                st.session_state.hint_text = cached  # 이미 저장된 힌트 -> AI 호출 없이 즉시 표시
            else:
                with st.spinner("힌트 만드는 중... (처음 한 번만 걸리고, 다음부터는 즉시 나와요)"):
                    try:
                        st.session_state.hint_text = generate_hint(img_path)
                        persist_field(project_dir, answers, qno, "hint", st.session_state.hint_text)
                    except HintExplainError as e:
                        st.session_state.hint_text = f"(힌트를 불러올 수 없어요: {e})"
                    except Exception as e:  # Gemini 호출 실패 등 예상 못한 오류도 앱이 죽지 않게 처리
                        st.session_state.hint_text = f"(힌트를 불러오는 중 오류가 발생했어요: {e})"
        st.info(f"💡 **힌트**\n\n{st.session_state.hint_text}")

        submitted2 = (
            _multiple_choice_input(qno, idx, 2) if qtype == "multiple_choice" else _short_answer_input(qno, idx, 2)
        )
        if submitted2:
            ok = grade_answer(qtype, correct_answer, submitted2)
            log_attempt(log_path, unit, qno, qtype, submitted2, ok, is_retry=True)
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
        st.error("이번에도 오답이에요. 정답과 풀이를 확인해보세요.")
        st.markdown(f"### 정답: `{correct_answer}`")

        if qtype != "essay":
            if st.session_state.explanation_text is None:
                cached = meta.get("explanation")
                if cached:
                    # 정답지에서 미리 파싱해뒀거나 이전에 생성해둔 해설 -> AI 호출 없이 즉시 표시
                    st.session_state.explanation_text = cached
                else:
                    with st.spinner("해설 만드는 중... (처음 한 번만 걸리고, 다음부터는 즉시 나와요)"):
                        try:
                            explanation = generate_explanation(
                                img_path,
                                st.session_state.get("last_wrong_2") or st.session_state.get("last_wrong_1") or "",
                                correct_answer,
                            )
                            st.session_state.explanation_text = explanation
                            persist_field(project_dir, answers, qno, "explanation", explanation)
                        except HintExplainError as e:
                            st.session_state.explanation_text = f"(해설을 불러올 수 없어요: {e})"
                        except Exception as e:
                            st.session_state.explanation_text = f"(해설을 불러오는 중 오류가 발생했어요: {e})"
            st.markdown("**📝 어디서 틀렸을까요?**")
            st.write(st.session_state.explanation_text)

        eligible = is_twin_question_eligible(meta)
        if eligible and qtype != "essay":
            if st.session_state.bonus_twin is None:
                if st.button("비슷한 문제로 한 번 더 연습하기", key=f"bonus_{qno}_{idx}"):
                    with st.spinner("비슷한 문제 만드는 중..."):
                        try:
                            st.session_state.bonus_twin = generate_twin_question(img_path)
                        except TwinQuestionError as e:
                            st.error(str(e))
                        except Exception as e:
                            st.error(f"문제 생성 중 오류가 발생했어요: {e}")
                    st.rerun()
            else:
                twin = st.session_state.bonus_twin
                st.markdown("---")
                st.markdown("**🔁 비슷한 문제**")
                st.write(twin["problem"])
                with st.expander("정답/해설 보기"):
                    st.write(f"정답: {twin['answer']}")
                    st.write(f"해설: {twin['explanation']}")

        if st.button("다음 문제로", key=f"next_{qno}_{idx}", type="primary"):
            advance_to_next()
        return


def main():
    st.set_page_config(page_title="오답노트 풀이", layout="centered")
    inject_toss_style()
    inject_centered_card_width()
    st.title("📘 문제집 풀이 & 오답 재풀이")

    result = render_sidebar()
    if result is None:
        return
    project_dir, qnos = result
    unit = st.session_state.unit

    if st.session_state.get("show_summary"):
        render_summary(project_dir, unit)
    else:
        render_question_card(project_dir, unit, qnos)


if __name__ == "__main__":
    main()
