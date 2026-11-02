"""NVIDIA API (build.nvidia.com) — Gemini 무료 한도에 걸렸을 때 쓰는 보조 AI.

OpenAI와 같은 요청 형식(/v1/chat/completions)이다. 문제를 사진으로 보내야 하므로
이미지를 받는 모델만 쓴다.

쓸 모델: 환경변수 NVIDIA_MODEL로 정할 수 있다. 없으면 처음 쓸 때 /v1/models 목록에서
VISION_MODEL_PREFERENCE 순서(사용자가 원한 Mistral 계열 먼저)로 후보를 찾고, 작은 그림을 하나
보내서 실제로 이미지를 받아주는 첫 모델을 고른다. (이름만 보고 고르면 글자만 받는 모델이
섞일 수 있어서 직접 확인한다.)

키: 환경변수 NVIDIA_API_KEY (nvapi-로 시작). 코드에 적지 않는다.
"""
from __future__ import annotations

import base64
import io
import json
import os
import threading
import urllib.error
import urllib.request

from subjects.math.gemini_client import GeminiError, QuotaExceededError

BASE_URL = "https://integrate.api.nvidia.com/v1"
TIMEOUT_SECONDS = 150  # 무료 서버는 줄이 밀리면 느리다. 이보다 오래 걸리면 포기하고 알려준다
MAX_ANSWER_TOKENS = 1500  # 문제 1개 답(JSON)에 충분한 길이. 길게 열어두면 그만큼 느려진다
MAX_IMAGE_SIDE = 1280
MAX_IMAGE_BYTES = 170_000  # NVIDIA는 요청 안에 바로 넣는 이미지 크기에 제한이 있어 넉넉히 작게

# 이미지 입력을 받는 것으로 알려진 모델 이름의 일부. 위에서부터 우선.
VISION_MODEL_PREFERENCE = [
    "mistral-large-3", "mistral-medium-3", "ministral-3", "mistral-small-3.1", "mistral-small-3.2", "pixtral",
    "gemma-3-27b", "llama-4-maverick", "llama-4-scout", "qwen3-vl", "qwen2.5-vl",
    "llama-3.2-90b-vision", "phi-4-multimodal", "nemotron-nano-vl", "llama-3.2-11b-vision",
]

_model_lock = threading.Lock()
_chosen_model: str | None = None


class NvidiaRateLimitError(QuotaExceededError):
    def __init__(self):
        self.retry_seconds = None
        RuntimeError.__init__(self, "보조 AI(NVIDIA)도 사용량 제한에 걸렸어요. 잠시 뒤에 다시 시도해주세요.")


class NvidiaRejectedError(GeminiError):
    """요청 자체를 거절당함(400/404/422 등). 모델이 이미지를 안 받거나 없는 모델일 때."""


class NvidiaTimeoutError(GeminiError):
    """NVIDIA가 제한 시간 안에 답을 주지 않음 (무료 서버가 붐비거나 모델이 느림)."""


class NvidiaResponse:
    def __init__(self, text: str, model: str):
        self.text = text
        self.provider = "nvidia"
        self.model = model


def nvidia_available() -> bool:
    return bool(os.environ.get("NVIDIA_API_KEY"))


def _request(path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        if e.code == 429:
            raise NvidiaRateLimitError() from e
        if e.code in (401, 403):
            raise GeminiError("NVIDIA API 키가 맞지 않아요. NVIDIA_API_KEY를 확인해주세요.") from e
        if 400 <= e.code < 500:
            raise NvidiaRejectedError(f"NVIDIA가 요청을 거절했어요 ({e.code}): {body}") from e
        raise GeminiError(f"NVIDIA 서버 오류 {e.code}: {body}") from e
    except TimeoutError as e:  # 응답을 기다리다 시간 초과 (socket.timeout도 여기에 해당)
        raise NvidiaTimeoutError(f"보조 AI(NVIDIA)가 {TIMEOUT_SECONDS}초 안에 답하지 않았어요. 서버가 붐비거나 모델이 느려요.") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise NvidiaTimeoutError(f"보조 AI(NVIDIA)가 {TIMEOUT_SECONDS}초 안에 답하지 않았어요. 서버가 붐비거나 모델이 느려요.") from e
        raise GeminiError(f"NVIDIA 서버에 연결하지 못했어요: {e.reason}") from e


def _image_data_url(image) -> str:
    """PIL 이미지를 크기 제한 안쪽의 JPEG data URL로. 화질을 먼저 낮추고, 그래도 크면 크기를 줄인다."""
    img = image.convert("RGB")
    if max(img.size) > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    while True:
        for quality in (85, 70, 55, 40):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= MAX_IMAGE_BYTES:
                return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        img.thumbnail((int(img.width * 0.8), int(img.height * 0.8)))


def _chat(model: str, contents: list, max_tokens: int = MAX_ANSWER_TOKENS) -> str:
    parts = []
    for item in contents:
        if isinstance(item, str):
            parts.append({"type": "text", "text": item})
        else:
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(item)}})
    data = _request("/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": parts}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    })
    return data["choices"][0]["message"]["content"] or ""


def all_model_ids() -> list[str]:
    return sorted(m["id"] for m in _request("/models").get("data", []))


def list_vision_candidates() -> list[str]:
    """/v1/models에서 선호 목록에 걸리는 모델들을 선호 순서대로."""
    ids = all_model_ids()
    ordered: list[str] = []
    for key in VISION_MODEL_PREFERENCE:
        for mid in sorted(ids):
            if key in mid.lower() and mid not in ordered:
                ordered.append(mid)
    return ordered


def accepts_images(model: str) -> bool:
    from PIL import Image

    try:
        _chat(model, ["이 그림의 색을 한 단어로 답해줘.", Image.new("RGB", (64, 64), "red")], max_tokens=10)
        return True
    except NvidiaRejectedError:
        return False  # 키 오류·연결 오류·사용량 제한은 그대로 올려서 원인을 정확히 보여준다


def use_model(model: str) -> None:
    """비교 스크립트에서 특정 모델로 바꿔 시험할 때."""
    global _chosen_model
    with _model_lock:
        _chosen_model = model


def chosen_model() -> str:
    global _chosen_model
    with _model_lock:
        if _chosen_model:
            return _chosen_model
        override = os.environ.get("NVIDIA_MODEL")
        if override:
            _chosen_model = override
            return override
        for mid in list_vision_candidates():
            if accepts_images(mid):
                _chosen_model = mid
                return mid
        raise GeminiError("NVIDIA에서 이미지를 읽을 수 있는 모델을 찾지 못했어요. NVIDIA_MODEL 환경변수로 모델을 직접 정해주세요.")


def generate(contents: list) -> NvidiaResponse:
    model = chosen_model()
    return NvidiaResponse(_chat(model, contents), model)
