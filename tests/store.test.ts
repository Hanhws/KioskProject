import { describe, expect, it } from "vitest";
import type { ChatResponse } from "../lib/api";
import { buildChatRequest, initialKioskState, kioskReducer, mapRagChoices } from "../lib/kiosk/store";
import { initialOrderState } from "../lib/order/engine";

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
});
