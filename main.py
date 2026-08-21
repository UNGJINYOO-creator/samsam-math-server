from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google import genai
import os

app = FastAPI()

# static 폴더 안에 있는 파일들을 열어줍니다.
app.mount("/static", StaticFiles(directory="static"), name="static")

# 제미나이 클라이언트 초기화
client = genai.Client()

# 1. 주소창에 그냥 접속했을 때 (https://samsam-math-server.onrender.com)
@app.get("/")
def read_index():
    # 이제 메시지가 아니라, static 폴더 안의 index.html 화면을 보여줍니다!
    return FileResponse("static/index.html")

# 2. 버튼을 눌렀을 때 문제를 만들어주는 기능
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
