"""수학 과목 전용 채점/재도전 가능 여부 규칙.

"과목 공통 파이프라인 + 과목별 플러그인" 구조에서, 이 파일이 수학 과목의
플러그인 역할을 한다. 국어/과학을 추가할 때는 subjects/korean/, subjects/science/
아래에 같은 인터페이스(is_auto_gradable, is_twin_question_eligible)를 구현한
모듈을 추가하고, app.py에서 과목 선택에 따라 로드하는 모듈만 바꾸면 된다.
(국어/과학 실제 로직은 이번 의뢰 범위 밖이며, 지금은 이 분리 구조만 준비해둔다.)
"""
from __future__ import annotations

AUTO_GRADABLE_TYPES = {"multiple_choice", "short_answer"}


def is_auto_gradable(question_type: str) -> bool:
    """essay(서술형/풀이과정 요구)는 자동채점 대상이 아니다 -> 수동 O/X 체크로 처리."""
    return question_type in AUTO_GRADABLE_TYPES


def is_twin_question_eligible(question_meta: dict) -> bool:
    """도형/그래프/작도 등 그림 자체가 문제 성립에 필수인 유형(shape_dependent=True)은
    쌍둥이 문제 생성이 어려우므로 제외하고, "정답/해설 다시 보기"로 대체한다."""
    return not question_meta.get("shape_dependent", False)
