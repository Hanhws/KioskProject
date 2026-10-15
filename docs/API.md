# 화면·서버 API 연결 규격

프론트엔드는 `POST /api/chat`으로 한 턴씩 보냅니다. 서버 입구는 `api/index.py`의 `route_message()`입니다.
기본 모드는 카탈로그 기반 시험용 요청 해석기와 `FakeRAG.ask()`를 사용합니다. 가짜 답변과 `FAKE-RAG-001`은 개발·시험용이며 실제 검색 근거가 아닙니다. `KIOSK_MODE=live`는 명확한 주문 표현에 기존 규칙을 재사용하고, 그 외 요청은 구조화 LLM 계획과 `CafeRAGService.ask()`를 연결합니다. 실제 API·주문 엔진 및 브라우저에서 확인한 범위는 [최종 흐름 검증 기록](FINAL_VERIFICATION.md)에 있습니다.

장바구니·옵션 검증·금액 계산·주문 진행 단계는 프론트엔드(`lib/order/`)가 맡습니다.
서버는 장바구니를 직접 바꾸지 않고 **`cart_actions`로 지시만** 보냅니다. 검증에 실패하면 프론트엔드가 이유와 대안을 안내합니다.

## 요청 형식

`history`는 이전 대화이며 역할은 `user`(사용자) 또는 `assistant`(키오스크)입니다. 현재 입력은 `message`로 전달합니다. 아래 예시는 아메리카노가 담겨 있고 카페라떼의 온도를 기다리는 상황입니다.

```jsonc
{
  "message": "아이스요, 치즈케이크도 주세요",
  "history": [{ "role": "assistant", "content": "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?" }], // 최근 10개
  "context": {
    "cart": {
      "stage": "more",                 // greeting | ordering | more | dine_option | final_confirm | payment | pay_confirm | complete
      "dine_option": null,             // dine_in | takeout | null
      "payment_method": null,
      "total_krw": 3000,
      "item_count": 1,
      "lines": [{
        "line_id": "L1", "menu_id": "MENU-001", "variant_id": "MEGA-002", "menu_name": "아메리카노",
        "temperature": "ICE", "options": { "OPT-001": 1 }, "option_labels": ["샷 추가"],
        "quantity": 1, "unit_price_krw": 3000, "line_total_krw": 3000
      }],
      "pending_question": "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"  // 멈춘 질문 (HTI 8.4.2)
    },
    "pending": { "kind": "temperature", "action": { "type": "add_item", "menu_id": "MENU-002" } },
    "last_recommended_menu_ids": ["MENU-006", "MENU-008"],   // "첫 번째 걸로" (3.6), "다른 거" (3.8)
    "prior_conditions": { "required_sweetness_level": "높음" } // 직전 응답의 prior_conditions
  }
}
```

어댑터는 `message`를 정보 질문·추천·주문 단계로 나누고, 정보 질문과 `prior_conditions`를 RAG에 전달합니다. 주문 단계는 카탈로그에 존재하는 메뉴·옵션 ID만 `cart_actions`로 보냅니다. 추천에서는 RAG의 `related_menu_ids` 순서를 기준으로 버튼·카드를 맞추고, 프론트는 `next_action=choose_menu`인 응답만 직전 추천으로 저장합니다. `다른 거` 요청은 직전 추천 ID를 제외 조건에 더합니다. 확정된 ID 순서가 없으면 `첫 번째`로 자동 선택하지 않습니다.

### 요청에 포함하는 상태

| 항목 | 의미 |
| --- | --- |
| `context.cart` | 현재 장바구니, 금액, 주문 단계, 안내 중인 질문 |
| `context.pending` | 아직 끝나지 않은 확인 요청. 없으면 `null` |
| `pending.kind` | 온도(`temperature`), 대량 수량 확인(`quantity_confirm`), 전체 취소 확인(`clear_confirm`), 수정 대상 확인(`target`) |
| `pending.action` | 확인 뒤 실행할 주문 초안 또는 액션 |
| `pending.line_ids` | 수정·삭제 대상을 고를 때의 장바구니 항목 ID 목록 |
| `context.last_recommended_menu_ids` | 직전 추천의 표시 순서. “첫 번째” 해석에 사용 |
| `context.prior_conditions` | 다음 턴에도 유지할 추천 조건 |

장바구니·보류 요청·대기 액션의 원본은 화면에 있습니다. 서버는 전달된 상태를 읽고 지시를 반환하며 별도 장바구니를 저장하지 않습니다.

## 응답 형식

응답은 RAG 결과에서 화면에 필요한 필드만 선택합니다. 주문 단계가 있으면 `cart_actions`를, RAG가 적용 조건을 반환하면 `prior_conditions`를 추가합니다. 실제 장바구니 변경 성공 여부와 총액은 프론트 주문 엔진에서 결정합니다.

| 필드 | 화면에서 쓰는 방식 |
| --- | --- |
| `answer` | 키오스크 말풍선 |
| `related_menu_ids` | 메뉴 카드(최대 4개, 담기 버튼 포함) |
| `choices` | 버튼. `menu_group_id` → 바로 담기(온도는 프론트가 물음), `free_text` → 입력창 포커스, 그 외 → 라벨을 메시지로 전송 |
| `evidence_document_ids` | 말풍선 아래 "근거" 표시 |
| `status`, `next_action`, `resume_prompt` | `out_of_scope`이거나 `resume_pending_question`이면 멈춘 질문을 다시 띄움 |
| `next_action=open_menu/open_chat` | 빠른 주문·대화 주문 탭 전환 (HTI 1.2) |
| `next_action=confirm_menu` | 미등록 오타의 메뉴 후보를 잠시 보관해 버튼이나 '네' 답으로 확인 (HTI 8.5) |
| `cart_actions` | **(통합)** 장바구니·주문 단계 지시. 순서대로 실행 |
| `prior_conditions` | **(통합)** 다음 턴에 그대로 돌려보낼 조건. `null`이면 비움, 생략하면 유지 |

## 주문 처리 지시 (`cart_actions`)

화면의 `lib/order/types.ts`에 정의된 `CartAction` 중 서버 응답 모델이 지원하는 지시를 사용합니다. 현재 서버 모델은 `api/index.py`에 정의되어 있습니다. 메뉴는 `menu_catalog.menu_group_id`, 옵션은 `options.option_id`를 씁니다. 아래 각 줄은 독립적인 지시 예시이며, 실제 응답에서는 배열로 반환합니다.

```jsonc
{ "type": "add_item", "menu_id": "MENU-002", "temperature": "ICE", "options": { "OPT-003": 1 }, "quantity": 2 }
{ "type": "add_item", "menu_id": "MENU-002", "temperature": "ICE", "pending_resolution": true } // 보류 중인 온도 질문에 대한 답
{ "type": "update_item", "target": { "menu_id": "MENU-002" }, "quantity": 3 }
{ "type": "update_item", "target": { "line_id": "L1" }, "set_options": { "OPT-001": 0 } }   // 0 = 옵션 제거 (6.3)
{ "type": "update_item", "target": { "line_id": "L1" }, "temperature": "ICE" }          // 이미 담긴 음료의 온도 변경
{ "type": "update_item", "target": { "pending": true }, "quantity": 3 }                  // 담기 전 보류 초안의 수량 변경
{ "type": "update_item", "target": { "pending": true }, "set_options": { "OPT-001": 0 } } // 보류 초안의 샷 제거
{ "type": "remove_item", "target": { "menu_ids": ["MENU-002", "MENU-004"] } }             // 여러 개면 프론트가 되물음 (6.5.1)
{ "type": "replace_menu", "target": { "menu_id": "MENU-001" }, "menu_id": "MENU-002" }    // 온도 유지 (6.5)
{ "type": "clear_cart" }                       // 확인 질문 후 비움 (6.7)
{ "type": "set_dine_option", "value": "takeout" }
{ "type": "finish_ordering" }                  // "그게 다예요" (6.8)
{ "type": "show_cart" }                        // "뭐 담겼어요?" (6.6)
{ "type": "confirm_order" }                    // "이대로 주문할게요" (7.1)
{ "type": "pay_now" }                          // 결제 확인 단계에서 모의 결제 완료
{ "type": "select_payment", "method": "card" } // card: 카드, easy_pay: 간편결제, cash: 현금(불가 안내)
{ "type": "new_order" }                        // 완료 후 새 주문. 진행 중인 주문을 자동 삭제하지 않음
```

결제 수단 선택(`select_payment`), 새 주문 시작(`new_order`), 모의 결제 확정(`pay_now`)은 서버에서도 반환할 수 있습니다. 실제 완료 여부는 화면의 주문 엔진이 현재 단계·결제 수단·주문 내역을 검사해 결정합니다. 카드·간편결제를 지원하며 현금은 불가 안내를 표시합니다.

- 온도·수량·옵션은 빠져도 됩니다. 온도가 필요하면 프론트가 온도만 묻고(6.1.1), 10잔 이상이면 확인합니다(6.1.3).
- 옵션 적용 가능 여부·최대 횟수·추가금은 주문 엔진이 다시 검사합니다. 요청 대상과 의도 해석의 정확성은 별도 흐름 시험으로 확인합니다.
- 질문이 생기면 남은 액션은 보관했다가 답을 받은 뒤 이어서 처리합니다. 예: `[라떼(온도 없음), 치즈케이크]` → 온도 질문 → 답 → 둘 다 담김.
- 보류 중 정보 질문이나 장바구니 확인은 장바구니·`pending`·`queued`를 유지하고 이전 질문을 다시 보여 줍니다. 온도 답 없는 독립 새 주문은 사용자 결정에 따라 먼저 처리한 뒤 기존 온도를 다시 묻습니다. 새 주문에도 확인이 필요하면 새 주문의 확인을 먼저 하고, 원래 보류 주문을 대기 액션으로 이어 갑니다. 다만 메뉴 교체 중 온도 확인은 원래 교체 대상을 보존해야 하므로 새 액션을 대기시킵니다.
- 주문 관련 요청이면 `answer`는 비워도 됩니다. 프론트가 담은 결과·금액·다음 질문을 HTI 문구로 안내합니다.
- 판매하지 않는 메뉴의 주문 요청은 RAG의 대안 결과를 조회합니다. RAG가 후보 ID를 반환하지 않으면 기존 팀 대안 CSV의 해당 규칙을 사용합니다. 카탈로그에 있는 메뉴 ID만 최대 3개까지 버튼·카드로 표시하며, 사용자가 선택하기 전에는 `cart_actions`를 만들지 않습니다. 보류 중이었다면 기존 질문을 다시 보여 줍니다. CSV로 대체한 응답에는 RAG 검색 근거를 임의로 붙이지 않습니다. Mock의 대안 문구는 개발·시험용입니다.

## 후속 요청 처리 원칙

- 온도 질문의 답은 `pending_resolution: true`로 기존 초안에 연결합니다. 새 메뉴 추가와 구분해 중복 담기를 방지합니다.
- 온도 미정 초안의 샷·수량 변경은 `target.pending: true`로 전달합니다. 이미 담긴 같은 메뉴와 혼동될 수 있으면 대상을 확인합니다.
- 담긴 음료에 대한 짧은 온도 변경은 음료 항목만 대상으로 합니다. 대상이 여러 개면 확인하고, 디저트는 변경하지 않습니다. 동일 온도 요청은 수량 추가로 처리하지 않습니다.
- ‘케이꾸’처럼 케이크 종류를 가리키는 표현은 실제 케이크 후보로 연결하며, 종류가 정해지기 전에는 임의로 담지 않습니다.
- 정보 답변과 원래 질문으로의 복귀 안내는 한 말풍선으로 합칩니다. 실제 주문 변경 결과와 금액은 엔진 실행 후 표시합니다.
- 정보 질문에 모델이 온도 값 없는 `answer_pending` 단계를 함께 반환하면 그 빈 단계는 실행하지 않습니다. 주문 변경이 없으면 원래 보류 질문과 선택 버튼으로 복귀합니다.

## 화면에서 서버 없이 처리하는 입력

키오스크가 온도나 확인 질문을 하고 있을 때, 확실한 단답만 서버 없이 처리합니다 (`lib/order/stageReply.ts`). “아이스요, 케이크도 주세요”, “네, 그런데 쿠키도 추가요”처럼 다른 요청이 섞이면 서버로 전달합니다.

현재 주문 단계에 표시된 버튼 문구를 그대로 입력하면 버튼과 같은 주문 지시를 실행합니다. “네, 주문할게요”, “네, 맞아요”, “네, 비워 주세요”, “결제 수단 바꾸기”, “새 주문 시작” 등이 해당합니다. 메뉴 탐색·화면 포커스 등 주문 지시가 아닌 버튼은 이 규칙에서 제외합니다. 문장 일부만 일치하는 복합 요청은 가로채지 않습니다.

“장바구니 보기”는 보류 중에도 대기 주문을 유지합니다. “카드로 결제할게요”와 “간편결제로 할게요”는 결제 수단 선택에 연결됩니다. 완료 후 “새 주문 시작”은 장바구니뿐 아니라 이전 대화·추천·조건도 초기화하며, 진행 중인 주문이 있으면 서버는 전체 취소 확인으로 안내합니다.

| 상황 | 예 |
| --- | --- |
| 온도 질문 | 따뜻하게 / 아이스요 (둘 다 있으면 서버로, HTI 8.2) |
| 10잔 확인·전체 취소 확인 | 네 / 아니요 |
| 매장/포장 질문 | 먹고 갈게요 / 포장이요 |
| 최종 확인 | 네 |
| 결제 수단 | 카드 / 간편결제 / 현금(불가 안내) |
| 추가 주문 확인 | 그게 다예요 / 이제 됐어요 |

### 채팅의 결제 요청

“결제하기”, “결제해 주세요”, “결제할게요”, “계산하기” 같은 명확한 단독 요청은 현재 단계에 따라 처리합니다. 모델 호출 없이 처리하며, 복합 문장이나 가능 여부 질문은 이 단답 규칙에 포함하지 않습니다.

| 현재 상태 | 처리 |
| --- | --- |
| 주문 중 | 주문 마무리로 이동. 매장/포장과 최종 확인은 생략하지 않음 |
| 보류 질문 있음 | 미완료 주문의 질문을 먼저 안내 |
| 매장/포장 미선택 | 매장/포장을 질문 |
| 주문 최종 확인 | 확인 후 결제 수단 선택으로 이동 |
| 결제 수단 미선택 | 카드·간편결제 선택 안내. 주문 확인 단계로 되돌리지 않음 |
| 결제 확인 완료 대기 | 결제 버튼과 같은 `pay_now` 실행, 모의 주문번호 발급 |

빈 장바구니로 결제하거나 완료된 주문을 중복 결제하는 요청은 기존 주문 엔진이 처리합니다. 실제 결제 연동은 없습니다.
