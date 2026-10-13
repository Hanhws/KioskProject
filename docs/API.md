# 프론트엔드 ↔ 서버 연결 규격

프론트엔드는 `POST /api/chat`으로 한 턴씩 보냅니다. 서버 입구는 `api/index.py`의 `route_message()`입니다.
지금은 RAG `CafeRAGService.ask()`만 호출합니다. 통합 담당은 이 함수 안에 요청 분류·복합 요청 분해·결과 병합을 넣으면 됩니다.

장바구니·옵션 검증·금액 계산·주문 진행 단계는 프론트엔드(`lib/order/`)가 맡습니다.
서버는 장바구니를 직접 바꾸지 않고 **`cart_actions`로 지시만** 보냅니다. 검증에 실패하면 프론트엔드가 이유와 대안을 안내합니다.

## 요청

```jsonc
{
  "message": "아아 두 잔이랑 치즈케이크 하나요",
  "history": [{ "role": "user" | "assistant", "content": "..." }],   // 최근 10개
  "context": {
    "cart": {
      "stage": "more",                 // greeting | more | dine_option | final_confirm | payment | pay_confirm | complete
      "dine_option": null,             // dine_in | takeout | null
      "payment_method": null,
      "total_krw": 2500,
      "item_count": 1,
      "lines": [{
        "line_id": "L1", "menu_id": "MENU-001", "variant_id": "MEGA-002", "menu_name": "아메리카노",
        "temperature": "ICE", "options": { "OPT-001": 1 }, "option_labels": ["샷 추가"],
        "quantity": 1, "unit_price_krw": 3000, "line_total_krw": 3000
      }],
      "pending_question": "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"  // 멈춘 질문 (HTI 8.4.2)
    },
    "last_recommended_menu_ids": ["MENU-006", "MENU-008"],   // "첫 번째 걸로" (3.6), "다른 거" (3.8)
    "prior_conditions": { "required_sweetness_level": "높음" } // 직전 응답의 prior_conditions
  }
}
```

현재 어댑터는 `pending_question`과 `last_recommended_menu_ids`를 `prior_conditions`에 넣어 `ask()`에 넘깁니다.

## 응답

`ask()` 반환값 그대로 + 아래 두 필드. `retrieved_documents`, `exact_facts`는 응답 크기 때문에 뺍니다.

| 필드 | 화면에서 쓰는 방식 |
| --- | --- |
| `answer` | 키오스크 말풍선 |
| `related_menu_ids` | 메뉴 카드(최대 4개, 담기 버튼 포함) |
| `choices` | 버튼. `menu_group_id` → 바로 담기(온도는 프론트가 물음), `free_text` → 입력창 포커스, 그 외 → 라벨을 메시지로 전송 |
| `evidence_document_ids` | 말풍선 아래 "근거" 표시 |
| `status`, `next_action`, `resume_prompt` | `out_of_scope`이거나 `resume_pending_question`이면 멈춘 질문을 다시 띄움 |
| `cart_actions` | **(통합)** 장바구니·주문 단계 지시. 순서대로 실행 |
| `prior_conditions` | **(통합)** 다음 턴에 그대로 돌려보낼 조건. `null`이면 비움, 생략하면 유지 |

## cart_actions

`lib/order/types.ts`의 `CartAction`과 같습니다. 메뉴는 `menu_catalog.menu_group_id`, 옵션은 `options.option_id`를 씁니다.

```jsonc
{ "type": "add_item", "menu_id": "MENU-002", "temperature": "ICE", "options": { "OPT-003": 1 }, "quantity": 2 }
{ "type": "update_item", "target": { "menu_id": "MENU-002" }, "quantity": 3 }
{ "type": "update_item", "target": { "line_id": "L1" }, "set_options": { "OPT-001": 0 } }   // 0 = 옵션 제거 (6.3)
{ "type": "remove_item", "target": { "menu_ids": ["MENU-002", "MENU-004"] } }             // 여러 개면 프론트가 되물음 (6.5.1)
{ "type": "replace_menu", "target": { "menu_id": "MENU-001" }, "menu_id": "MENU-002" }    // 온도 유지 (6.5)
{ "type": "clear_cart" }                       // 확인 질문 후 비움 (6.7)
{ "type": "set_dine_option", "value": "takeout" }
{ "type": "finish_ordering" }                  // "그게 다예요" (6.8)
{ "type": "show_cart" }                        // "뭐 담겼어요?" (6.6)
{ "type": "confirm_order" }                    // "이대로 주문할게요" (7.1)
{ "type": "select_payment", "method": "card" | "easy_pay" | "cash" }
```

- 온도·수량·옵션은 빠져도 됩니다. 온도가 필요하면 프론트가 온도만 묻고(6.1.1), 10잔 이상이면 확인합니다(6.1.3).
- 옵션 적용 가능 여부·최대 횟수·추가금은 프론트가 다시 검사합니다. 서버가 틀린 지시를 보내도 장바구니가 잘못되지 않습니다.
- 질문이 생기면 남은 액션은 보관했다가 답을 받은 뒤 이어서 처리합니다. 예: `[라떼(온도 없음), 치즈케이크]` → 온도 질문 → 답 → 둘 다 담김.
- 주문 관련 요청이면 `answer`는 비워도 됩니다. 프론트가 담은 결과·금액·다음 질문을 HTI 문구로 안내합니다.

## 프론트가 서버 없이 처리하는 입력

키오스크가 닫힌 질문을 하고 있을 때의 짧은 답은 서버로 보내지 않습니다 (`lib/order/stageReply.ts`).

| 상황 | 예 |
| --- | --- |
| 온도 질문 | 따뜻하게 / 아이스요 (둘 다 있으면 서버로, HTI 8.2) |
| 10잔 확인·전체 취소 확인 | 네 / 아니요 |
| 매장/포장 질문 | 먹고 갈게요 / 포장이요 |
| 최종 확인 | 네 |
| 결제 수단 | 카드 / 간편결제 / 현금(불가 안내) |
| 추가 주문 확인 | 그게 다예요 / 이제 됐어요 |
