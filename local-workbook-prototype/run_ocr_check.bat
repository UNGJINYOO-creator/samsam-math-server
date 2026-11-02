@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo PDF 글자(OCR)가 문제 풀이에 쓸 만큼 깨끗한지 무작위 5쪽을 검사합니다. (AI 사용 안 함)
echo 특정 쪽을 보려면 cmd에서: python scripts\show_text.py --pages 20 74 105
echo.
python scripts\show_text.py
pause
