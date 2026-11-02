"""틀린 문제의 손글씨 풀이를 AI에게 보여주고, 어디서 틀렸는지 짧게 알려받는다.

답 채점은 AI 없이 바로 하고(무료·즉시), 이 기능은 학생이 틀린 뒤 "내 풀이 봐줘"를 눌렀을 때만 쓴다
(AI 요청 1번, 이미지 1장 -> Gemini 한도에 걸리면 보조 AI(NVIDIA)로도 갈 수 있음).
"""
from __future__ import annotations

from subjects.math.ai_solver import _plain_math
from subjects.math.gemini_client import generate

PROMPT = """너는 중학생 수학 선생님이야. 이미지는 학생이 아래 문제를 손으로 푼 풀이야.
학생이 낸 답은 틀렸어. 풀이를 보고, 어느 부분에서 무엇을 잘못했는지 학생에게 2~3문장으로 다정하게 알려줘.
- 정답 전체 풀이를 다시 쓰지 말고, 틀린 지점과 이유만.
- 손글씨를 읽을 수 없거나 풀이가 거의 없으면 그렇다고 말하고, 풀이를 적어보라고 권해줘.
- LaTeX 쓰지 말고 일반 글자로 (분수 1/2, 거듭제곱 x^2).

[문제]
{question}
{choices}
[정답] {answer}
[학생이 낸 답] {mine}
[바른 풀이]
{solution}
"""


def explain_mistake(problem: dict, image, mine: str) -> str:
    choices = "\n".join(f"{'①②③④⑤'[i]} {c}" for i, c in enumerate(problem.get("choices") or []))
    prompt = (
        PROMPT.replace("{question}", problem["question"]).replace("{choices}", choices)
        .replace("{answer}", problem["answer"]).replace("{mine}", mine or "(없음)")
        .replace("{solution}", "\n".join(problem.get("steps") or []).replace("[[", "").replace("]]", ""))
    )
    return _plain_math(generate([prompt, image]).text)
