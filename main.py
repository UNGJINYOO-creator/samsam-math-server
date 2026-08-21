from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from google import genai
import httpx
import json

app = FastAPI()

# 1. 화면(정적 파일)을 서비스하겠다고 선언
app.mount("/static", StaticFiles(directory="static"), name="static")

# 설정 정보
GEMINI_API_KEY = "AQ.Ab8RN6IaPcPW6b_i8dMpSYWQqKN187rXVC9j_S_y-9qW43cJTA"
TELEGRAM_BOT_TOKEN = "8978029216:AAGDDxhgzfWHs2S6nlwfKl4BxCub9LbfOPI"
TELEGRAM_CHAT_ID = "8963543201"

client = genai.Client(api_key=GEMINI_API_KEY)

class MathProblem(BaseModel):
    question: str
    student_answer: str

async def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    async with httpx.AsyncClient() as client_http:
        await client_http.post(url, json=payload)

@app.get("/")
def read_root():
    return {"message": "삼삼수학 AI 서버가 정상적으로 동작 중입니다! 🚀"}

@app.post("/api/check-math")
async def check_math_problem(data: MathProblem):
    try:
        prompt = f"""
        너는 초·중등 수학 전문 친절하고 귀여운 AI 과외 선생님이야. 
        아이가 푼 수학 문제를 보고 정답 여부를 판별하고, 친절하게 해설해 줘.
        
        [문제]: {data.question}
        [학생의 답]: {data.student_answer}
        
        반드시 아래 JSON 형식으로만 답해줘:
        {{
            "is_correct": true 또는 false,
            "feedback": "아이에게 들려줄 다정한 칭찬과 해설 (줄바꿈은 \\n 사용)"
        }}
        """

        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt,
        )
        
        raw_text = response.text.replace("```json", "").replace("```", "").strip()
        result_json = json.loads(raw_text)
        
        is_correct = result_json.get("is_correct", False)
        feedback = result_json.get("feedback", "")
        
        status_emoji = "정답입니다! 🎉" if is_correct else "틀렸어요, 다시 도전! 💪"
        
        telegram_text = (
            f"📐 [삼삼수학 실시간 학습 리포트]\n\n"
            f"📝 문제: {data.question}\n"
            f"✏️ 아들의 답: {data.student_answer}\n"
            f"────────────────\n"
            f"🎯 채점 결과: {status_emoji}\n\n"
            f"💬 AI 선생님 코멘트:\n{feedback}"
        )
        
        await send_telegram_message(telegram_text)
        
        return {
            "status": "success",
            "is_correct": is_correct,
            "ai_review": feedback
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))