"""태블릿 풀이 화면 (Streamlit).

실행 (같은 와이파이의 태블릿에서 접속 가능하게):
    streamlit run app.py --server.address 0.0.0.0

화면 구성:
  - 사이드바: 단원(=프로젝트 폴더) 선택, 이어서 풀기 / 처음부터
  - 메인: 문제 이미지 -> 답 입력(객관식 버튼 / 주관식 텍스트) -> 즉시 채점
    -> 오답이면 [쌍둥이 문제 도전] / [넘어가기]
  - 한 단원 다 풀면 결과 요약 화면 (정답률, 틀린 문제, 재도전 후 이해/여전히 틀림)
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ANSWERS_FILENAME, LOG_FILENAME, QUESTIONS_DIRNAME
from core.answer_parser import load_answers
from core.grading import grade_answer, normalize_short_answer
from core.session_log import append_log, answered_question_nos, unit_review_summary
from subjects.math.answer_rules import is_auto_gradable, is_twin_question_eligible
from subjects.math.twin_question import TwinQuestionError, generate_twin_question

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


def reset_session_for_unit(unit_name: str, start_index: int):
    st.session_state.unit = unit_name
    st.session_state.index = start_index
    st.session_state.awaiting_retry_choice = False
    st.session_state.twin = None
    st.session_state.twin_result = None
    st.session_state.show_summary = False


def render_sidebar() -> tuple[Path, list[str]] | None:
    st.sidebar.header("단원 선택")
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


def render_summary(project_dir: Path, unit: str):
    st.header("결과 요약")
    summary = unit_review_summary(project_dir / LOG_FILENAME, unit)
    st.metric("첫 시도 정답률", f"{summary['accuracy'] * 100:.0f}%", f"{summary['correct_first']}/{summary['total']}")

    if summary["wrong_qnos"]:
        st.subheader(f"틀린 문제 ({len(summary['wrong_qnos'])}개)")
        st.write(", ".join(summary["wrong_qnos"]))
        st.markdown(f"- ✅ 재도전 후 이해함: {', '.join(summary['understood_after_retry']) or '없음'}")
        st.markdown(f"- ❌ 여전히 틀림: {', '.join(summary['still_wrong']) or '없음'}")
    else:
        st.success("틀린 문제가 없습니다!")

    if st.button("다시 처음부터 풀기"):
        reset_session_for_unit(unit, 0)
        st.rerun()


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
    img_path = question_image_path(project_dir, qno)

    st.progress((idx) / len(qnos), text=f"{idx + 1} / {len(qnos)} 문제")
    st.subheader(f"문제 {qno}")
    if img_path.exists():
        st.image(str(img_path))
    else:
        st.warning(f"문제 이미지가 없습니다: {img_path}")

    # 쌍둥이 문제 재도전 모드
    if st.session_state.twin is not None:
        twin = st.session_state.twin
        st.info("🔁 쌍둥이 문제")
        st.write(twin["problem"])
        submitted = st.text_input("답 입력", key=f"twin_input_{qno}_{idx}")

        # Streamlit은 매 상호작용마다 스크립트를 처음부터 다시 실행하므로, 버튼 클릭
        # 시점에만 존재하는 값(True)에 의존해 그 아래에 또 다른 버튼을 중첩시키면
        # 다음 렌더링에서 바깥 버튼이 다시 False가 되어 안쪽 버튼이 사라진다.
        # 그래서 채점 결과를 session_state에 저장해두고, 그 상태를 기준으로
        # "다음 문제로" 버튼을 별도로(중첩 없이) 그린다.
        if st.button("제출 (쌍둥이)", key=f"twin_submit_{qno}_{idx}"):
            correct = bool(submitted.strip()) and normalize_short_answer(submitted) == normalize_short_answer(
                twin["answer"]
            )
            log_attempt(project_dir / LOG_FILENAME, unit, qno, qtype, submitted, correct, is_retry=True)
            st.session_state.twin_result = correct

        if st.session_state.get("twin_result") is not None:
            if st.session_state.twin_result:
                st.success("정답입니다! 이번엔 이해했네요 🎉")
            else:
                st.error("이번에도 오답입니다.")
                with st.expander("정답/해설 보기"):
                    st.write(f"정답: {twin['answer']}")
                    st.write(f"해설: {twin['explanation']}")
            if st.button("다음 문제로", key=f"twin_next_{qno}_{idx}"):
                st.session_state.index += 1
                st.session_state.twin = None
                st.session_state.twin_result = None
                st.session_state.awaiting_retry_choice = False
                st.rerun()
        return

    if not st.session_state.awaiting_retry_choice:
        submitted_answer = None
        if qtype == "multiple_choice":
            cols = st.columns(5)
            for i, label in enumerate(CIRCLED):
                if cols[i].button(label, key=f"mc_{qno}_{idx}_{i}"):
                    submitted_answer = str(i + 1)
        elif qtype == "essay":
            st.info("서술형/풀이과정 문항입니다. 정답을 보고 직접 O/X를 선택하세요.")
            with st.expander("정답 보기"):
                st.write(meta.get("answer", ""))
            col_o, col_x = st.columns(2)
            if col_o.button("맞았어요 (O)", key=f"essay_o_{qno}_{idx}"):
                log_attempt(project_dir / LOG_FILENAME, unit, qno, qtype, "manual_O", True, is_retry=False)
                st.session_state.index += 1
                st.rerun()
            if col_x.button("틀렸어요 (X)", key=f"essay_x_{qno}_{idx}"):
                log_attempt(project_dir / LOG_FILENAME, unit, qno, qtype, "manual_X", False, is_retry=False)
                st.session_state.awaiting_retry_choice = True
                st.rerun()
            return
        else:
            submitted_answer = st.text_input("답 입력", key=f"sa_{qno}_{idx}")
            if not st.button("제출", key=f"submit_{qno}_{idx}"):
                submitted_answer = None

        if submitted_answer:
            correct = grade_answer(qtype, meta.get("answer", ""), submitted_answer)
            log_attempt(project_dir / LOG_FILENAME, unit, qno, qtype, submitted_answer, correct, is_retry=False)
            if correct:
                st.success("정답입니다! ✅")
                st.session_state.index += 1
                st.rerun()
            else:
                st.error("오답입니다. ❌")
                st.session_state.awaiting_retry_choice = True
                st.rerun()
        return

    # 오답 처리: 쌍둥이 문제 도전 or 넘어가기
    st.error("오답입니다.")
    col1, col2 = st.columns(2)
    eligible = is_twin_question_eligible(meta)

    if eligible:
        if col1.button("쌍둥이 문제 도전", key=f"twin_btn_{qno}_{idx}"):
            with st.spinner("Gemini로 쌍둥이 문제 생성 중..."):
                try:
                    st.session_state.twin = generate_twin_question(img_path)
                except TwinQuestionError as e:
                    st.error(str(e))
                except Exception as e:  # Gemini API 호출 실패 등 예상 못한 오류도 앱이 죽지 않게 처리
                    st.error(f"쌍둥이 문제 생성 중 오류가 발생했습니다: {e}")
            st.rerun()
    else:
        with col1:
            st.info("도형/그래프 문제는 쌍둥이 생성이 어려워요.")
            with st.expander("정답/해설 다시 보기"):
                st.write(f"정답: {meta.get('answer', '')}")

    if col2.button("넘어가기", key=f"skip_{qno}_{idx}"):
        st.session_state.index += 1
        st.session_state.awaiting_retry_choice = False
        st.rerun()


def main():
    st.set_page_config(page_title="오답노트 풀이", layout="centered")
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
