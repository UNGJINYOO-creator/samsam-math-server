"""토스(Toss) 스타일 공용 CSS. app.py, correct_app.py에서 공유.

색/폰트 값은 .streamlit/config.toml의 [theme] 값과 맞춰뒀다.
"""
import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif;
}

.block-container {
    padding-top: 2.5rem;
    padding-bottom: 3rem;
}

h1, h2, h3 {
    font-weight: 700;
    color: #191F28;
    letter-spacing: -0.02em;
}

/* 버튼: 둥글고 그림자 살짝, 눌렀을 때 톤다운 */
div.stButton > button {
    border-radius: 14px;
    border: none;
    padding: 0.6rem 1.1rem;
    font-weight: 600;
    transition: transform 0.05s ease-in-out, opacity 0.15s ease-in-out;
    box-shadow: 0 1px 2px rgba(25, 31, 40, 0.06);
}
div.stButton > button:hover {
    opacity: 0.88;
}
div.stButton > button:active {
    transform: scale(0.97);
}
div.stButton > button[kind="primary"] {
    background-color: #3182F6;
    color: #FFFFFF;
}

/* 텍스트 입력 필드: 둥근 카드형 */
div[data-testid="stTextInput"] input {
    border-radius: 12px;
    border: 1.5px solid #E5E8EB;
    padding: 0.6rem 0.9rem;
}
div[data-testid="stTextInput"] input:focus {
    border-color: #3182F6;
    box-shadow: 0 0 0 2px rgba(49, 130, 246, 0.15);
}

/* 이미지(문제 카드): 둥근 테두리 + 그림자로 카드 느낌 */
div[data-testid="stImage"] img {
    border-radius: 16px;
    border: 1px solid #F2F4F6;
    box-shadow: 0 2px 10px rgba(25, 31, 40, 0.06);
}

/* 알림 박스(success/error/info/warning): 둥글게 */
div[data-testid="stAlert"] {
    border-radius: 14px;
}

/* 진행률 바: 브랜드 블루 */
div[data-testid="stProgress"] > div > div > div {
    background-color: #3182F6;
}

/* 사이드바 배경 */
section[data-testid="stSidebar"] {
    background-color: #F2F4F6;
}
</style>
"""


def inject_toss_style() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def inject_centered_card_width(max_width_px: int = 720) -> None:
    """문제 카드 화면(app.py)처럼 폭을 좁게 가운데 정렬하고 싶을 때만 추가로 호출."""
    st.markdown(f"<style>.block-container {{ max-width: {max_width_px}px; }}</style>", unsafe_allow_html=True)
