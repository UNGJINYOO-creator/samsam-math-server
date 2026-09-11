# 개인용 문제집 디지털화 & 오답 재풀이 (로컬 프로토타입)

완전 로컬 실행 전용 프로토타입. 회원가입/로그인/결제/클라우드/서버 배포 전혀 없음.
PC 1대에서만 실행하고, 태블릿은 같은 집 와이파이 대역에서 브라우저로 접속한다.

기존 "마클리" 앱(`main.py`, `static/`)과는 완전히 독립된 별개 프로젝트다. 이 폴더
안의 코드만으로 동작하며, 상위 저장소의 다른 코드를 참조하지 않는다.

## 0. 설치

```bash
cd local-workbook-prototype
pip install -r requirements.txt

# Gemini API 키는 코드에 하드코딩하지 않고 환경변수로만 넘긴다.
export GEMINI_API_KEY="여기에 본인 키"
```

## 1. 사용 순서 (0일차 -> 3일차)

### 0일차 (30분 이내) — 텍스트 레이어 확인 (가장 먼저 할 것)

```bash
python scripts/run_step0_check_text_layer.py ./math_grade1.pdf
```

- 모든 페이지가 `TEXT`로 나오면 -> 순수 좌표/텍스트 분할로 충분.
- 일부/전부가 `IMAGE-ONLY`로 나오면 -> 해당 페이지는 OCR 경로(`--answer-ocr`,
  `pytesseract` 설치)가 필요하고, 문제 분할도 자동이 아니라 `correct_app.py`에서
  수동으로 경계를 나눠야 한다.

### 1일차 — 문제 분할 + 정답표 파싱

```bash
python scripts/run_split_and_parse.py \
  --project ./math_grade1_project \
  --pdf ./math_grade1.pdf \
  --answer-pages 41,42
```

`--answer-pages`는 책 뒤편 "빠른 정답표"가 있는 실제 PDF 페이지 번호(1-based)를
직접 확인해서 넘겨준다 (책마다 위치가 달라 자동 탐지가 불안정하기 때문에 사람이
지정하는 것으로 설계했다).

결과물:
```
math_grade1_project/
├── pages/            # 페이지별 원본 이미지 (중간 산출물)
├── questions/        # q_01.png, q_02.png ... (문제별 최종 이미지)
├── boundaries.json   # 문제 경계 (자동 탐지 + 수동 보정 반영)
└── answers.json       # 문항별 정답/유형(객관식·단답형·서술형)/도형여부
```

### 1분 눈 검수 — 정답 확인 + 경계 보정

```bash
streamlit run correct_app.py -- --project ./math_grade1_project
```

- **정답 검수 탭**: 문항별 정답/유형/도형여부를 훑어보고 잘못된 것만 고친 뒤
  "모두 저장".
- **문제 경계 보정 탭**: 자동 분할이 실패했거나 어긋난 문제를, 페이지 이미지를
  클릭해서 나온 y좌표를 표에 입력하는 방식으로 보정("클릭 2번"으로 시작/끝 지정).
  `streamlit-image-coordinates`가 설치되어 있으면 클릭 즉시 좌표가 표시된다.

### 2~3일차 — 태블릿 풀이

```bash
streamlit run app.py --server.address 0.0.0.0
```

터미널에 출력되는 `Network URL` (예: `http://192.168.0.12:8501`)을 태블릿
브라우저에 입력해 접속한다. PC와 태블릿이 **같은 와이파이**에 연결되어 있어야 한다.

화면에서:
1. 사이드바에서 단원(프로젝트 폴더)을 선택하고 "이어서 풀기" 또는 "처음부터".
2. 문제를 카드로 넘기며 풀이 -> 제출 즉시 자동채점.
3. 오답이면 [쌍둥이 문제 도전](Gemini가 이미지 그대로 보고 유사 문제 생성) 또는
   [넘어가기].
4. 도형/그래프/작도처럼 그림이 필수인 문제는 "정답 검수" 탭에서 도형 체크를
   해두면 쌍둥이 생성 대신 "정답/해설 다시 보기"로 대체된다.
5. 단원을 다 풀면 결과 요약 화면(정답률, 틀린 문제, 재도전 후 이해/여전히 틀림).

풀이 이력은 `math_grade1_project/log.csv`에 누적 저장된다 (분석 화면은 없음,
저장만 함).

## 2. 폴더 구조

```
local-workbook-prototype/
├── config.py                  # 경로/상수 설정
├── core/                       # 과목 공통 파이프라인
│   ├── pdf_analysis.py         # 0단계: 텍스트 레이어 확인
│   ├── page_render.py          # PDF -> 페이지 이미지
│   ├── question_split.py       # 문제 경계 자동분할 + 수동보정 데이터 모델
│   ├── answer_parser.py        # 정답표 파싱 -> answers.json
│   ├── grading.py               # 정답 정규화/비교
│   └── session_log.py          # log.csv 기록/집계
├── subjects/
│   └── math/                   # 수학 과목 전용 플러그인
│       ├── answer_rules.py     # 자동채점 가능 여부, 쌍둥이문제 가능 여부
│       └── twin_question.py    # Gemini 쌍둥이 문제 생성
├── scripts/
│   ├── run_step0_check_text_layer.py
│   └── run_split_and_parse.py
├── correct_app.py               # 1분 눈 검수 (Streamlit)
└── app.py                        # 태블릿 풀이 화면 (Streamlit)
```

## 3. 향후 확장 (지금 구현 안 됨, 구조만 고려)

국어/과학 등 다른 과목을 추가할 때는 `subjects/korean/`, `subjects/science/`
아래에 `answer_rules.py`(자동채점/재도전 가능 여부 규칙)와 같은 이름의 인터페이스를
구현한 모듈을 추가하고, `app.py`가 과목 선택에 따라 불러오는 모듈만 바꾸면 된다.
지문 기반 문제 그룹핑(국어) 등 과목별 로직은 이번 범위가 아니다.

## 4. 알아두어야 할 한계 (개인용 프로토타입이므로 의도적으로 생략한 것)

- 인증/멀티유저/권한 없음 — 같은 와이파이에 있는 누구나 접속 가능한 로컬 서비스.
- 표 형태 정답표는 "번호가 1부터 순서대로 증가한다"는 가정에 기반한 정규식 파서로
  처리한다. 레이아웃이 심하게 어긋나는 다단 표는 자동 파싱이 틀릴 수 있으니
  1분 눈 검수에서 반드시 확인한다.
- 문제 경계가 페이지 3개 이상에 걸치는 경우 등 매우 예외적인 레이아웃은
  `correct_app.py` UI로 편집이 안 되고 `boundaries.json`을 직접 고쳐야 한다.
