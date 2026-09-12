@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%GEMINI_API_KEY%"=="" (
    echo [안내] GEMINI_API_KEY가 설정되어 있지 않습니다.
    echo        힌트/해설/쌍둥이문제 기능은 동작하지 않고, 문제 풀이/채점은 정상 작동합니다.
    echo        한 번만 등록하려면 이 창에서: setx GEMINI_API_KEY "본인키"
    echo        (등록 후에는 이 창을 닫고 새로 열어야 적용됩니다)
    echo.
)

python -m streamlit run app.py
pause
