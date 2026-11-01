@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%GEMINI_API_KEY%"=="" (
    echo [안내] GEMINI_API_KEY가 설정되어 있지 않습니다.
    echo        문제를 처음 볼 때 AI가 풀어주는 기능이 꼭 필요하니, 먼저 등록해주세요:
    echo        setx GEMINI_API_KEY "본인키"  (등록 후 새 창에서 다시 실행)
    echo.
    pause
    exit /b
)

python -m streamlit run live_app.py
pause
