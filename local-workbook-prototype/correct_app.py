"""1분 눈 검수 도구 (Streamlit).

두 가지를 한 화면에서 처리한다:
  1) 정답 검수: answers.json의 문항별 정답/유형/도형여부를 훑어보고 수정
  2) 문제 경계 보정: 자동 분할이 실패했거나 어긋난 문제의 시작/끝 y좌표를 보정
     (페이지 이미지를 클릭하면 클릭한 픽셀 y좌표가 표시되고, 그 값을 표에 입력하는
     방식으로 "클릭 2번(시작/끝)으로 지정"을 구현했다. 별도 클릭 컴포넌트
     streamlit-image-coordinates가 설치되어 있으면 자동으로 사용하고, 없으면
     이미지만 보고 숫자를 직접 입력해도 동작한다.)

실행:
    streamlit run correct_app.py -- --project ./math_grade1_project
"""
import argparse
import sys
from pathlib import Path

import streamlit as st
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ANSWERS_FILENAME, BOUNDARIES_FILENAME, PAGES_DIRNAME, QUESTIONS_DIRNAME, RENDER_DPI
from core.answer_parser import infer_answer_type, load_answers, save_answers
from core.question_split import export_question_crops, load_boundaries, save_boundaries

try:
    from streamlit_image_coordinates import streamlit_image_coordinates

    HAS_CLICK_COMPONENT = True
except ImportError:
    HAS_CLICK_COMPONENT = False


def get_project_dir() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args(sys.argv[1:])
    return Path(args.project)


def numeric_sort_key(qno: str):
    return (0, int(qno)) if qno.isdigit() else (1, qno)


def render_answer_review_tab(project_dir: Path):
    answers_path = project_dir / ANSWERS_FILENAME
    questions_dir = project_dir / QUESTIONS_DIRNAME
    answers = load_answers(answers_path)

    if not answers:
        st.info("answers.json이 비어있습니다. 먼저 scripts/run_split_and_parse.py를 실행하세요.")
        return

    st.caption(f"총 {len(answers)}문항. 이상해 보이는 것만 고치고 [모두 저장]을 누르세요.")

    edited: dict = {}
    for qno in sorted(answers.keys(), key=numeric_sort_key):
        entry = answers[qno]
        cols = st.columns([1, 2, 2, 1, 1])
        crop_path = questions_dir / f"q_{qno.zfill(2)}.png" if qno.isdigit() else None

        with cols[0]:
            st.markdown(f"**#{qno}**")
            if crop_path and crop_path.exists():
                st.image(str(crop_path), width=90)

        with cols[1]:
            new_answer = st.text_input("정답", value=entry.get("answer", ""), key=f"ans_{qno}", label_visibility="collapsed")

        with cols[2]:
            options = ["multiple_choice", "short_answer", "essay"]
            current = entry.get("type", "short_answer")
            idx = options.index(current) if current in options else 1
            new_type = st.selectbox("유형", options, index=idx, key=f"type_{qno}", label_visibility="collapsed")

        with cols[3]:
            new_shape = st.checkbox("도형", value=entry.get("shape_dependent", False), key=f"shape_{qno}")

        with cols[4]:
            if st.button("추정", key=f"guess_{qno}"):
                new_type = infer_answer_type(new_answer)
                st.session_state[f"type_{qno}"] = new_type

        edited[qno] = {"answer": new_answer, "type": new_type, "shape_dependent": new_shape}

    if st.button("모두 저장", type="primary"):
        save_answers(answers_path, edited)
        st.success("answers.json 저장 완료")


def render_boundary_fix_tab(project_dir: Path):
    boundaries_path = project_dir / BOUNDARIES_FILENAME
    pages_dir = project_dir / PAGES_DIRNAME
    questions_dir = project_dir / QUESTIONS_DIRNAME
    boundaries = load_boundaries(boundaries_path)

    page_files = sorted(pages_dir.glob("page_*.png"))
    if not page_files:
        st.info("pages/ 폴더에 페이지 이미지가 없습니다. run_split_and_parse.py를 먼저 실행하세요.")
        return

    page_no = st.selectbox("보정할 페이지", [p.stem.split("_")[1] for p in page_files])
    page_no = int(page_no)
    page_img_path = pages_dir / f"page_{page_no:03d}.png"
    img = Image.open(page_img_path)
    scale = RENDER_DPI / 72.0

    st.write(f"이미지 크기: {img.width} x {img.height} px (scale={scale:.2f})")

    if HAS_CLICK_COMPONENT:
        coords = streamlit_image_coordinates(str(page_img_path), key=f"click_{page_no}")
        if coords is not None:
            st.info(f"마지막 클릭 픽셀 y좌표: **{coords['y']}**  (이 값을 아래 표의 y0/y1에 입력하세요)")
    else:
        st.warning(
            "streamlit-image-coordinates가 설치되어 있지 않아 클릭 좌표 확인이 불가능합니다.\n"
            "pip install streamlit-image-coordinates 로 설치하면 이미지 클릭으로 y좌표를 바로 확인할 수 있습니다.\n"
            "설치 전까지는 아래 이미지를 보고 대략적인 y픽셀 값을 직접 입력하세요."
        )
        st.image(str(page_img_path), use_container_width=True)

    # 이 페이지에 걸린 단일 파트(같은 페이지 안에서 시작~끝) 문항만 표로 편집 가능하게 노출.
    # 여러 페이지에 걸친 문항은 이 표에서 다루지 않고 boundaries.json을 직접 수정해야 한다.
    rows = []
    for qno, entry in boundaries.items():
        if len(entry["parts"]) == 1 and entry["parts"][0]["page"] == page_no:
            part = entry["parts"][0]
            rows.append(
                {
                    "question_no": qno,
                    "y0_px": round(part["y0"] * scale, 1),
                    "y1_px": round(part["y1"] * scale, 1),
                }
            )
    rows.sort(key=lambda r: numeric_sort_key(r["question_no"]))

    edited_rows = st.data_editor(
        rows,
        num_rows="dynamic",
        column_config={
            "question_no": st.column_config.TextColumn("문항 번호 (숫자 또는 임시명)"),
            "y0_px": st.column_config.NumberColumn("시작 y(px)"),
            "y1_px": st.column_config.NumberColumn("끝 y(px)"),
        },
        key=f"editor_{page_no}",
    )

    if st.button("이 페이지 경계 적용 + 이미지 재생성", type="primary"):
        # 이 페이지에 속했던 기존 단일 파트 항목 제거 후 표 내용으로 재구성
        for qno in list(boundaries.keys()):
            entry = boundaries[qno]
            if len(entry["parts"]) == 1 and entry["parts"][0]["page"] == page_no:
                del boundaries[qno]

        for row in edited_rows:
            qno = str(row["question_no"]).strip()
            if not qno:
                continue
            boundaries[qno] = {
                "parts": [
                    {
                        "page": page_no,
                        "y0": float(row["y0_px"]) / scale,
                        "y1": float(row["y1_px"]) / scale,
                    }
                ],
                "auto": False,
            }

        # 이 페이지가 통째로 미확정("page{N}_unsplit")이었다면, 사람이 방금 나눴으니 제거
        unsplit_key = f"page{page_no}_unsplit"
        boundaries.pop(unsplit_key, None)

        save_boundaries(boundaries_path, boundaries)
        saved = export_question_crops(boundaries, pages_dir, questions_dir)
        st.success(f"boundaries.json 저장 + {len(saved)}개 문제 이미지 재생성 완료")


def main():
    st.set_page_config(page_title="문제집 디지털화 - 1분 눈 검수", layout="wide")
    project_dir = get_project_dir()
    st.title("1분 눈 검수")
    st.caption(f"프로젝트: {project_dir}")

    tab1, tab2 = st.tabs(["정답 검수", "문제 경계 보정"])
    with tab1:
        render_answer_review_tab(project_dir)
    with tab2:
        render_boundary_fix_tab(project_dir)


if __name__ == "__main__":
    main()
