# KioskPJ — 프론트엔드·주문 기능

카페 자연어 키오스크의 **주문 기능·프론트엔드·UI** 담당 부분입니다.
채팅 화면, 추천 메뉴 카드, 메뉴판(빠른 주문), 주문 내역·총액, 장바구니 추가·수정·삭제, 옵션 검증, 금액 계산, 매장/포장 → 최종 확인 → 결제 → 주문번호까지의 주문 흐름을 구현했습니다. Next.js(App Router) + TypeScript입니다.

RAG 답변(문서 검색, 메뉴 설명·비교·추천)과 API 서버(`api/index.py`, `rag/`)는 KioskProject에 있습니다. 이 폴더는 `POST /api/chat`으로 그 서버를 호출합니다.

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
npm test           # 주문 로직 테스트 (37개)
npm run typecheck
```

대화 답변을 받으려면 KioskProject에서 API 서버를 함께 띄웁니다 (`npm run api`, localhost:8000). 개발 모드에서는 `/api/*` 요청이 8000으로 프록시됩니다.
API 서버가 없어도 메뉴판·장바구니·주문 흐름은 동작하고, 대화창에는 연결 실패 안내가 나옵니다.

CSV가 바뀌면 `data/megacoffee_rag_enriched/`를 갱신한 뒤 `npm run catalog`를 실행합니다.

## 통합 담당에게

- 서버는 장바구니를 직접 바꾸지 않고 응답에 `cart_actions`를 담아 지시합니다. 형식은 `docs/API.md`에 있습니다.
- 프론트가 옵션 적용 가능 여부·최대 횟수·추가금을 다시 검사하므로, 서버가 틀린 지시를 보내도 장바구니가 잘못되지 않습니다.
- 요청 종류 구분, 복합 요청 분해, 이전 대화 연결은 KioskProject `api/index.py`의 `route_message()`에 넣으면 됩니다. 프론트는 `history`, `cart`, `pending_question`, `last_recommended_menu_ids`, `prior_conditions`를 매 턴 보냅니다.
