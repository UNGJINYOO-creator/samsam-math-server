from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google import genai
import os

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# 안전하게 제미나이 클라이언트 초기화 (환경 변수 자동 인식)
try:
    client = genai.Client()
except Exception as e:
    client = None

@app.get("/")
def read_index():
    return FileResponse("static/index.html")

@app.get("/generate-problem")
def generate_problem():
    try:
        if not client:
            return {"problem": "AI 클라이언트가 초기화되지 않았습니다. API 키를 확인해주세요."}
            
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents='중학교 1학년 수준의 수학 연산 또는 방정식 문제 1개를 만들어줘. 반드시 문제 내용만 깔끔하게 줘.',
        )
        return {"problem": response.text}
    except Exception as e:
        return {"problem": f"문제 생성 중 오류가 발생했습니다: {str(e)}"}
