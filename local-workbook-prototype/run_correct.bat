@echo off
chcp 65001 >nul
cd /d "%~dp0"

set /p PROJECT=1분 눈 검수할 프로젝트 폴더 이름을 입력하세요 (예: sample_project):

if "%PROJECT%"=="" (
    echo 폴더 이름을 입력하지 않아서 종료합니다.
    pause
    exit /b
)

python -m streamlit run correct_app.py -- --project "%PROJECT%"
pause
