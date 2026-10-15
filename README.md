# KioskProject — 대화 흐름·RAG·주문 통합

카페 자연어 키오스크의 **대화 제어·RAG API·주문 기능·사용자 화면**을 연결한 팀 프로젝트입니다. 통합 코드는 `main`에서 제공합니다.
사용자는 채팅이나 메뉴판으로 메뉴를 선택하고, 옵션·수량을 바꾸며, 매장/포장 선택과 최종 확인을 거쳐 모의 주문번호를 받을 수 있습니다. 화면은 Next.js·TypeScript, 서버는 Python·FastAPI로 구성했습니다.

이 브랜치의 `api/index.py`는 `POST /api/chat`을 제공합니다. 기본 모드는 `api/fake_rag.py`와 카탈로그 기반 시험용 요청 해석기로 동작하며 실제 검색·임베딩·모델 호출을 하지 않습니다. `KIOSK_MODE=live`에서는 명확한 HTI 주문 표현을 기존 규칙으로 처리하고, 나머지 요청은 구조화 LLM 요청 해석기와 `CafeRAGService.ask()`를 사용합니다. 실제 모드는 API 키가 필요합니다. 현재 검증 범위와 남은 화면 확인은 [최종 흐름 검증 기록](docs/FINAL_VERIFICATION.md)에 있습니다.

대화 흐름·통합 구현과 인계 문서는 `a858f0a`에서 `main`에 병합했습니다. HTI 원본은 [엑셀](docs/HTI_키오스크.xlsx)과 [PDF](docs/HTI_키오스크.pdf)로 보존했습니다. 화면 전체 검증과 팀 발표·제출 준비는 별도로 남아 있습니다.

## 동작 흐름

사용자 입력 → 요청 해석 → RAG 조회 또는 주문 액션 생성 → 주문 엔진의 검증·계산 → 화면 반영 순서로 처리합니다. 온도나 확인 질문에 대한 확실한 단답은 화면에서 바로 처리합니다.

- 서버는 메뉴 설명·비교·추천을 조회하고 주문 요청을 `cart_actions`로 변환합니다.
- 화면의 주문 엔진이 장바구니·주문 단계·보류 요청을 관리하고 실제 금액을 계산합니다.
- “아이스요, 치즈케이크도 주세요”처럼 여러 요청이 섞인 입력은 서버에서 해석합니다.
- 온도가 미정인 주문은 초안으로 유지하며, 담기 전에도 샷·수량을 바꿀 수 있습니다.
- “첫 번째 걸로 주세요”는 직전 추천의 표시 순서를 사용합니다. 대상이 불명확하면 확인합니다.

## 구조

```text
app/                 Next.js 페이지·전역 스타일
components/          KioskApp(전체 배치), ChatView, MenuBoard, MenuCard, OptionSheet, CartPanel
lib/order/           주문 도메인 (React와 무관한 순수 함수)
  catalog.ts           CSV에서 만든 메뉴·옵션·페어링 조회
  validation.ts        옵션 검증: 적용 대상, 최대 횟수, 동시 선택 불가, 온도, 수량
  pricing.ts           금액 계산: 온도별 기본가 + 옵션 추가금 × 횟수, × 수량
  engine.ts            장바구니 액션 처리 + HTI 주문 진행 단계(1~9)와 안내 문구
  stageReply.ts        온도·확인 질문의 단답 처리, 복합 입력은 서버로 전달
  format.ts            금액·옵션 표시 문자열
lib/kiosk/store.ts   대화 기록 + 주문 상태 + 서버 응답 병합
lib/api.ts           /api/chat 요청·응답 타입과 호출
lib/generated/       scripts/build-catalog.mjs가 CSV로 만든 catalog.json
data/                메뉴 CSV (catalog.json 생성용)
tests/               HTI ID별 테스트 (vitest)
docs/                API 연결 규격, 검증·인계 기록, HTI 원본
api/                 /api/chat, 대화 요청 해석·액션 연결, 가짜/실제 RAG 어댑터
rag/                 검색·답변 코드와 저장된 임베딩·FAISS 인덱스
```

## 담당 기능

| 기능 | 구현 위치 |
| --- | --- |
| 요청 구분·복합 발화·후속 요청 연결 | `api/dialogue.py`, `api/index.py` |
| RAG 검색·답변 연결 | `api/live_rag.py`, `rag/service.py` |
| 채팅 화면 | `components/ChatView.tsx`, `lib/kiosk/store.ts` |
| 추천 메뉴 카드 (담기 버튼) | `components/MenuCard.tsx` — RAG 응답의 `related_menu_ids`, `choices` 사용 |
| 메뉴판·옵션 선택 창 | `components/MenuBoard.tsx`, `components/OptionSheet.tsx` |
| 주문 내역·총액 | `components/CartPanel.tsx`, `lib/order/pricing.ts` |
| 장바구니 추가·수정·삭제 | `lib/order/engine.ts` (`add_item`, `update_item`, `remove_item`, `replace_menu`, `clear_cart`) |
| 옵션 검증 | `lib/order/validation.ts` |
| 금액 계산 | `lib/order/pricing.ts` |

## 규칙과 근거

| 항목 | 규칙 | 근거 |
| --- | --- | --- |
| 옵션 적용 대상 | KioskProject `rag/build_index.py`의 `option_relation()`과 같은 계산 | options.csv, menu_variants.allergens·decaf_available |
| 옵션 최대 횟수·추가금 | `max_quantity`, `price_delta_krw` | options.csv |
| 샷 추가 + 연하게, 두유 + 저지방 | 동시 선택 불가 | 팀 규칙 |
| 온도 | 두 온도 메뉴는 필수 질문, ICE만 있는 메뉴는 자동, 디저트는 없음 | menu_catalog.available_temperatures |
| 같은 메뉴·온도·옵션 | 수량 합침, 옵션이 다르면 별도 항목 | HTI 6.1.2 |
| 수량 | 10개 이상 확인 질문, 항목당 최대 999 | HTI 6.1.3, 팀 규칙 |
| 디저트 제안 | 음료만 담겼을 때 주문당 한 번, pairings.csv | HTI 단계 5, 공통 규칙 4 |
| 결제 | 카드·간편결제만, 모의 주문번호 | FAQ-005 |

## 개발 환경 준비

Node.js·npm과 Python 3.10 이상이 필요합니다. 처음 실행할 때 프로젝트 루트에서 설치합니다.

```bash
npm install
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## 실행 방법

### 시험용 모드 — API 키 없이 실행

터미널 1에서 API 서버를 실행합니다.

```bash
source .venv/bin/activate
KIOSK_MODE=mock npm run api
```

터미널 2에서 화면을 실행합니다.

```bash
npm run dev
```

[http://localhost:3000](http://localhost:3000)에 접속합니다. 시작 전에 CSV에서 화면용 `catalog.json`을 생성합니다. 개발 모드의 `/api/*` 요청은 `127.0.0.1:8000`으로 전달됩니다.

시험용 모드(Mock)는 고정된 응답과 카탈로그 기반 요청 해석을 사용합니다. `FAKE-RAG-001` 같은 근거 ID는 시험용이며 실제 문서 검색 결과가 아닙니다. API 서버 없이도 메뉴판·장바구니는 사용할 수 있지만, 서버가 필요한 채팅에는 연결 실패 안내가 표시됩니다.

### 실제 RAG 모드 — API 키 필요

실제 RAG를 사용하려면 선택적 패키지를 설치하고, 저장소에 커밋되지 않는 `.env` 또는 환경변수로 `OPENAI_API_KEY`를 설정합니다. `KIOSK_MODE=live`는 요청 해석, 질문 임베딩, RAG 답변 생성에서 외부 API를 호출하므로 사용량에 따라 비용이 발생합니다. 저장된 인덱스를 읽으며 자동 재생성은 하지 않습니다.

```bash
python3 -m pip install -r requirements-rag.txt
KIOSK_MODE=live RAG_REBUILD_INDEX=0 npm run api
```

프로젝트 루트에서 가상 환경을 활성화한 상태로 실행합니다. 화면 서버는 위와 같이 별도 터미널에서 실행합니다. 모드를 바꿀 때는 기존 API 서버를 종료하고 다시 시작합니다. 실제 결제는 어느 모드에서도 수행하지 않습니다.

## 테스트와 빌드

```bash
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
KIOSK_MODE=mock python3 -m unittest discover -s api/tests
npm test
npm run typecheck
npm run build
```

RAG 단위 테스트는 `requirements-rag.txt` 설치 후 `python3 -m unittest discover -s rag/tests`로 실행합니다. 기본 `npm test`는 실제 API 흐름 시험 5건을 건너뜁니다. 실제 흐름 시험은 서버와 외부 API를 사용하는 별도 검증이므로 실행 범위와 비용을 확인해야 합니다.

`main` 병합 상태에서는 Mock API 48건, RAG 단위 3건, 주문·상태 59건과 타입 검사·빌드가 통과했습니다. 과거 실제 API 시험과 화면 미검증 항목은 [검증 기록](docs/FINAL_VERIFICATION.md)에서 구분합니다.

## 데이터와 알려진 한계

- CSV 7개는 `data/megacoffee_rag_enriched/`에 있습니다. 화면용 데이터는 `npm run catalog`로 생성합니다.
- CSV 변경 후에는 저장된 RAG 인덱스의 서명도 확인해야 합니다. 화면용 데이터 생성만으로 임베딩·검색 인덱스가 갱신되지는 않습니다.
- 카페인 답변의 근거 ID 누락과 브라우니 대안의 팀 CSV 규칙 사용 사례가 기록되어 있습니다.
- 실제 API를 연결한 브라우저에서 채팅 결제·추천 후속 선택·보류 초안 수정의 3개 흐름을 확인했습니다. 모든 HTI 항목이나 모든 표현을 검증한 것은 아닙니다. 입력 순서와 수정 사항은 [검증 기록](docs/FINAL_VERIFICATION.md)에 있습니다.

## 기능 간 연결 원칙

- 서버는 장바구니를 직접 바꾸지 않고 응답에 `cart_actions`를 담아 지시합니다. 형식은 `docs/API.md`에 있습니다.
- 주문 엔진이 옵션 적용 가능 여부·최대 횟수·추가금을 다시 검사합니다. 다만 요청 대상이나 의도 해석 오류까지 모두 차단한다는 뜻은 아니므로 흐름 시험도 필요합니다.
- `api/index.py`의 `route_message()`는 정보 질문을 RAG에 보내고 자연어 주문·수정·확인 요청을 기존 `cart_actions`로 변환합니다. 실제 모드의 명확한 주문 표현은 `api/dialogue.py`의 기존 규칙을 재사용하고, 나머지는 구조화 LLM 계획을 사용합니다. 추천 후속 선택은 RAG의 메뉴 ID 순서를 사용하며, 보류 중 정보 질문은 원래 질문으로 복귀합니다. 보류 중 독립된 새 주문은 먼저 처리하고 기존 온도를 다시 묻습니다. 프론트는 `history`, `cart`, 구조화된 `pending`, `pending_question`, `last_recommended_menu_ids`, `prior_conditions`를 매 턴 보냅니다. 실제 RAG의 근거·대안 품질과 브라우저 화면은 [검증 기록](docs/FINAL_VERIFICATION.md)의 남은 확인 사항을 참고하세요.
