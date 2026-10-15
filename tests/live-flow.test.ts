/** Opt-in integration checks: real dialogue/RAG API plus the screen's state reducer. */
import { describe, expect, it } from "vitest";
import { postChat, type ChatResponse } from "../lib/api";
import { buildChatRequest, confirmSuggestedMenu, initialKioskState, kioskReducer, type KioskState } from "../lib/kiosk/store";
import { getMenu } from "../lib/order/catalog";
import { priceCart } from "../lib/order/pricing";
import { interpretStageReply } from "../lib/order/stageReply";

async function send(state: KioskState, text: string): Promise<{ state: KioskState; response?: ChatResponse }> {
  const confirmed = confirmSuggestedMenu(state, text);
  const local = confirmed ?? interpretStageReply(state.order, text);
  if (local) return { state: kioskReducer(state, { type: "engine", actions: local, echo: text }) };

  const request = buildChatRequest(state, text);
  const withUser = kioskReducer(state, { type: "user_text", text });
  const response = await postChat(request);
  return { state: kioskReducer(withUser, { type: "server", response }), response };
}

function count(state: KioskState, menuId: string): number {
  return state.order.lines.filter((line) => line.menuId === menuId).reduce((sum, line) => sum + line.quantity, 0);
}

const live = process.env.KIOSK_LIVE_TEST === "1";

describe.skipIf(!live)("실제 RAG·대화 API와 주문 상태 통합", () => {
  it("온도 답과 추가 주문을 함께 처리하고 라떼를 한 번만 담는다", async () => {
    let state = (await send(initialKioskState(), "라떼 하나요")).state;
    expect(state.order.pending?.kind).toBe("temperature");
    state = (await send(state, "아이스요, 치즈케이크도 주세요")).state;
    expect(state.order.pending).toBeNull();
    expect(count(state, "MENU-002")).toBe(1);
    expect(count(state, "MENU-028")).toBe(1);
    expect(state.order.lines.find((line) => line.menuId === "MENU-002")?.temperature).toBe("ICE");
    expect(priceCart(state.order.lines).total).toBe(7600);
  }, 180_000);

  it("보류 중 정보 질문에 답한 뒤 온도와 대기 주문을 이어 간다", async () => {
    let state = (await send(initialKioskState(), "라떼 하나랑 치즈케이크 하나요")).state;
    expect(state.order.pending?.kind).toBe("temperature");
    expect(state.order.queued.some((action) => action.type === "add_item" && action.menu_id === "MENU-028")).toBe(true);
    const pending = state.order.pending;
    const queued = state.order.queued;
    const info = await send(state, "카페인은요?");
    expect(info.response?.cart_actions).toBeUndefined();
    expect(info.response?.status).toBe("ok");
    expect(info.response?.next_action).toBe("resume_pending_question");
    expect(info.response?.answer).toBeTruthy();
    state = info.state;
    expect(state.order.pending).toEqual(pending);
    expect(state.order.queued).toEqual(queued);
    state = (await send(state, "아이스요")).state;
    expect(count(state, "MENU-002")).toBe(1);
    expect(count(state, "MENU-028")).toBe(1);
    expect(priceCart(state.order.lines).total).toBe(7600);
  }, 180_000);

  it("없는 메뉴의 실제 판매 대안은 선택 전까지 담지 않는다", async () => {
    let state = initialKioskState();
    const result = await send(state, "브라우니 주세요");
    state = result.state;
    expect(result.response?.cart_actions).toBeUndefined();
    expect(state.order.lines).toHaveLength(0);
    const reply = state.messages.at(-1)!;
    expect(reply.menuIds.length).toBeGreaterThan(0);
    expect(reply.menuIds.length).toBeLessThanOrEqual(3);
    expect(reply.menuIds.every((id) => !!getMenu(id))).toBe(true);
    const choice = reply.choices.find((item) => item.action.type === "add_item");
    expect(choice).toBeDefined();
    if (!choice || choice.action.type !== "add_item") return;
    state = kioskReducer(state, { type: "engine", actions: [choice.action], echo: choice.label });
    expect(count(state, choice.action.menu_id)).toBe(1);
    expect(choice.action.menu_id).toBe("MENU-030");
    expect(priceCart(state.order.lines).total).toBe(4200);
  }, 180_000);

  it("복수 주문의 옵션·수량 변경과 옵션 제거 뒤 금액을 다시 계산한다", async () => {
    let state = (await send(initialKioskState(), "아이스 아메리카노 두 잔이랑 치즈케이크 하나 주세요")).state;
    expect(count(state, "MENU-001")).toBe(2);
    expect(count(state, "MENU-028")).toBe(1);
    expect(priceCart(state.order.lines).total).toBe(9200);
    state = (await send(state, "아메리카노에 샷 하나 추가해 주세요")).state;
    expect(state.order.lines.find((line) => line.menuId === "MENU-001")?.options["OPT-001"]).toBe(1);
    state = (await send(state, "아메리카노 세 잔으로 바꿔 주세요")).state;
    expect(count(state, "MENU-001")).toBe(3);
    state = (await send(state, "아메리카노 샷 추가는 빼 주세요")).state;
    expect(state.order.lines.find((line) => line.menuId === "MENU-001")?.options["OPT-001"] ?? 0).toBe(0);
    expect(priceCart(state.order.lines).total).toBe(11700);
  }, 180_000);

  it("최종 확인 중 추가한 주문을 재확인하고 모의 주문번호를 발급한다", async () => {
    let state = (await send(initialKioskState(), "아이스 아메리카노 하나 주세요")).state;
    state = (await send(state, "그게 다예요")).state;
    expect(state.order.stage).toBe("dine_option");
    state = (await send(state, "포장이요")).state;
    expect(state.order.stage).toBe("final_confirm");
    state = (await send(state, "네, 그런데 치즈케이크도 추가요")).state;
    expect(state.order.stage).toBe("final_confirm");
    expect(count(state, "MENU-001")).toBe(1);
    expect(count(state, "MENU-028")).toBe(1);
    expect(priceCart(state.order.lines).total).toBe(6700);
    state = (await send(state, "네")).state;
    expect(state.order.stage).toBe("payment");
    state = (await send(state, "카드로 할게요")).state;
    expect(state.order.stage).toBe("pay_confirm");
    state = kioskReducer(state, { type: "engine", actions: [{ type: "pay_now" }] });
    expect(state.order.stage).toBe("complete");
    expect(state.order.orderNumber).toMatch(/^\d+$/);
  }, 180_000);
});
