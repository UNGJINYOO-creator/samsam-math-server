@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%GEMINI_API_KEY%"=="" (
    echo [안내] GEMINI_API_KEY가 설정되어 있지 않아 AI 풀이가 동작하지 않습니다.
    echo        setx GEMINI_API_KEY "본인키" 로 한 번 등록 후 새 창에서 다시 실행해주세요.
    pause
    exit /b
)

echo 정답표를 읽는 대신, 문제 이미지를 AI가 하나씩 직접 풀어서 정답을 만듭니다.
echo 먼저 run_split.bat으로 문제 분할까지는 끝내둔 프로젝트여야 합니다.
echo.

set /p PROJECT=프로젝트 폴더 이름을 입력하세요 (예: sample_project):

if "%PROJECT%"=="" (
    echo 프로젝트 폴더 이름을 입력하지 않아서 종료합니다.
    pause
    exit /b
)

python scripts\solve_with_ai.py --project "%PROJECT%"
pause
