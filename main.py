from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google import genai
import os

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# Render에 등록된 환경 변수 키를 명시적으로 가져와서 클라이언트를 초기화합니다.
api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

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
