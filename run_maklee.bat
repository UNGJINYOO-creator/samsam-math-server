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
python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 python -m pip install -r requirements.txt
echo 마클리를 켭니다. 아래에 나오는 태블릿 접속 주소로 들어가세요. 이 창은 닫지 마세요.
python -m uvicorn main:app --host 0.0.0.0 --port 8000
echo.
echo 마클리가 꺼졌습니다. 위에 오류 글자가 있으면 이 창을 캡처해서 보내주세요.
pause
