from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google import genai
import os

app = FastAPI()

# 1. 화면(정적 파일)을 서비스하겠다고 선언
app.mount("/static", StaticFiles(directory="static"), name="static")

# 제미나이 클라이언트 초기화
client = genai.Client()

# 2. 주소창에 그냥 접속했을 때 index.html 화면을 보여줍니다.
@app.get("/")
def read_index():
    return FileResponse("static/index.html")

# 3. 버튼을 눌렀을 때 제미나이가 문제를 만들어주는 기능
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
