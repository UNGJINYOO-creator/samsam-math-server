@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%GEMINI_API_KEY%%NVIDIA_API_KEY%"=="" (
    echo [안내] AI 키가 하나도 설정되어 있지 않습니다.
    echo        Gemini 키:          setx GEMINI_API_KEY "본인키"
    echo        보조 AI(NVIDIA) 키: setx NVIDIA_API_KEY "nvapi-..."
    echo        (등록 후 이 창을 닫고 새로 열어야 적용됩니다)
    echo.
    pause
    exit /b
)

python -m streamlit run live_app.py --server.address 0.0.0.0
pause
