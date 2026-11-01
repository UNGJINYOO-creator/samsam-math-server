"""정답표 파싱 없이, 각 문제 이미지를 AI(Gemini)가 직접 풀어서 answers.json을 만든다.

출판사 정답표 레이아웃이 복잡해서 자동 파싱이 자꾸 깨지거나, 정답표 자체가
없을 때 쓸 수 있는 대안 경로. core/question_split.py로 이미 잘라둔 문제
이미지(questions/q_XX.png)를 하나씩 Gemini에게 보여주고 풀게 한다.

사용법:
    python scripts/solve_with_ai.py --project ./math_grade1_project

먼저 run_split.bat(또는 run_split_and_parse.py)로 문제 분할까지는 끝내둬야 한다
(--answer-pages는 몰라도 되고, 아무 숫자나 넣어서 일단 분할만 해도 된다).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ANSWERS_FILENAME, QUESTIONS_DIRNAME  # noqa: E402
from core.answer_parser import load_answers, save_answers  # noqa: E402
from subjects.math.ai_solver import AISolveError, solve_question  # noqa: E402


def question_sort_key(path: Path):
    stem = path.stem.replace("q_", "")
    return (0, int(stem)) if stem.isdigit() else (1, stem)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="프로젝트 폴더 (예: ./math_grade1_project)")
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="이미 answers.json에 정답이 있는 문항은 건너뛰고, 없는 문항만 AI로 풀기",
    )
    args = parser.parse_args()

    project_dir = Path(args.project)
    questions_dir = project_dir / QUESTIONS_DIRNAME
    answers_path = project_dir / ANSWERS_FILENAME

    image_files = sorted(questions_dir.glob("q_*.png"), key=question_sort_key)
    if not image_files:
        print("questions/ 폴더에 문제 이미지가 없습니다. 먼저 run_split.bat을 실행하세요.")
        sys.exit(1)

    answers = load_answers(answers_path)

    print(f"{len(image_files)}개 문제를 AI가 하나씩 풉니다. (문항당 1~3초 정도 걸립니다)")
    solved = 0
    for i, img_path in enumerate(image_files, 1):
        stem = img_path.stem.replace("q_", "")
        qno = str(int(stem)) if stem.isdigit() else stem

        if args.only_missing and answers.get(qno, {}).get("answer"):
            continue

        print(f"  [{i}/{len(image_files)}] 문항 {qno} 푸는 중...", end=" ", flush=True)
        try:
            result = solve_question(img_path)
            old = answers.get(qno, {})
            answers[qno] = {
                "answer": result["answer"],
                "type": result["type"],
                "shape_dependent": old.get("shape_dependent", False),
                "explanation": result["explanation"],
            }
            save_answers(answers_path, answers)  # 문항마다 즉시 저장 -> 중간에 끊겨도 그동안 작업은 보존됨
            print(f"-> {result['answer']}")
            solved += 1
        except AISolveError as e:
            print(f"\n중단: {e}")
            break
        except Exception as e:  # JSON 파싱 실패 등 이 문항만의 문제는 건너뛰고 계속 진행
            print(f"실패(건너뜀): {e}")

        time.sleep(0.5)  # API 과도한 연속 호출 방지

    print(f"\n완료. {solved}개 문항을 AI가 풀어서 {answers_path} 에 저장했습니다.")
    print("1분 눈 검수(run_correct.bat)에서 틀린 것만 확인하세요 — 특히 도형/그래프 문제는 더 꼼꼼히 봐주세요.")


if __name__ == "__main__":
    main()
