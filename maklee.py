"""마클리 실행기: 서버를 켜고, 앱 안에서 업데이트하면 새 버전으로 다시 켠다. (run_maklee.bat이 실행)

이 파일은 처음 한 번 읽힌 뒤로는 바뀌어도 상관없게 단순하게 둔다.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESTART_CODE = 3


def ensure_packages() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("필요한 프로그램을 설치하는 중이에요 (처음 한 번만)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], cwd=ROOT)


def main() -> None:
    ensure_packages()
    while True:
        print("마클리를 켭니다. 아래에 나오는 태블릿 접속 주소로 들어가세요. 이 창은 닫지 마세요.", flush=True)
        code = subprocess.call([sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"], cwd=ROOT)
        if code != RESTART_CODE:
            print("\n마클리가 꺼졌습니다. 위에 오류 글자가 있으면 이 창을 캡처해서 보내주세요.")
            return
        print("\n업데이트를 받아서 새 버전으로 다시 켭니다...\n", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
