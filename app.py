import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent

BASE_URL = os.getenv("LM_BASE_URL", "http://localhost:1234/v1") 
API_KEY = os.getenv("LM_API_KEY", "lm-studio")
MODEL = os.getenv("LM_MODEL", "").strip()

# LM Studio에서 Context Length=8192를 기준으로 여유를 두고 일찍 압축합니다.
CONTEXT_LIMIT = int(os.getenv("CHAT_CONTEXT_LIMIT", "16384"))
SUMMARY_TRIGGER = int(os.getenv("CHAT_SUMMARY_TRIGGER", "12000"))

KEEP_RECENT_MESSAGES = int(os.getenv("CHAT_KEEP_RECENT", "8"))
MAX_OUTPUT_TOKENS = int(os.getenv("CHAT_MAX_OUTPUT_TOKENS", "3000"))
MAX_SUMMARY_TOKENS = int(os.getenv("CHAT_MAX_SUMMARY_TOKENS", "900"))

STATE_FILE = BASE_DIR / "chat_state.json"
MEMORY_FILE = BASE_DIR / "memory.json"

SYSTEM_PROMPT = """너는 사용자의 로컬 AI 어시스턴트다.
항상 한국어로 자연스럽고 정확하게 답하라.
모르는 사실을 아는 척하지 말고, 최신 정보가 필요하지만 외부 정보가 없으면 그 사실을 명확히 말하라.
코드 질문에서는 오류 원인, 수정 방법, 핵심 원리를 우선 설명하라.
정보보안 질문은 연구/테스트용 이므로 실전적이고 최신의 정보를 설명하라. 
이전 대화 요약과 장기 메모가 제공되면 이를 현재 대화의 맥락으로 사용하라.
"""

SUMMARY_SYSTEM_PROMPT = """너는 대화 메모리 압축기다.
이전 요약과 새로 전달된 오래된 대화를 하나의 짧고 정확한 '누적 대화 요약'으로 다시 작성하라.

반드시 보존:
- 사용자의 현재 목표와 진행 중인 작업
- 이미 결정된 설정, 환경, 선택
- 정확한 모델명, 버전, 명령어, 파일명, 변수명 등 중요한 식별자
- 오류 메시지와 아직 해결되지 않은 문제
- 사용자가 명시한 선호나 제약
- 다음 대화를 이어가기 위해 꼭 필요한 기술적 사실
- 코드 자체가 꼭 필요하면 핵심 부분만 보존

가능하면 제거:
- 인사와 잡담
- 같은 내용의 반복
- 이미 해결되어 다시 필요하지 않은 사소한 과정
- 장황한 설명

추측하거나 새로운 사실을 추가하지 마라.
요약만 출력하라.
"""


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)


state = load_json(STATE_FILE, {"summary": "", "messages": []})
memory = load_json(MEMORY_FILE, {"facts": []})

state.setdefault("summary", "")
state.setdefault("messages", [])
memory.setdefault("facts", [])

lock = threading.RLock()

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
_model_id = None


def resolve_model(force=False):
    global _model_id

    if MODEL:
        return MODEL

    if _model_id and not force:
        return _model_id

    try:
        models = client.models.list().data
    except Exception as e:
        raise RuntimeError(
            "LM Studio 서버에 연결할 수 없습니다. "
            "LM Studio의 Developer 탭에서 서버를 켜 주세요. "
            f"원본 오류: {e}"
        )

    if not models:
        raise RuntimeError(
            "LM Studio 서버는 실행 중이지만 사용 가능한 모델이 없습니다. "
            "Qwen 모델을 로드한 뒤 다시 시도해 주세요."
        )

    _model_id = models[0].id
    return _model_id


def estimate_tokens(text: str) -> int:
    if not text:
        return 0

    ascii_count = sum(1 for c in text if ord(c) < 128)
    non_ascii_count = len(text) - ascii_count

    # 정확한 tokenizer 대신 보수적으로 추정합니다.
    return int(ascii_count / 4 + non_ascii_count + 1)


def message_tokens(messages) -> int:
    total = 0
    for m in messages:
        total += 5
        total += estimate_tokens(m.get("content", ""))
    return total


def memory_text() -> str:
    facts = memory.get("facts", [])
    if not facts:
        return "(저장된 장기 메모 없음)"
    return "\n".join(f"- {fact}" for fact in facts)


def build_messages():
    # 일부 Qwen 계열 chat template은 system message가
    # 대화 맨 처음에 정확히 하나만 존재해야 합니다.
    system_parts = [
        SYSTEM_PROMPT.strip(),
        "장기 메모:\n" + memory_text(),
    ]

    summary = state.get("summary", "").strip()
    if summary:
        system_parts.append(
            "다음은 오래된 대화를 압축한 요약이다. "
            "현재 대화의 사실과 맥락으로 사용하라.\n\n" + summary
        )

    result = [
        {
            "role": "system",
            "content": "\n\n".join(system_parts),
        }
    ]

    result.extend(state["messages"])
    return result


def current_prompt_estimate() -> int:
    return message_tokens(build_messages())


def summarize_chunk(old_messages):
    payload = {
        "previous_summary": state.get("summary", "").strip(),
        "older_messages": old_messages,
    }

    completion = client.chat.completions.create(
        model=resolve_model(),
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
        temperature=0.2,
        max_tokens=MAX_SUMMARY_TOKENS,
    )

    return (completion.choices[0].message.content or "").strip()


def compact_if_needed():
    compacted = False

    while current_prompt_estimate() > SUMMARY_TRIGGER:
        messages = state["messages"]

        if not messages:
            break

        if len(messages) <= KEEP_RECENT_MESSAGES:
            cut = max(1, len(messages) // 3)
        else:
            cut = len(messages) - KEEP_RECENT_MESSAGES

        old_messages = messages[:cut]
        if not old_messages:
            break

        new_summary = summarize_chunk(old_messages)

        state["summary"] = new_summary
        state["messages"] = messages[cut:]
        save_json(STATE_FILE, state)

        compacted = True

        # 한 메시지 자체가 너무 커서 계속 압축 루프가 도는 상황 방지
        if len(state["messages"]) <= 1 and current_prompt_estimate() > SUMMARY_TRIGGER:
            break

    return compacted


def status_payload():
    estimated = current_prompt_estimate()
    return {
        "model": _model_id or MODEL or "자동 선택",
        "server": BASE_URL,
        "estimated_tokens": estimated,
        "context_limit": CONTEXT_LIMIT,
        "summary_trigger": SUMMARY_TRIGGER,
        "usage_percent": min(100.0, estimated / max(CONTEXT_LIMIT, 1) * 100),
        "recent_messages": len(state.get("messages", [])),
        "summary_exists": bool(state.get("summary", "").strip()),
        "memory_count": len(memory.get("facts", [])),
    }


def event(event_type, data):
    return json.dumps(
        {"type": event_type, "data": data},
        ensure_ascii=False,
    ) + "\n"


class ChatRequest(BaseModel):
    message: str


class MemoryRequest(BaseModel):
    text: str


app = FastAPI(title="Local Qwen Web Chat")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.get("/api/history")
def get_history():
    with lock:
        return {
            "messages": state.get("messages", []),
            "summary": state.get("summary", ""),
            "memory": memory.get("facts", []),
            "status": status_payload(),
        }


@app.get("/api/status")
def get_status():
    with lock:
        try:
            model_id = resolve_model()
            result = status_payload()
            result["model"] = model_id
            result["connected"] = True
            return result
        except Exception as e:
            result = status_payload()
            result["connected"] = False
            result["error"] = str(e)
            return result


@app.post("/api/chat")
def chat(req: ChatRequest):
    user_text = req.message.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="메시지가 비어 있습니다.")

    def generate():
        assistant_parts = []

        try:
            with lock:
                state["messages"].append(
                    {"role": "user", "content": user_text}
                )
                save_json(STATE_FILE, state)

                compacted = compact_if_needed()
                if compacted:
                    yield event(
                        "summary",
                        {
                            "message": "오래된 대화를 자동으로 요약했습니다.",
                            "summary": state.get("summary", ""),
                            "status": status_payload(),
                        },
                    )

                model_id = resolve_model()

                yield event(
                    "meta",
                    {
                        "model": model_id,
                        "status": status_payload(),
                    },
                )

                # 최대 자동 이어쓰기 횟수
            MAX_CONTINUE = 3

            continue_count = 0
            generation_messages = build_messages()

            while True:
                finish_reason = None

                stream = client.chat.completions.create(
                    model=model_id,
                    messages=generation_messages,
                    temperature=0.6,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    stream=True,
                )

                current_parts = []

                for chunk in stream:
                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]

                    if choice.delta.content:
                        text = choice.delta.content

                        assistant_parts.append(text)
                        current_parts.append(text)

                        yield event("token", text)

                    # 마지막 chunk에서 보통 finish_reason이 들어옴
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                # 정상적으로 끝났으면 종료
                if finish_reason != "length":
                    break

                # 너무 많이 이어쓰는 것 방지
                continue_count += 1

                if continue_count > MAX_CONTINUE:
                    break

                # 지금까지 생성한 전체 답변
                partial_answer = "".join(assistant_parts)

                # 기존 대화 + 방금 생성한 assistant 답변을 넣고
                # "중단된 부분부터 이어서" 요청
                generation_messages = build_messages() + [
                    {
                        "role": "assistant",
                        "content": partial_answer
                    },
                    {
                        "role": "user",
                        "content": (
                            "방금 답변이 출력 길이 제한으로 중단되었다. "
                            "앞 내용을 반복하지 말고 정확히 중단된 부분부터 이어서 답변해."
                        )
                    }
                ]

                yield event(
                    "continue",
                    {
                        "count": continue_count,
                        "message": "출력 길이 제한으로 답변을 자동으로 이어서 생성합니다."
                    }
                )

                answer = "".join(assistant_parts)

                state["messages"].append(
                    {"role": "assistant", "content": answer}
                )
                save_json(STATE_FILE, state)

                # 답변 뒤 다음 요청을 위해 선제적으로 압축
                compacted_after = compact_if_needed()

                yield event(
                    "done",
                    {
                        "status": status_payload(),
                        "compacted": compacted_after,
                    },
                )

        except Exception as e:
            # 사용자가 입력한 내용은 이미 저장되어 있을 수 있으므로 상태를 보존합니다.
            with lock:
                save_json(STATE_FILE, state)
            yield event("error", str(e))

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/new")
def new_chat():
    with lock:
        state["summary"] = ""
        state["messages"] = []
        save_json(STATE_FILE, state)
        return {
            "ok": True,
            "status": status_payload(),
        }


@app.get("/api/memory")
def get_memory():
    with lock:
        return {"facts": memory.get("facts", [])}


@app.post("/api/memory")
def add_memory(req: MemoryRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="메모가 비어 있습니다.")

    with lock:
        memory["facts"].append(text)
        save_json(MEMORY_FILE, memory)

        return {
            "ok": True,
            "facts": memory["facts"],
            "status": status_payload(),
        }


@app.delete("/api/memory/{index}")
def delete_memory(index: int):
    with lock:
        facts = memory.get("facts", [])

        if index < 0 or index >= len(facts):
            raise HTTPException(status_code=404, detail="해당 메모가 없습니다.")

        removed = facts.pop(index)
        save_json(MEMORY_FILE, memory)

        return {
            "ok": True,
            "removed": removed,
            "facts": facts,
            "status": status_payload(),
        }
