import { describe, expect, it } from "vitest";
import { initialOrderState, runActions, stagePrompt } from "../lib/order/engine";
import { priceCart } from "../lib/order/pricing";
import { interpretStageReply } from "../lib/order/stageReply";
import { validateItem } from "../lib/order/validation";
import type { CartAction, EngineAction, OrderState } from "../lib/order/types";

const AMERICANO = "MENU-001";
const LATTE = "MENU-002";
const CAPPUCCINO = "MENU-003";
const VANILLA_LATTE = "MENU-004";
const MOCHA = "MENU-006";
const COLD_BREW = "MENU-007";
const CHOCO = "MENU-008";
const STRAWBERRY_LATTE = "MENU-011";
const LEMONADE = "MENU-019";
const CHEESECAKE = "MENU-028";
const COOKIE = "MENU-032";

function run(state: OrderState, ...actions: EngineAction[]) {
  return runActions(state, actions, { makeOrderNumber: () => "123" });
}

function add(menu_id: string, rest: Partial<Extract<CartAction, { type: "add_item" }>> = {}): CartAction {
  return { type: "add_item", menu_id, ...rest };
}

describe("옵션 검증 (validation)", () => {
  it("5.1 라떼 바닐라 시럽 가능, 최대 2회", () => {
    expect(validateItem({ menuId: LATTE, temperature: "ICE", options: { "OPT-003": 2 } }).ok).toBe(true);
    const over = validateItem({ menuId: LATTE, temperature: "ICE", options: { "OPT-003": 3 } });
    expect(over.ok).toBe(false);
    if (!over.ok) expect(over.issues[0].code).toBe("option_quantity_exceeded");
  });

  it("5.2 콜드브루오리지널은 '연하게' 불가, 샷 추가는 가능", () => {
    expect(validateItem({ menuId: COLD_BREW, temperature: "ICE", options: { "OPT-001": 1 } }).ok).toBe(true);
    const weak = validateItem({ menuId: COLD_BREW, temperature: "ICE", options: { "OPT-002": 1 } });
    expect(weak.ok).toBe(false);
  });

  it("5.3.1 아메리카노 두유 변경 불가 + 대안 메뉴 제시", () => {
    const result = validateItem({ menuId: AMERICANO, temperature: "HOT", options: { "OPT-005": 1 } });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.issues[0].code).toBe("option_not_applicable");
      expect(result.issues[0].alternativeMenuIds).toContain(LATTE);
    }
  });

  it("5.4.1 콜드브루 디카페인 불가, 아메리카노 대안", () => {
    const result = validateItem({ menuId: COLD_BREW, temperature: "ICE", options: { "OPT-009": 1 } });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.issues[0].alternativeMenuIds).toContain(AMERICANO);
  });

  it("5.5 휘핑: 카페모카 제외 가능, 초코라떼 추가 가능, 라떼 불가", () => {
    expect(validateItem({ menuId: MOCHA, temperature: "HOT", options: { "OPT-008": 1 } }).ok).toBe(true);
    expect(validateItem({ menuId: CHOCO, temperature: "HOT", options: { "OPT-007": 1 } }).ok).toBe(true);
    expect(validateItem({ menuId: LATTE, temperature: "HOT", options: { "OPT-007": 1 } }).ok).toBe(false);
  });

  it("팀 규칙: 샷 추가와 연하게, 두유와 저지방은 함께 고를 수 없음", () => {
    const shot = validateItem({ menuId: LATTE, temperature: "HOT", options: { "OPT-001": 1, "OPT-002": 1 } });
    expect(shot.ok).toBe(false);
    const milk = validateItem({ menuId: LATTE, temperature: "HOT", options: { "OPT-005": 1, "OPT-006": 1 } });
    expect(milk.ok).toBe(false);
  });

  it("2.3 딸기라떼는 ICE만 가능", () => {
    const hot = validateItem({ menuId: STRAWBERRY_LATTE, temperature: "HOT" });
    expect(hot.ok).toBe(false);
    if (!hot.ok) expect(hot.issues[0].code).toBe("temperature_unavailable");
    const auto = validateItem({ menuId: STRAWBERRY_LATTE });
    expect(auto.ok && auto.item.temperature).toBe("ICE");
  });

  it("디저트는 온도를 묻지 않음", () => {
    const result = validateItem({ menuId: CHEESECAKE });
    expect(result.ok && result.item.temperature).toBeNull();
  });
});

describe("금액 계산 (pricing)", () => {
  it("5.7 아이스 라떼 + 샷 + 바닐라 = 4,400원", () => {
    const { state } = run(initialOrderState(), add(LATTE, { temperature: "ICE", options: { "OPT-001": 1, "OPT-003": 1 } }));
    expect(priceCart(state.lines).total).toBe(4400);
  });

  it("4.4 아아 두 잔 + 치즈케이크 = 9,200원", () => {
    const { state, reply } = run(
      initialOrderState(),
      add(AMERICANO, { temperature: "ICE", quantity: 2 }),
      add(CHEESECAKE),
    );
    expect(priceCart(state.lines).total).toBe(9200);
    expect(reply.text).toContain("더 필요하신 건 없으세요?");
  });

  it("4.3 ICE 카페라떼 2잔 = 6,800원", () => {
    const { state } = run(initialOrderState(), add(LATTE, { temperature: "ICE", quantity: 2 }));
    expect(priceCart(state.lines).total).toBe(6800);
  });
});

describe("장바구니 (cart)", () => {
  it("6.1 담으면 금액 안내 + 커피 첫 담기 힌트 + 디저트 제안은 한 번만", () => {
    const first = run(initialOrderState(), add(AMERICANO, { temperature: "ICE" }));
    expect(first.reply.text).toContain("ICE 아메리카노 1잔(2,500원)을 담았어요.");
    expect(first.reply.text).toContain("샷·시럽 추가도 가능해요.");
    expect(first.reply.menuIds).toEqual(expect.arrayContaining([CHEESECAKE]));
    expect(first.state.stage).toBe("more");

    const second = run(first.state, add(LATTE, { temperature: "HOT" }));
    expect(second.reply.text).not.toContain("샷·시럽 추가도 가능해요.");
    expect(second.reply.menuIds).toHaveLength(0);
  });

  it("6.1.1 온도가 빠지면 온도만 묻고, 답하면 담는다", () => {
    const asked = run(initialOrderState(), add(LATTE));
    expect(asked.state.pending?.kind).toBe("temperature");
    expect(asked.state.lines).toHaveLength(0);
    expect(asked.reply.choices.map((c) => c.label)).toEqual(["따뜻하게 3,400원", "아이스 3,400원"]);

    const typed = interpretStageReply(asked.state, "아이스요");
    expect(typed).not.toBeNull();
    expect(interpretStageReply(asked.state, "ice 요~")).toMatchObject([{ pending_resolution: true, temperature: "ICE" }]);
    const done = run(asked.state, ...typed!);
    expect(done.state.lines[0].temperature).toBe("ICE");
    expect(done.state.pending).toBeNull();
  });

  it("온도 질문 때문에 멈춘 나머지 액션은 답한 뒤 이어서 처리", () => {
    const asked = run(initialOrderState(), add(LATTE), add(CHEESECAKE));
    expect(asked.state.lines).toHaveLength(0);
    expect(asked.state.queued).toHaveLength(1);
    const done = run(asked.state, add(LATTE, { temperature: "HOT", pending_resolution: true }));
    expect(done.state.lines.map((l) => l.menuId)).toEqual([LATTE, CHEESECAKE]);
  });

  it("아이스요와 케이크 추가를 함께 받아 라떼를 한 번만 담는다", () => {
    const asked = run(initialOrderState(), add(LATTE));
    expect(interpretStageReply(asked.state, "아이스요, 치즈케이크도 주세요")).toBeNull();
    const done = run(asked.state,
      add(LATTE, { temperature: "ICE", pending_resolution: true }), add(CHEESECAKE));
    expect(done.state.lines.map((line) => [line.menuId, line.quantity])).toEqual([[LATTE, 1], [CHEESECAKE, 1]]);
    expect(done.state.pending).toBeNull();
  });

  it("보류 중 독립 새 주문을 먼저 처리하고 기존 온도를 다시 묻는다", () => {
    const asked = run(initialOrderState(), add(LATTE));
    const added = run(asked.state, add(CHEESECAKE));
    expect(added.state.lines.map((line) => line.menuId)).toEqual([CHEESECAKE]);
    expect(added.state.pending?.kind).toBe("temperature");
    expect(added.reply.text).toContain("카페라떼는 따뜻한 것과 아이스 중");
    const done = run(added.state, ...interpretStageReply(added.state, "아이스요")!);
    expect(done.state.lines.map((line) => line.menuId)).toEqual([CHEESECAKE, LATTE]);
  });

  it("새 주문에도 온도 질문이 필요하면 새 메뉴부터 완성하고 기존 온도를 다시 묻는다", () => {
    const original = run(initialOrderState(), add(LATTE));
    const newer = run(original.state, add(CAPPUCCINO));
    expect(newer.state.pending?.kind).toBe("temperature");
    expect(newer.reply.text).toContain("카푸치노는 따뜻한 것과 아이스 중");
    const newDone = run(newer.state, ...interpretStageReply(newer.state, "아이스요")!);
    expect(newDone.state.lines.map((line) => line.menuId)).toEqual([CAPPUCCINO]);
    expect(newDone.reply.text).toContain("카페라떼는 따뜻한 것과 아이스 중");
    const allDone = run(newDone.state, ...interpretStageReply(newDone.state, "따뜻하게")!);
    expect(allDone.state.lines.map((line) => line.menuId)).toEqual([CAPPUCCINO, LATTE]);
  });

  it("보류 중 다른 기존 항목의 수량 수정은 보류와 대기 주문을 유지한다", () => {
    let result = run(initialOrderState(), add(AMERICANO, { temperature: "ICE" }));
    result = run(result.state, add(LATTE), add(CHEESECAKE));
    const updated = run(result.state, { type: "update_item", target: { menu_id: AMERICANO }, quantity: 2 });
    expect(updated.state.lines[0].quantity).toBe(2);
    expect(updated.state.pending?.kind).toBe("temperature");
    expect(updated.state.queued).toEqual([add(CHEESECAKE)]);
    const done = run(updated.state, ...interpretStageReply(updated.state, "아이스요")!);
    expect(done.state.lines.map((line) => line.menuId)).toEqual([AMERICANO, LATTE, CHEESECAKE]);
  });

  it("단답만 로컬에서 처리하고 추가 요청이 섞이면 서버로 보낸다", () => {
    const temp = run(initialOrderState(), add(LATTE)).state;
    expect(interpretStageReply(temp, "아이스요")).toMatchObject([{ pending_resolution: true, temperature: "ICE" }]);
    expect(interpretStageReply(temp, "아이스요, 치즈케이크도 주세요")).toBeNull();
    const clear = run(run(initialOrderState(), add(CHEESECAKE)).state, { type: "clear_cart" }).state;
    expect(interpretStageReply(clear, "네")).toMatchObject([{ type: "clear_cart", confirmed: true }]);
    expect(interpretStageReply(clear, "아니요")).toEqual([{ type: "cancel_pending" }]);
    expect(run(clear, ...interpretStageReply(clear, "아니요")!).state.lines).toHaveLength(1);
  });

  it("명시적으로 보류 요청을 취소하면 해당 주문과 대기 액션을 실행하지 않는다", () => {
    const asked = run(initialOrderState(), add(LATTE), add(CHEESECAKE));
    const cancelled = run(asked.state, { type: "cancel_pending" });
    expect(cancelled.state.pending).toBeNull();
    expect(cancelled.state.queued).toHaveLength(0);
    expect(cancelled.state.lines).toHaveLength(0);
  });

  it("6.1.2 같은 메뉴 다른 옵션은 별도 항목, 합계 7,300원", () => {
    const { state } = run(
      initialOrderState(),
      add(LATTE, { temperature: "ICE" }),
      add(LATTE, { temperature: "ICE", options: { "OPT-005": 1 } }),
    );
    expect(state.lines).toHaveLength(2);
    expect(priceCart(state.lines).total).toBe(7300);
  });

  it("같은 메뉴·온도·옵션은 수량을 합친다", () => {
    const { state } = run(initialOrderState(), add(AMERICANO, { temperature: "ICE" }), add(AMERICANO, { temperature: "ICE" }));
    expect(state.lines).toHaveLength(1);
    expect(state.lines[0].quantity).toBe(2);
  });

  it("6.1.3 10잔 이상은 확인 후 담는다 (100잔 250,000원)", () => {
    const asked = run(initialOrderState(), add(AMERICANO, { temperature: "ICE", quantity: 100 }));
    expect(asked.state.lines).toHaveLength(0);
    expect(asked.reply.text).toContain("100잔(250,000원)");
    const done = run(asked.state, ...interpretStageReply(asked.state, "네")!);
    expect(priceCart(done.state.lines).total).toBe(250000);
  });

  it("5.6 샷 3개 요청 → 최대 2개로 담을지 제안", () => {
    const { state, reply } = run(initialOrderState(), add(LATTE, { temperature: "HOT", options: { "OPT-001": 3 } }));
    expect(state.lines).toHaveLength(0);
    expect(reply.text).toContain("최대 2개");
    const fix = reply.choices.find((c) => c.label.startsWith("네"));
    expect(fix?.action).toMatchObject({ options: { "OPT-001": 2 } });
  });

  it("6.2 수량 변경과 6.3 옵션 제거 후 총액 재계산", () => {
    let { state } = run(initialOrderState(), add(LATTE, { temperature: "HOT", options: { "OPT-001": 1 } }));
    const lineId = state.lines[0].lineId;
    ({ state } = run(state, { type: "update_item", target: { line_id: lineId }, quantity: 2 }));
    expect(priceCart(state.lines).total).toBe(7800);
    const removed = run(state, { type: "update_item", target: { menu_id: LATTE }, set_options: { "OPT-001": 0 } });
    expect(priceCart(removed.state.lines).total).toBe(6800);
    expect(removed.reply.text).toContain("-1,000원");
  });

  it("6.4 메뉴 빼기, 모두 빼면 인사로 돌아감", () => {
    const { state } = run(initialOrderState(), add(CHEESECAKE));
    const removed = run(state, { type: "remove_item", target: { menu_id: CHEESECAKE } });
    expect(removed.state.lines).toHaveLength(0);
    expect(removed.state.stage).toBe("greeting");
    expect(removed.reply.text).toContain("안녕하세요");
  });

  it("6.5 메뉴 바꾸기: 온도 유지", () => {
    const { state } = run(initialOrderState(), add(AMERICANO, { temperature: "ICE", quantity: 2 }));
    const replaced = run(state, { type: "replace_menu", target: { menu_id: AMERICANO }, menu_id: LATTE });
    expect(replaced.state.lines).toHaveLength(1);
    expect(replaced.state.lines[0]).toMatchObject({ menuId: LATTE, temperature: "ICE", quantity: 2 });
  });

  it("6.5.1 대상이 모호하면 어느 항목인지 되묻는다", () => {
    const { state } = run(initialOrderState(), add(LATTE, { temperature: "HOT" }), add(VANILLA_LATTE, { temperature: "HOT" }));
    const asked = run(state, { type: "remove_item", target: { menu_ids: [LATTE, VANILLA_LATTE] } });
    expect(asked.state.pending?.kind).toBe("target");
    expect(asked.reply.choices).toHaveLength(2);
    const done = run(asked.state, asked.reply.choices[1].action as EngineAction);
    expect(done.state.lines.map((l) => l.menuId)).toEqual([LATTE]);
  });

  it("6.5.1 대상 확인에 메뉴명을 입력해도 원래 삭제 요청을 이어서 처리한다", () => {
    const { state } = run(initialOrderState(), add(LATTE, { temperature: "HOT" }), add(VANILLA_LATTE, { temperature: "HOT" }));
    const asked = run(state, { type: "remove_item", target: { menu_ids: [LATTE, VANILLA_LATTE] } });
    const typed = interpretStageReply(asked.state, "바닐라라떼요");
    expect(typed).toMatchObject([{ type: "remove_item", target: { line_id: asked.state.lines[1].lineId } }]);
    const done = run(asked.state, ...typed!);
    expect(done.state.lines.map((line) => line.menuId)).toEqual([LATTE]);
  });

  it("6.7 전체 취소는 확인 후 비우고 인사로", () => {
    const { state } = run(initialOrderState(), add(CHEESECAKE));
    const asked = run(state, { type: "clear_cart" });
    expect(asked.state.lines).toHaveLength(1);
    const done = run(asked.state, { type: "clear_cart", confirmed: true });
    expect(done.state.lines).toHaveLength(0);
    expect(done.state.stage).toBe("greeting");
  });
});

describe("주문 진행 흐름 (flow)", () => {
  it("6.8 → 6 매장/포장 → 7 최종 확인 → 8 결제 → 9 완료", () => {
    let result = run(initialOrderState(), add(AMERICANO, { temperature: "ICE", quantity: 2 }), add(CHEESECAKE));
    result = run(result.state, ...interpretStageReply(result.state, "그게 다예요")!);
    expect(result.state.stage).toBe("dine_option");
    expect(result.reply.text).toContain("매장에서 드시나요, 포장하시나요?");

    result = run(result.state, ...interpretStageReply(result.state, "포장이요")!);
    expect(result.state.stage).toBe("final_confirm");
    expect(result.reply.text).toContain("총 9,200원 · 포장");
    expect(result.reply.text).toContain("이대로 주문할까요?");

    result = run(result.state, ...interpretStageReply(result.state, "네")!);
    expect(result.state.stage).toBe("payment");
    expect(result.reply.text).toBe("카드와 간편결제 중 선택해 주세요.");

    result = run(result.state, ...interpretStageReply(result.state, "현금 돼요?")!);
    expect(result.reply.text).toContain("현금 결제는 지원하지 않아요");
    expect(result.state.stage).toBe("payment");

    result = run(result.state, ...interpretStageReply(result.state, "카드로 할게요")!);
    expect(result.state.stage).toBe("pay_confirm");

    result = run(result.state, { type: "pay_now" });
    expect(result.state.stage).toBe("complete");
    expect(result.reply.text).toContain("주문번호는 123번");
    expect(result.reply.text).toContain("메뉴가 준비되면 번호로 불러 드릴게요.");
  });

  it("1.3 매장/포장을 미리 정하면 건너뛴다", () => {
    let result = run(initialOrderState(), { type: "set_dine_option", value: "dine_in" }, add(CHEESECAKE));
    result = run(result.state, { type: "finish_ordering" });
    expect(result.state.stage).toBe("final_confirm");
  });

  it("7.1 '이대로 주문할게요'는 요약부터 보여 준다", () => {
    let result = run(initialOrderState(), { type: "set_dine_option", value: "takeout" }, add(CHEESECAKE));
    result = run(result.state, { type: "confirm_order" });
    expect(result.state.stage).toBe("final_confirm");
  });

  it("7.1.2 최종 확인 중 추가하면 바뀐 요약으로 다시 확인", () => {
    let result = run(initialOrderState(), { type: "set_dine_option", value: "takeout" }, add(AMERICANO, { temperature: "HOT" }));
    result = run(result.state, { type: "finish_ordering" });
    result = run(result.state, add(COOKIE));
    expect(result.state.stage).toBe("final_confirm");
    expect(result.reply.text).toContain("총 4,700원");
    expect(result.reply.text).toContain("이대로 주문할까요?");
    expect(interpretStageReply(result.state, "네")).toEqual([{ type: "confirm_order" }]);
    expect(interpretStageReply(result.state, "네, 그런데 쿠키도 추가요")).toBeNull();

    result = run(result.state, ...interpretStageReply(result.state, "네")!);
    expect(result.state.stage).toBe("payment");
    result = run(result.state, ...interpretStageReply(result.state, "카드로 할게요")!);
    expect(result.state.stage).toBe("pay_confirm");
    result = run(result.state, { type: "pay_now" });
    expect(result.state.stage).toBe("complete");
    expect(priceCart(result.state.lines).total).toBe(4700);
    expect(result.reply.text).toContain("주문번호는 123번");
  });

  it("7.3.1 빈 장바구니로 결제 요청하면 메뉴 선택으로", () => {
    const result = run(initialOrderState(), { type: "finish_ordering" });
    expect(result.reply.text).toContain("아직 담긴 메뉴가 없어요");
    expect(result.state.stage).toBe("greeting");
  });

  it("8.2.1 따뜻한 레몬에이드 → ICE만 가능 + 따뜻한 대안", () => {
    const result = run(initialOrderState(), add(LEMONADE, { temperature: "HOT" }));
    expect(result.reply.text).toContain("레몬에이드는 아이스만 가능해요.");
    expect(result.reply.choices[0].label).toBe("아이스 레몬에이드로 담기");
    expect(result.reply.menuIds).toEqual(expect.arrayContaining(["MENU-017", "MENU-018"]));
  });

  it("8.4.2 보류 질문은 stagePrompt로 다시 물을 수 있다", () => {
    const asked = run(initialOrderState(), add(LATTE));
    expect(stagePrompt(asked.state).text).toBe("카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?");
  });

  it("8.2 '따뜻한 아이스'처럼 충돌하면 로컬에서 정하지 않고 서버로", () => {
    const asked = run(initialOrderState(), add(CAPPUCCINO));
    expect(interpretStageReply(asked.state, "따뜻한 아이스")).toBeNull();
  });
});
