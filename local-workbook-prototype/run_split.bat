@echo off
chcp 65001 >nul
cd /d "%~dp0"

set /p PDF=PDF 파일 이름을 입력하세요 (예: math.pdf):
set /p PROJECT=만들 프로젝트 폴더 이름을 입력하세요 (예: math_project):
set /p ANSWER_PAGES=정답표가 있는 페이지 번호(콤마로 구분, 예: 41,42):
set /p EXPLANATION_PAGES=(선택) 전체 해설이 있는 페이지 번호, 없으면 그냥 엔터:

if "%PDF%"=="" (
    echo PDF 파일명을 입력하지 않아서 종료합니다.
    pause
    exit /b
)

if "%EXPLANATION_PAGES%"=="" (
    python scripts\run_split_and_parse.py --project "%PROJECT%" --pdf "%PDF%" --answer-pages "%ANSWER_PAGES%"
) else (
    python scripts\run_split_and_parse.py --project "%PROJECT%" --pdf "%PDF%" --answer-pages "%ANSWER_PAGES%" --explanation-pages "%EXPLANATION_PAGES%"
)

pause
