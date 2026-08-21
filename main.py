from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google import genai
import os

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# 형의 API 키를 코드 안에 직접 안전하게 장착했습니다!
client = genai.Client(api_key="AQ.Ab8RN6IaPcPW6b_i8dMpSYWQqKN187rXVC9j_S_y-9qW43cJTA")

@app.get("/")
def read_index():
    return FileResponse("static/index.html")

@app.get("/generate-problem")
def generate_problem():
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents='중학교 1학년 수준의 수학 연산 또는 방정식 문제 1개를 만들어줘. 반드시 문제 내용만 깔끔하게 줘.',
        )
        return {"problem": response.text}
    except Exception as e:
        return {"problem": f"문제 생성 중 오류가 발생했습니다: {str(e)}"}
