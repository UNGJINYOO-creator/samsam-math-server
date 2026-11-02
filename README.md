# 마클리 (삼삼수학)

문제집 PDF의 한 쪽을 고르면 AI가 그 쪽 개념을 읽고 **숫자를 바꾼 새 문제**를 만들어 출제한다.
태블릿에서 왼쪽은 문제, 오른쪽은 손글씨 풀이 공간. 집 PC에서 켜고 같은 와이파이의 태블릿으로 접속한다.

## 켜는 법 (Windows)

1. AI 키 한 번 등록 (cmd에서, 등록 후 cmd 창을 새로 열어야 적용)
   ```
   setx GEMINI_API_KEY "Gemini 키"
   setx NVIDIA_API_KEY "nvapi-..."      (선택: Gemini 무료 한도에 걸렸을 때 보조 AI)
   ```
2. `run_maklee.bat` 더블클릭 (처음 한 번은 필요한 프로그램을 자동 설치)
3. 검은 창에 나오는 **태블릿에서 접속: http://192.168.x.x:8000** 주소를 태블릿 크롬에 입력
   - 태블릿 크롬 메뉴 → "홈 화면에 추가"를 누르면 앱처럼 아이콘으로 열 수 있다.
   - 이 PC에서는 http://localhost:8000

문제집 PDF는 `local-workbook-prototype` 폴더에 넣는다.

## 기능

- 📖 **문제집으로 풀기**: 쪽 선택(미리보기) → AI가 개념·유형 파악 + 새 문제 6개 (AI 요청 1번)
  - 문제가 떨어질 즈음 뒤에서 미리 더 만든다 (글자만 보내는 요청 1번에 6문제)
- ✨ **오늘의 문제**: 문제집 없이 중1/중2/중3 교육과정에서 골고루
- 🔁 **틀린 유형 복습**: 틀린 유형만 모아 새 문제로. 한 유형을 3번 연속 한 번에 맞히면 목록에서 빠진다
- 📊 **기록**: 오늘 푼 문제, 첫 시도 정답률, 유형별 정답률
- 채점: 답(보기 버튼/입력칸)은 AI 없이 즉시 채점. 틀리면 힌트 → 다시 풀기 → 그래도 틀리면 정답·풀이 + 빈칸 채우기
  → "같은 유형 한 문제 더" / "✍️ 내 풀이 어디가 틀렸는지 보기"(손글씨를 AI가 보고 알려줌, 이때만 AI 요청 1번)
- 정답 검산: 계산으로 확인할 수 있는 문제는 AI가 낸 정답을 파이썬이 분수로 다시 계산해서, 다르면 그 문제는 내지 않는다.

## 구조

- `main.py` — 웹 서버 (FastAPI). 화면은 `static/index.html` 한 장.
- `local-workbook-prototype/practice_engine.py` — 문제 세트 저장, 채점 기록, 복습, 미리 만들기
- `local-workbook-prototype/subjects/math/problem_maker.py` — AI 문제 생성 + 정답 검산
- `local-workbook-prototype/subjects/math/handwriting.py` — 손글씨 풀이 피드백
- 푼 기록과 만든 문제: `local-workbook-prototype/practice_data/` (git에 올리지 않음)
- 예전 Streamlit 앱(`local-workbook-prototype/live_app.py` 등)은 그대로 남아 있다.
