# Local Qwen Web Chat

LM Studio에서 실행 중인 로컬 모델을 웹 브라우저에서 사용하는 간단한 ChatGPT 스타일 UI입니다.

## 기능

- FastAPI 백엔드
- HTML/CSS/JavaScript 프론트엔드
- LM Studio OpenAI 호환 API 연결
- AI 답변 실시간 스트리밍
- Markdown 렌더링
- 코드 블록 복사 버튼
- 대화 기록 자동 저장
- 오래된 대화 자동 요약
- 최근 메시지는 원문 유지
- 현재 context 사용량 표시
- 장기 메모 추가/삭제
- 새 채팅
- 프로그램 재실행 후 이전 대화 복원

## 1. LM Studio 준비

1. LM Studio에서 Qwen3.5-9B 등을 로드합니다.
2. Developer 탭에서 Local Server를 시작합니다.
3. 기본 주소는 `http://localhost:1234` 입니다.
4. Context Length는 우선 `8192` 정도를 권장합니다.

## 2. 설치

Windows PowerShell 또는 CMD에서 프로젝트 폴더로 이동한 뒤:

```bash
pip install -r requirements.txt
```

## 3. 실행

```bash
uvicorn app:app --reload
```

그 다음 브라우저에서:

```text
http://127.0.0.1:8000
```

으로 접속합니다.

## 모델 직접 지정

LM Studio에 여러 모델이 잡히면 환경변수로 모델 ID를 고정할 수 있습니다.

PowerShell:

```powershell
$env:LM_MODEL="LM Studio에 표시되는 정확한 모델 ID"
uvicorn app:app --reload
```

CMD:

```cmd
set LM_MODEL=LM Studio에 표시되는 정확한 모델 ID
uvicorn app:app --reload
```

## Context 설정

기본값:

- LM Studio context: 8192
- 웹앱 표시 context limit: 8192
- 자동 요약 시작: 약 5500 token 추정

PowerShell에서 변경 예:

```powershell
$env:CHAT_CONTEXT_LIMIT="16384"
$env:CHAT_SUMMARY_TRIGGER="12000"
uvicorn app:app --reload
```

LM Studio의 Context Length도 같은 수준 이상으로 설정해야 합니다.

## 생성 파일

실행하면 프로젝트 폴더에 다음 데이터가 저장됩니다.

- `chat_state.json`: 누적 요약 + 최근 대화
- `memory.json`: 장기 메모

## 참고

프론트엔드의 Markdown 렌더링은 CDN의 `marked`와 `DOMPurify`를 사용합니다.
완전한 오프라인 UI를 원하면 이 두 JavaScript 파일을 프로젝트에 내려받아 로컬 정적 파일로 바꾸면 됩니다.
