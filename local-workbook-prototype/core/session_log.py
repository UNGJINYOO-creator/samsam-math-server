"""풀이 이력 로그 (log.csv) 기록/조회.

분석 화면은 이번 범위가 아니므로 저장 + 아주 단순한 집계 함수만 제공한다.
pandas 없이 표준 csv 모듈만 사용해 의존성을 최소화한다.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

FIELDNAMES = [
    "timestamp",
    "unit",
    "question_no",
    "question_type",
    "is_retry",
    "submitted_answer",
    "is_correct",
]


def append_log(
    log_path: Path,
    unit: str,
    question_no: str,
    question_type: str,
    submitted_answer: str,
    is_correct: bool,
    is_retry: bool = False,
) -> None:
    is_new = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "unit": unit,
                "question_no": question_no,
                "question_type": question_type,
                "is_retry": is_retry,
                "submitted_answer": submitted_answer,
                "is_correct": is_correct,
            }
        )


def read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    with log_path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def unit_review_summary(log_path: Path, unit: str) -> dict:
    """한 단원(unit)에 대해 "첫 시도 정답률"과 "오답 중 재도전 후 이해한 문제 /
    여전히 틀리는 문제"를 구분해 반환. app.py의 결과 요약 화면에서 사용."""
    rows = [r for r in read_log(log_path) if r["unit"] == unit]

    first_attempt: dict[str, bool] = {}
    retry_success: dict[str, bool] = {}
    for row in rows:
        qno = row["question_no"]
        is_retry = row["is_retry"] in ("True", "true", True)
        is_correct = row["is_correct"] in ("True", "true", True)
        if not is_retry:
            first_attempt.setdefault(qno, is_correct)
        elif is_correct:
            retry_success[qno] = True

    def sort_key(q: str):
        return (0, int(q)) if q.isdigit() else (1, q)

    total = len(first_attempt)
    correct_first = sum(1 for v in first_attempt.values() if v)
    wrong_qnos = sorted((q for q, v in first_attempt.items() if not v), key=sort_key)
    understood_after_retry = [q for q in wrong_qnos if retry_success.get(q)]
    still_wrong = [q for q in wrong_qnos if q not in retry_success]

    return {
        "total": total,
        "correct_first": correct_first,
        "accuracy": round(correct_first / total, 3) if total else 0.0,
        "wrong_qnos": wrong_qnos,
        "understood_after_retry": understood_after_retry,
        "still_wrong": still_wrong,
    }


def answered_question_nos(log_path: Path, unit: str) -> set[str]:
    """이 단원에서 (재도전 포함 여부와 무관하게) 최초 시도가 한 번이라도 기록된 문항 번호 집합."""
    rows = [r for r in read_log(log_path) if r["unit"] == unit and r["is_retry"] in ("False", "false", False)]
    return {r["question_no"] for r in rows}


def summarize_by_unit(log_path: Path) -> dict:
    """단원(unit)별 누적 정답률. 같은 문항을 여러 번 풀면 가장 마지막 시도만 반영."""
    rows = read_log(log_path)
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["unit"], row["question_no"])
        latest[key] = row  # 뒤에서 읽은 값이 최신이므로 그냥 덮어쓰면 마지막 시도가 남음

    summary: dict[str, dict] = {}
    for (unit, _qno), row in latest.items():
        s = summary.setdefault(unit, {"total": 0, "correct": 0})
        s["total"] += 1
        if row["is_correct"] in ("True", "true", True):
            s["correct"] += 1

    for unit, s in summary.items():
        s["accuracy"] = round(s["correct"] / s["total"], 3) if s["total"] else 0.0

    return summary
