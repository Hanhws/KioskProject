import { describe, expect, it } from "vitest";
import type { ChatResponse } from "../lib/api";
import { buildChatRequest, confirmSuggestedMenu, initialKioskState, kioskReducer, mapRagChoices } from "../lib/kiosk/store";
import { initialOrderState } from "../lib/order/engine";
import { priceCart } from "../lib/order/pricing";

function response(partial: Partial<ChatResponse>): ChatResponse {
  return {
    answer: "",
    status: "ok",
    choices: [],
    related_menu_ids: [],
    next_action: null,
    resume_prompt: null,
    error: null,
    ...partial,
  };
}

describe("서버 응답 처리 (store)", () => {
  it("추천 응답: 메뉴 카드와 선택 버튼, 직전 추천 목록 저장", () => {
    const state = kioskReducer(
      initialKioskState(),
      {
        type: "server",
        response: response({
          answer: "카페모카, 초코라떼를 추천해요.",
          next_action: "choose_menu",
          related_menu_ids: ["MENU-006", "MENU-008", "NOT-A-MENU"],
          choices: [
            { field: "menu_group_id", value: "MENU-006", label: "카페모카", source_id: "MENU-006" },
            { field: "free_text", value: "free_text", label: "직접 입력", source_id: null },
          ],
        }),
      },
    );
    const last = state.messages[state.messages.length - 1];
    expect(last.menuIds).toEqual(["MENU-006", "MENU-008"]);
    expect(last.choices[0].action).toEqual({ type: "add_item", menu_id: "MENU-006" });
    expect(last.choices[1].action).toEqual({ type: "focus_input" });
    expect(state.lastRecommended).toEqual(["MENU-006", "MENU-008"]);
  });

  it("cart_actions가 오면 엔진으로 담고 결과를 이어서 안내 (4.4)", () => {
    const state = kioskReducer(initialKioskState(), {
      type: "server",
      response: response({
        answer: "",
        cart_actions: [
          { type: "add_item", menu_id: "MENU-001", temperature: "ICE", quantity: 2 },
          { type: "add_item", menu_id: "MENU-028" },
        ],
      }),
    });
    expect(state.order.lines).toHaveLength(2);
    expect(state.messages[state.messages.length - 1].text).toContain("총 9,200원");
  });

  it("8.4.2 무관한 질문 뒤 멈췄던 온도 질문을 다시 한다", () => {
    let state = kioskReducer(initialKioskState(), { type: "engine", actions: [{ type: "add_item", menu_id: "MENU-002" }] });
    state = kioskReducer(state, {
      type: "server",
      response: response({ answer: "저는 주문을 돕는 키오스크예요.", status: "out_of_scope" }),
    });
    const last = state.messages[state.messages.length - 1];
    expect(last.text).toBe("아까 카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?");
    expect(last.choices).toHaveLength(2);
  });

  it("요청에 장바구니·멈춘 질문·직전 추천이 함께 간다", () => {
    let state = kioskReducer(initialKioskState(), { type: "engine", actions: [{ type: "add_item", menu_id: "MENU-002" }] });
    state = { ...state, lastRecommended: ["MENU-006"] };
    const request = buildChatRequest(state, "너 AI야?");
    expect(request.context.cart.pending_question).toBe("카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?");
    expect(request.context.last_recommended_menu_ids).toEqual(["MENU-006"]);
  });

  it("HTI 3.6 추천 순서는 다른 정보 질문 뒤에도 유지한다", () => {
    let state = kioskReducer(initialKioskState(), { type: "server", response: response({
      answer: "초코라떼, 카페모카를 추천해요.", next_action: "choose_menu",
      related_menu_ids: ["MENU-008", "MENU-006"],
    }) });
    state = kioskReducer(state, { type: "user_text", text: "아메리카노 가격도 알려 주세요" });
    state = kioskReducer(state, { type: "server", response: response({
      answer: "가격 안내", related_menu_ids: ["MENU-001"],
    }) });
    expect(buildChatRequest(state, "첫 번째 걸로 주세요").context.last_recommended_menu_ids).toEqual(["MENU-008", "MENU-006"]);
  });

  it("HTI 8.5 오타 후보를 확인하는 입력은 같은 메뉴의 담기 액션으로 이어진다", () => {
    let state = kioskReducer(initialKioskState(), { type: "server", response: response({
      answer: "바닐라라떼를 말씀하셨나요?", status: "needs_clarification", next_action: "confirm_menu",
      choices: [{ field: "menu_group_id", value: "MENU-004", label: "바닐라라떼", source_id: "MENU-004" }],
      related_menu_ids: ["MENU-004"],
    }) });
    const action = confirmSuggestedMenu(state, "네, 맞아요");
    expect(action).toEqual([{ type: "add_item", menu_id: "MENU-004" }]);
    state = kioskReducer(state, { type: "engine", actions: action!, echo: "네, 맞아요" });
    expect(state.order.pending?.kind).toBe("temperature");
    expect(state.pendingMenuConfirmation).toBeNull();
  });

  it("HTI 8.4.2 주문 중 정보 질문은 보류·대기 액션을 보존하고 질문을 재개한다", () => {
    let state = kioskReducer(initialKioskState(), { type: "engine", actions: [
      { type: "add_item", menu_id: "MENU-002" },
      { type: "add_item", menu_id: "MENU-028" },
    ] });
    expect(state.order.pending?.kind).toBe("temperature");
    expect(state.order.queued).toHaveLength(1);
    state = kioskReducer(state, { type: "user_text", text: "너 AI야?" });
    state = kioskReducer(state, { type: "server", response: response({
      answer: "저는 주문을 돕는 키오스크예요.", next_action: "resume_pending_question",
    }) });
    expect(state.order.pending?.kind).toBe("temperature");
    expect(state.order.queued).toHaveLength(1);
    expect(state.messages.at(-1)?.text).toContain("카페라떼는 따뜻한 것과 아이스 중");
    state = kioskReducer(state, { type: "engine", actions: [{ type: "add_item", menu_id: "MENU-002", temperature: "ICE" }] });
    expect(state.order.lines.map((line) => line.menuId)).toEqual(["MENU-002", "MENU-028"]);
  });

  it("보류 중 장바구니 확인은 합계를 보여 주고 원래 보류·대기 액션을 유지한다", () => {
    let state = kioskReducer(initialKioskState(), { type: "engine", actions: [
      { type: "add_item", menu_id: "MENU-001", temperature: "ICE" },
    ] });
    state = kioskReducer(state, { type: "engine", actions: [
      { type: "add_item", menu_id: "MENU-002" }, { type: "add_item", menu_id: "MENU-028" },
    ] });
    const before = structuredClone(state.order);
    state = kioskReducer(state, { type: "server", response: response({
      cart_actions: [{ type: "show_cart" }],
    }) });
    expect(state.order).toEqual(before);
    expect(state.messages.at(-2)?.text).toContain("총 2,500원");
    expect(state.messages.at(-1)?.text).toContain("카페라떼는 따뜻한 것과 아이스 중");
  });

  it("silent 조작은 질문이 없으면 대화창에 남기지 않는다", () => {
    let state = kioskReducer(initialKioskState(), {
      type: "engine",
      actions: [{ type: "add_item", menu_id: "MENU-028" }],
    });
    const count = state.messages.length;
    state = kioskReducer(state, {
      type: "engine",
      actions: [{ type: "update_item", target: { menu_id: "MENU-028" }, quantity: 2 }],
      echo: "수량 변경",
      silent: true,
    });
    expect(state.messages).toHaveLength(count);
    expect(state.order.lines[0].quantity).toBe(2);
  });

  it("온도 선택지는 보류 중인 메뉴에 적용된다", () => {
    const order = { ...initialOrderState(), pending: { kind: "temperature" as const, action: { type: "add_item" as const, menu_id: "MENU-003" }, question: "?" } };
    const [choice] = mapRagChoices([{ field: "temperature", value: "ICE", label: "아이스", source_id: null }], order);
    expect(choice.action).toEqual({ type: "add_item", menu_id: "MENU-003", temperature: "ICE" });
  });

  it("정보 질문 응답과 근거를 표시해도 기존 항목·옵션·수량·총액은 유지된다", () => {
    let state = kioskReducer(initialKioskState(), {
      type: "engine",
      actions: [{ type: "add_item", menu_id: "MENU-002", temperature: "ICE", options: { "OPT-001": 1 }, quantity: 2 }],
    });
    const before = structuredClone(state.order);
    const totalBefore = priceCart(before.lines).total;
    state = kioskReducer(state, { type: "user_text", text: "아메리카노 가격 알려 주세요" });
    state = kioskReducer(state, {
      type: "server",
      response: response({
        answer: "[개발·시험용 가짜 RAG 응답] 아메리카노 가격 질문을 받았습니다.",
        evidence_document_ids: ["FAKE-RAG-001"],
      }),
    });
    expect(state.order).toEqual(before);
    expect(priceCart(state.order.lines).total).toBe(totalBefore);
    expect(state.messages.at(-1)).toMatchObject({ evidence: ["FAKE-RAG-001"] });
  });

  it("정보 답변 뒤 같은 응답의 주문 액션을 기존 엔진으로 실행한다", () => {
    let state = kioskReducer(initialKioskState(), { type: "user_text", text: "라떼 얼마예요? 그거 아이스로 두 잔 주세요" });
    state = kioskReducer(state, {
      type: "server",
      response: response({
        answer: "[시험 RAG] 아이스 카페라떼는 3,400원이에요.",
        evidence_document_ids: ["TEST-EVIDENCE"],
        cart_actions: [{ type: "add_item", menu_id: "MENU-002", temperature: "ICE", quantity: 2 }],
      }),
    });
    expect(state.order.lines).toMatchObject([{ menuId: "MENU-002", temperature: "ICE", quantity: 2 }]);
    expect(priceCart(state.order.lines).total).toBe(6800);
    expect(state.messages.some((message) => message.evidence?.includes("TEST-EVIDENCE"))).toBe(true);
  });

  it("온도가 빠진 복수 주문은 기존 엔진이 질문하고 나머지 액션을 보관한다", () => {
    let state = kioskReducer(initialKioskState(), {
      type: "server",
      response: response({ cart_actions: [
        { type: "add_item", menu_id: "MENU-001" },
        { type: "add_item", menu_id: "MENU-028" },
      ] }),
    });
    expect(state.order.pending?.kind).toBe("temperature");
    expect(state.order.queued).toHaveLength(1);
    state = kioskReducer(state, { type: "engine", actions: [{ type: "add_item", menu_id: "MENU-001", temperature: "ICE" }] });
    expect(state.order.lines.map((line) => line.menuId)).toEqual(["MENU-001", "MENU-028"]);
  });

  it("HTI 4.4 복수 주문은 기존 가격 계산으로 합계 9,200원이 된다", () => {
    const state = kioskReducer(initialKioskState(), {
      type: "server",
      response: response({ cart_actions: [
        { type: "add_item", menu_id: "MENU-001", temperature: "ICE", quantity: 2 },
        { type: "add_item", menu_id: "MENU-028" },
      ] }),
    });
    expect(state.order.lines.map((line) => line.quantity)).toEqual([2, 1]);
    expect(priceCart(state.order.lines).total).toBe(9200);
  });
});
