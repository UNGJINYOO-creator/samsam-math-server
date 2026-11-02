@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 자동 정답 파싱이 많이 틀렸을 때 쓰는 기능입니다.
echo 먼저 메모장으로 텍스트 파일을 하나 만들어서, 한 줄에 한 문항씩
echo "1. 정답" 형식으로 책을 보면서 쭉 타이핑해두세요.
echo.

set /p PROJECT=프로젝트 폴더 이름을 입력하세요 (예: sample_project):
set /p TEXTFILE=정답을 적어둔 텍스트 파일 이름을 입력하세요 (예: answers_manual.txt):

if "%PROJECT%"=="" (
    echo 프로젝트 폴더 이름을 입력하지 않아서 종료합니다.
    pause
    exit /b
)

python scripts\answers_from_text.py --project "%PROJECT%" --text "%TEXTFILE%"
pause
