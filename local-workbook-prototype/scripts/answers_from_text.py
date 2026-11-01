"""자동 정답 파싱이 심하게 틀렸을 때 쓰는 비상 경로.

다단 표 + 괄호 설명이 섞인 정답표처럼 자동 파싱이 많이 틀리는 경우,
Streamlit 화면에서 한 칸씩 고치는 것보다 메모장에 정답을 쭉 타이핑해서
한 번에 다시 만드는 게 훨씬 빠르다.

사용법:
    1. 프로젝트 폴더 안에 answers_manual.txt 같은 파일을 만들고,
       한 줄에 한 문항씩 "번호. 정답" 형식으로 입력한다.
       예:
           1. 0.25, 유한소수
           2. 0.125, 유한소수
           3. 0.727272..., 무한소수
    2. python scripts/answers_from_text.py --project ./프로젝트 --text ./answers_manual.txt
       (또는 run_answers_from_text.bat 더블클릭)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ANSWERS_FILENAME  # noqa: E402
from core.answer_parser import load_answers, parse_answer_text, save_answers  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="프로젝트 폴더 (예: ./math_grade1_project)")
    parser.add_argument("--text", required=True, help="한 줄에 한 문항씩 정답이 적힌 텍스트 파일")
    args = parser.parse_args()

    project_dir = Path(args.project)
    text_path = Path(args.text)
    if not text_path.exists():
        print(f"파일을 찾을 수 없습니다: {text_path}")
        sys.exit(1)

    text = text_path.read_text(encoding="utf-8")
    new_answers = parse_answer_text(text)
    if not new_answers:
        print(
            "문항을 하나도 인식하지 못했습니다. "
            "각 줄이 '1. 정답' 처럼 번호로 시작하는지, 번호가 1부터 빠짐없이 "
            "순서대로 이어지는지 확인해주세요."
        )
        sys.exit(1)

    answers_path = project_dir / ANSWERS_FILENAME
    existing = load_answers(answers_path)

    # 이미 correct_app.py에서 유형/도형여부를 손봐둔 문항이 있으면 그 값을 유지한다.
    for qno, entry in new_answers.items():
        old = existing.get(qno)
        if old:
            entry["shape_dependent"] = old.get("shape_dependent", entry["shape_dependent"])
        # 기존에 있던 해설(정답지에서 파싱했거나 AI가 만들어둔)도 유지
        if old and old.get("explanation"):
            entry["explanation"] = old["explanation"]
        if old and old.get("hint"):
            entry["hint"] = old["hint"]

    save_answers(answers_path, new_answers)
    print(f"{len(new_answers)}개 문항으로 {answers_path} 다시 저장 완료.")


if __name__ == "__main__":
    main()
