@echo off
chcp 65001 >nul
cd /d "%~dp0local-workbook-prototype"

if not "%GEMINI_API_KEY%%NVIDIA_API_KEY%"=="" goto start
echo [안내] AI 키가 설정되어 있지 않습니다.  setx GEMINI_API_KEY "본인키"  로 등록 후 새 창에서 실행하세요.
pause
exit /b

:start
echo 문제 은행 만들기: 문제집을 한 쪽씩 넘기면서 새 문제와 정답을 미리 만들어 둡니다.
echo 무료 한도에 걸리면 기다렸다가 자동으로 이어서 합니다. 창을 닫아도 다음에 이어서 해요.
echo.
python scripts\build_bank.py
echo.
pause
