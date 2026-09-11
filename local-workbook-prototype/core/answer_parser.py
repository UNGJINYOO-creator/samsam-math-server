"""빠른 정답표 파싱 -> answers.json.

핵심 아이디어 (question_split.py의 "예상 다음 번호" 트릭과 동일):
  정답표는 "문항 번호가 1부터 연속으로 증가한다"는 강력한 사전 지식이 있다.
  따라서 텍스트 전체에서 "숫자+구분자(./))" 패턴을 모두 찾고, 그 중
  "직전에 채택한 번호 + 1"과 일치하는 것만 실제 문항 번호로 채택한다.
  이렇게 하면 정답 내용 안에 포함된 숫자(예: "12cm²"의 12, "x=4"의 4)가
  다음 문항 번호로 오인되는 것을 방지할 수 있다.

표 형태든 인라인 텍스트 형태("1.③ 2.① 3.해설참조 4.②")든 PyMuPDF의
get_text()가 대체로 읽는 순서(왼쪽->오른쪽, 위->아래)를 보존하기 때문에
동일한 파서로 처리 가능하다. 레이아웃이 심하게 어긋나는 다단 표는
1분 눈 검수 단계(correct_app.py)에서 사람이 직접 고치는 것으로 커버한다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf as fitz

MARKER_PATTERN = re.compile(r"(\d{1,3})\s*[.\)]")

CIRCLED_DIGITS = {"①", "②", "③", "④", "⑤"}


ESSAY_KEYWORDS = ("해설참조", "풀이참조", "해설 참조", "풀이 참조", "풀이과정")


def infer_answer_type(answer_text: str) -> str:
    """중1 수학 정답 형태를 추정: multiple_choice / short_answer / essay.

    - 객관식: 원문자(①~⑤) 하나, 또는 정답표에 자주 쓰이는 "1"~"5" 단독 숫자
    - 서술형: "해설참조"처럼 정답표에 실제 값 대신 안내문만 있는 경우
      -> 자동채점 대상에서 제외, app.py에서 수동 O/X 체크로 처리
    - 그 외(방정식 해, 단위 포함 숫자, 분수 등): 단답형
    """
    a = answer_text.strip()
    if any(kw in a for kw in ESSAY_KEYWORDS):
        return "essay"
    if a in CIRCLED_DIGITS:
        return "multiple_choice"
    if a.isdigit() and len(a) == 1 and a in {"1", "2", "3", "4", "5"}:
        return "multiple_choice"
    return "short_answer"


def _extract_page_text(pdf_path: str, page_no: int, use_ocr: bool = False) -> str:
    """page_no는 1-based. use_ocr=True면 텍스트 레이어 대신 OCR 결과를 사용한다."""
    if not use_ocr:
        doc = fitz.open(pdf_path)
        text = doc[page_no - 1].get_text()
        doc.close()
        return text

    # OCR 경로 (텍스트 레이어가 없는 스캔본 정답표용).
    # 무거운 딥러닝 OCR(PaddleOCR)까지는 이 개인용 프로토타입에 과하므로
    # 가벼운 Tesseract를 기본으로 사용. 설치 안 되어 있으면 안내 메시지를 낸다.
    try:
        import pytesseract
        from PIL import Image

        doc = fitz.open(pdf_path)
        zoom = 300 / 72.0
        pix = doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        doc.close()
        img_path = Path(f"._ocr_tmp_page_{page_no}.png")
        pix.save(str(img_path))
        text = pytesseract.image_to_string(Image.open(img_path), lang="kor+eng")
        img_path.unlink(missing_ok=True)
        return text
    except ImportError as e:
        raise RuntimeError(
            "OCR 경로가 필요하지만 pytesseract가 설치되어 있지 않습니다.\n"
            "  pip install pytesseract  (+ 시스템에 tesseract-ocr 바이너리 설치 필요, "
            "Mac: brew install tesseract-lang, Windows: 공식 인스톨러)"
        ) from e


def parse_answer_text(text: str) -> dict:
    matches = list(MARKER_PATTERN.finditer(text))
    expected = 1
    accepted: list[tuple[int, re.Match]] = []
    for m in matches:
        no = int(m.group(1))
        if no == expected:
            accepted.append((no, m))
            expected += 1

    answers: dict = {}
    for i, (no, m) in enumerate(accepted):
        start = m.end()
        end = accepted[i + 1][1].start() if i + 1 < len(accepted) else len(text)
        raw_answer = text[start:end].strip()
        # 같은 줄에 다음 문항이 바로 붙어 나오는 경우를 대비해 개행/과도한 공백 정리
        raw_answer = re.sub(r"\s+", " ", raw_answer).strip(" .　")
        if not raw_answer:
            continue
        answers[str(no)] = {
            "answer": raw_answer,
            "type": infer_answer_type(raw_answer),
            # 도형/그래프/작도처럼 그림이 문제 성립에 필수인 유형은 쌍둥이 문제 생성이
            # 어려우므로, 1분 눈 검수 단계(correct_app.py)에서 사람이 체크하면 True로 바뀐다.
            "shape_dependent": False,
        }
    return answers


def parse_answer_pages(pdf_path: str, page_numbers: list[int], use_ocr: bool = False) -> dict:
    """정답표가 있는 페이지 번호들(1-based, 여러 페이지에 걸칠 수 있음)을 합쳐서 파싱."""
    combined_text = "\n".join(_extract_page_text(pdf_path, p, use_ocr=use_ocr) for p in page_numbers)
    return parse_answer_text(combined_text)


LINE_START_MARKER_PATTERN = re.compile(r"^\s*0?(\d{1,3})[.\)]\s")


def parse_explanation_text(text: str) -> dict:
    """"정답과 해설"처럼 문항번호 뒤에 짧은 정답이 아니라 풀이 과정 전체가
    딸려오는 형식을 파싱한다. answer_parser.parse_answer_text와 같은
    "예상 다음 번호" 트릭을 쓰지만, 두 가지가 다르다.
      1) 내용을 한 줄로 뭉치지 않고 문단 그대로 보존한다.
      2) 문항 번호는 반드시 "줄 맨 앞"에서만 인정한다. 해설 문장 안에 있는
         소수(예: 원주율 "3.14")의 "3."을 3번 문항 시작으로 오인해 해설이
         잘리는 것을 막기 위해서다. (실제 교재는 각 문항 해설이 항상 새 줄에서
         시작하므로 이 제약이 안전하다.)

    주의: 일부 출판사 PDF는 수식 기호(도, 원주율, 비율 등)를 특수 폰트로
    인코딩해서, 텍스트로 뽑으면 "!", "@", "`:`" 같은 글자로 깨져 나올 수 있다.
    (해당 폰트의 글자-기호 매핑표가 없으면 일반적인 방법으로는 복원이 어렵다.)
    이 경우 파싱된 해설이 읽기 어려울 수 있으니 1분 눈 검수에서 확인이 필요하다.
    """
    lines = text.split("\n")
    expected = 1
    starts: list[tuple[int, int]] = []  # (line_index, question_no)
    for i, line in enumerate(lines):
        m = LINE_START_MARKER_PATTERN.match(line)
        if m and int(m.group(1)) == expected:
            starts.append((i, expected))
            expected += 1

    explanations: dict = {}
    for i, (line_i, no) in enumerate(starts):
        end_line = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        block = list(lines[line_i:end_line])
        block[0] = LINE_START_MARKER_PATTERN.sub("", block[0], count=1)
        raw = "\n".join(block).strip()
        if raw:
            explanations[str(no)] = raw
    return explanations


def parse_explanation_pages(pdf_path: str, page_numbers: list[int], use_ocr: bool = False) -> dict:
    """"정답과 해설" 책자처럼 문항번호별 풀이 과정 전체가 있는 페이지들을 파싱해
    {문항번호: 해설텍스트} 딱 만든다. run_split_and_parse.py --explanation-pages 에서 사용."""
    combined_text = "\n".join(_extract_page_text(pdf_path, p, use_ocr=use_ocr) for p in page_numbers)
    return parse_explanation_text(combined_text)


def merge_explanations(answers: dict, explanations: dict) -> dict:
    """answers[qno]["explanation"]에 해설 텍스트를 채워 넣는다.
    answers.json에 없는 문항 번호의 해설은 무시한다."""
    for qno, explanation in explanations.items():
        if qno in answers:
            answers[qno]["explanation"] = explanation
    return answers


def save_answers(path: Path, answers: dict) -> None:
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")


def load_answers(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("사용법: python core/answer_parser.py <pdf_path> <answer_page1,answer_page2,...> [--ocr]")
        sys.exit(1)
    pdf_path = sys.argv[1]
    pages = [int(p) for p in sys.argv[2].split(",")]
    use_ocr = "--ocr" in sys.argv
    result = parse_answer_pages(pdf_path, pages, use_ocr=use_ocr)
    print(json.dumps(result, ensure_ascii=False, indent=2))
