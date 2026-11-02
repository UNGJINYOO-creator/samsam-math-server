"""앱 안에서 하는 수동 업데이트 (⚙ 관리 → 업데이트 확인 → 업데이트하기).

깃허브에 새 버전이 올라왔는지 확인하고(git fetch), 누르면 받아온 뒤(git pull) 앱을 다시 켠다.
다시 켜는 건 maklee.py(run_maklee.bat이 실행하는 감시 프로그램)가 한다: 서버가 RESTART_CODE로
끝나면 새 코드로 서버를 다시 띄운다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRANCH = os.environ.get("MAKLEE_BRANCH", "claude/pdf-to-workbook-design-a2t7it")
RESTART_CODE = 3


class UpdateError(RuntimeError):
    pass


def _git(*args: str, timeout: int = 90) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        raise UpdateError("이 PC에서 git을 찾지 못했어요. cmd에서 git이 되는지 확인해주세요.") from e
    except subprocess.TimeoutExpired as e:
        raise UpdateError("깃허브 응답이 너무 늦어요. 인터넷 연결을 확인하고 다시 시도해주세요.") from e
    if r.returncode != 0:
        raise UpdateError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "git 오류")
    return r.stdout.strip()


def version() -> dict:
    try:
        return {"commit": _git("rev-parse", "--short", "HEAD"), "date": _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M")}
    except UpdateError:
        return {"commit": "?", "date": ""}


def check() -> dict:
    """깃허브에 새 버전이 있는지. {"behind": 새 업데이트 수, "latest_date": 최신 버전 날짜}"""
    _git("fetch", "origin", BRANCH)
    behind = int(_git("rev-list", "--count", "HEAD..FETCH_HEAD") or 0)
    latest = _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M", "FETCH_HEAD") if behind else ""
    return {"behind": behind, "latest_date": latest, **version()}


def apply() -> dict:
    """새 버전을 받아오고, 잠시 뒤 앱을 다시 켠다."""
    changed = [line[3:] for line in _git("status", "--porcelain", "--untracked-files=no").splitlines() if line.strip()]
    if changed:
        raise UpdateError("이 PC에서 직접 고친 파일이 있어서 자동 업데이트를 할 수 없어요: " + ", ".join(changed[:5]))
    before = _git("rev-parse", "HEAD")
    _git("pull", "--no-edit", "origin", BRANCH, timeout=180)
    after = _git("rev-parse", "HEAD")
    if before == after:
        return {"updated": False, **version()}
    if "requirements.txt" in _git("diff", "--name-only", before, after).splitlines():
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")], cwd=ROOT)
    threading.Timer(1.5, os._exit, [RESTART_CODE]).start()  # 응답을 보낸 뒤 서버를 끄면 maklee.py가 새 코드로 다시 켠다
    return {"updated": True, "restarting": True, **version()}
