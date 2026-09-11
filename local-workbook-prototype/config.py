"""프로젝트 전역 경로/상수 설정.

이 프로토타입은 완전 로컬 실행을 전제로 한다.
프로젝트 폴더 구조:
  <project_dir>/
    source.pdf
    pages/        (페이지 원본 이미지, 중간 산출물)
    questions/    (문제별로 잘라낸 최종 이미지, q_01.png ...)
    boundaries.json  (문제 경계 자동/수동 보정 결과)
    answers.json  (정답 데이터)
    log.csv       (풀이 이력)
"""
from pathlib import Path

# 실제 작업 폴더는 각 스크립트 실행 시 --project 인자로 지정한다.
DEFAULT_PROJECT_DIR = Path("./math_grade1_project")

PAGES_DIRNAME = "pages"
QUESTIONS_DIRNAME = "questions"
BOUNDARIES_FILENAME = "boundaries.json"
ANSWERS_FILENAME = "answers.json"
LOG_FILENAME = "log.csv"
SOURCE_PDF_DEFAULT = "source.pdf"

# 페이지 렌더링 해상도 (DPI). 로컬 프로토타입이므로 고해상도보다 처리 속도 우선.
RENDER_DPI = 200

# 정답표 페이지 범위를 자동으로 못 찾을 경우 사람이 지정해야 하는 기본값.
# (실제 사용 시 --answer-pages 인자로 override)
DEFAULT_ANSWER_PAGE_HINT = "마지막 1~3페이지 부근에 있는 경우가 많음"

import os

# 계정/시점에 따라 사용 가능한 모델명이 다를 수 있으므로 환경변수로 override 가능하게 둔다.
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")
