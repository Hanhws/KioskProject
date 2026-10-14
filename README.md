# KioskPJ — 프론트엔드·주문 기능

카페 자연어 키오스크의 **주문 기능·프론트엔드·UI** 담당 부분입니다.
채팅 화면, 추천 메뉴 카드, 메뉴판(빠른 주문), 주문 내역·총액, 장바구니 추가·수정·삭제, 옵션 검증, 금액 계산, 매장/포장 → 최종 확인 → 결제 → 주문번호까지의 주문 흐름을 구현했습니다. Next.js(App Router) + TypeScript입니다.

이 브랜치의 `api/index.py`는 `POST /api/chat`을 제공합니다. 기본 모드는 `api/fake_rag.py`와 카탈로그 기반 시험용 요청 해석기로 동작하며 실제 검색·임베딩·모델 호출을 하지 않습니다. `KIOSK_MODE=live`에서는 구조화 LLM 요청 해석기와 `rag` 브랜치에서 가져온 `CafeRAGService.ask()`를 사용합니다. 실제 모드는 API 키가 필요하며 아직 외부 API 호출로 검증하지 않았습니다.

## 구조

```text
app/                 Next.js 페이지·전역 스타일
components/          KioskApp(전체 배치), ChatView, MenuBoard, MenuCard, OptionSheet, CartPanel
lib/order/           주문 도메인 (React와 무관한 순수 함수)
  catalog.ts           CSV에서 만든 메뉴·옵션·페어링 조회
  validation.ts        옵션 검증: 적용 대상, 최대 횟수, 동시 선택 불가, 온도, 수량
  pricing.ts           금액 계산: 온도별 기본가 + 옵션 추가금 × 횟수, × 수량
  engine.ts            장바구니 액션 처리 + HTI 주문 진행 단계(1~9)와 안내 문구
  stageReply.ts        닫힌 질문에 대한 짧은 답을 서버 없이 처리
  format.ts            금액·옵션 표시 문자열
lib/kiosk/store.ts   대화 기록 + 주문 상태 + 서버 응답 병합
lib/api.ts           /api/chat 요청·응답 타입과 호출
lib/generated/       scripts/build-catalog.mjs가 CSV로 만든 catalog.json
data/                메뉴 CSV (catalog.json 생성용)
tests/               HTI ID별 테스트 (vitest)
docs/API.md          프론트 ↔ 서버 연결 규격, cart_actions 형식
api/                 /api/chat, 대화 요청 해석·액션 연결, 가짜/실제 RAG 어댑터
rag/                 기존 rag 브랜치의 검색·답변 코드와 저장된 인덱스
```

## 담당 기능

| 기능 | 구현 위치 |
| --- | --- |
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

## 실행

```bash
npm install
npm run dev        # localhost:3000, 시작 전에 CSV로 catalog.json을 다시 만듦
npm test           # 주문·상태 테스트
npm run typecheck
```

API 서버를 실행할 Python 환경을 한 번 준비합니다 (Python 3.10 이상).

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

서로 다른 두 터미널에서 같은 가상 환경을 활성화한 다음 실행합니다.

```bash
npm run api        # 127.0.0.1:8000, API 키 불필요
npm run dev        # localhost:3000
```

개발 모드에서는 `/api/*` 요청이 8000으로 프록시됩니다. 채팅창에 `아메리카노 가격 알려 주세요`를 입력하면 가짜 답변과 `FAKE-RAG-001` 근거 표시가 나옵니다. 이 ID와 답변은 실제 메뉴 자료의 검색 결과가 아닙니다. Python API 테스트까지 실행하려면 `python3 -m pip install -r requirements-dev.txt` 후 `python3 -m unittest discover -s api/tests`를 사용합니다.
API 서버가 없어도 메뉴판·장바구니·주문 흐름은 동작하고, 대화창에는 연결 실패 안내가 나옵니다.

실제 RAG를 사용하려면 선택적 패키지를 설치하고, 저장소에 커밋되지 않는 `.env` 또는 환경변수로 `OPENAI_API_KEY`를 설정합니다. `KIOSK_MODE=live`는 요청 해석, 질문 임베딩, RAG 답변 생성에서 외부 API를 호출하므로 사용량에 따라 비용이 발생합니다. 저장된 인덱스를 읽으며 자동 재생성은 하지 않습니다.

```bash
python3 -m pip install -r requirements-rag.txt
KIOSK_MODE=live npm run api
```

CSV가 바뀌면 `data/megacoffee_rag_enriched/`를 갱신한 뒤 `npm run catalog`를 실행합니다.

## 통합 담당에게

- 서버는 장바구니를 직접 바꾸지 않고 응답에 `cart_actions`를 담아 지시합니다. 형식은 `docs/API.md`에 있습니다.
- 프론트가 옵션 적용 가능 여부·최대 횟수·추가금을 다시 검사하므로, 서버가 틀린 지시를 보내도 장바구니가 잘못되지 않습니다.
- `api/index.py`의 `route_message()`는 정보 질문을 RAG에 보내고 자연어 주문·수정·확인 요청을 기존 `cart_actions`로 변환합니다. 실제 모드에서는 `api/dialogue.py`의 구조화 LLM 계획을 사용합니다. 추천 순서는 RAG 선택지 또는 답변에서 확인 가능한 경우에만 후속 선택에 쓰며, 보류 중 정보 질문은 원래 질문으로 복귀합니다. 보류 중 독립된 새 주문은 먼저 처리하고 기존 온도를 다시 묻습니다. 실제 LLM·RAG의 후속 대화 품질은 별도 검증이 필요합니다. 프론트는 `history`, `cart`, 구조화된 `pending`, `pending_question`, `last_recommended_menu_ids`, `prior_conditions`를 매 턴 보냅니다.
