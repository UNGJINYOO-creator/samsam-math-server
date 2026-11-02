@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not "%GEMINI_API_KEY%%NVIDIA_API_KEY%"=="" goto start
echo [안내] AI 키가 하나도 설정되어 있지 않습니다.
echo        Gemini 키:          setx GEMINI_API_KEY "본인키"
echo        보조 AI NVIDIA 키:  setx NVIDIA_API_KEY "nvapi-..."
echo        등록 후 이 창을 닫고 새로 열어야 적용됩니다.
echo.
pause
exit /b

:start
python -m streamlit run live_app.py --server.address 0.0.0.0
echo.
echo 앱이 꺼졌습니다. 위에 오류 글자가 있으면 이 창을 캡처해서 보내주세요.
pause
