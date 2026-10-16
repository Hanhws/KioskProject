/**
 * 주문 엔진: 장바구니 액션과 주문 진행 단계(HTI 2쪽 '주문 진행 흐름')를 처리한다.
 *
 * - 화면 버튼과 서버(통합 라우터)의 cart_actions가 모두 runActions()로 들어온다.
 * - 메뉴·옵션·수량은 validation.ts, 금액은 pricing.ts만 사용한다.
 * - 키오스크 문구는 HTI의 기대 응답과 '다음 안내'를 따른다.
 */

import { dessertPairingFor, findVariant, getMenu, isCoffee, isDrink, MENUS } from "./catalog";
import { dineLabel, josa, paymentLabel, temperatureLabel, won } from "./format";
import { lineSpoken, lineTitle, optionLabels, priceCart, priceLine, unitWord } from "./pricing";
import { QUANTITY_CONFIRM_THRESHOLD, validateItem } from "./validation";
import type {
  CartAction,
  CartLine,
  Choice,
  EngineAction,
  ItemDraft,
  KioskReply,
  LineTarget,
  OptionSelection,
  OrderState,
  Temperature,
} from "./types";

type AddAction = Extract<CartAction, { type: "add_item" }>;

export const GREETING =
  "안녕하세요. 원하시는 메뉴를 말씀하시거나 '달콤한 거', '커피 말고'처럼 취향을 말씀해 주세요.";

export const GREETING_CHOICES: Choice[] = [
  { label: "커피", action: { type: "send", text: "커피 메뉴 뭐 있어요?" } },
  { label: "커피 말고 다른 거", action: { type: "send", text: "커피 말고 다른 거 추천해 주세요" } },
  { label: "달콤한 거", action: { type: "send", text: "달콤한 음료 추천해 주세요" } },
  { label: "디저트", action: { type: "send", text: "디저트 뭐 있어요?" } },
];

export function initialOrderState(): OrderState {
  return {
    lines: [],
    dineOption: null,
    stage: "greeting",
    pending: null,
    queued: [],
    paymentMethod: null,
    orderNumber: null,
    dessertSuggested: false,
    coffeeHintShown: false,
    nextLineSeq: 1,
  };
}

export function greetingReply(): KioskReply {
  return { text: GREETING, choices: GREETING_CHOICES, menuIds: [] };
}

export interface EngineOptions {
  /** 모의 주문번호 생성기. 테스트에서 고정값을 넣는다 */
  makeOrderNumber?: () => string;
  /** 보류 주문을 잠시 제쳐 두고 새 액션을 실행할 때 중간 단계 안내를 생략한다. */
  suppressStagePrompt?: boolean;
}

interface Ctx {
  texts: string[];
  choices: Choice[];
  menuIds: string[];
  changed: boolean;
  promptDone: boolean;
  stageBefore: OrderState["stage"];
  opts: EngineOptions;
}

const POST_CONFIRM_STAGES = new Set(["final_confirm", "payment", "pay_confirm"]);

function defaultOrderNumber(): string {
  return String(100 + Math.floor(Math.random() * 900));
}

function optionKey(options: OptionSelection): string {
  return Object.keys(options)
    .filter((id) => options[id] > 0)
    .sort()
    .map((id) => `${id}:${options[id]}`)
    .join(",");
}

function lineKey(line: Pick<CartLine, "menuId" | "temperature" | "options">): string {
  return `${line.menuId}|${line.temperature ?? "-"}|${optionKey(line.options)}`;
}

function total(state: OrderState): number {
  return priceCart(state.lines).total;
}

function summaryText(state: OrderState): string {
  const cart = priceCart(state.lines);
  const rows = cart.lines.map(
    (line) => `· ${lineTitle(line)} ×${line.quantity} — ${won(line.lineTotal)}`,
  );
  return [...rows, `총 ${won(cart.total)} · ${dineLabel(state.dineOption)}`].join("\n");
}

function temperatureChoices(action: AddAction): Choice[] {
  const menu = getMenu(action.menu_id);
  if (!menu) return [];
  return menu.temperatures.map((temperature) => {
    const variant = findVariant(menu, temperature);
    return {
      label: `${temperature === "HOT" ? "따뜻하게" : "아이스"}${variant ? ` ${won(variant.price)}` : ""}`,
      action: { ...action, temperature, pending_resolution: true },
    };
  });
}

const FRUITY = ["상큼", "새콤", "시트러스", "레몬", "자몽", "유자", "딸기", "과일", "청포도", "망고"];

/** 요청 온도가 안 되는 메뉴 대신, 그 온도가 가능한 비슷한 음료 2개 (HTI 8.2.1) */
function temperatureAlternatives(menuId: string, temperature: Temperature): string[] {
  const origin = getMenu(menuId);
  if (!origin) return [];
  const fruity = (tags: string[]) => tags.some((tag) => FRUITY.some((word) => tag.includes(word)));
  return MENUS.filter((menu) => menu.id !== menuId && isDrink(menu) && menu.temperatures.includes(temperature))
    .map((menu) => {
      const overlap = menu.flavorTags.filter((tag) => origin.flavorTags.includes(tag)).length;
      const score =
        overlap * 2 +
        (fruity(menu.flavorTags) && fruity(origin.flavorTags) ? 3 : 0) +
        (isCoffee(menu) === isCoffee(origin) ? 2 : 0) +
        (menu.sweetness === origin.sweetness ? 1 : 0);
      return { id: menu.id, score };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 2)
    .map((row) => row.id);
}

function resolveTarget(state: OrderState, target: LineTarget): CartLine[] {
  if (target.line_id) return state.lines.filter((line) => line.lineId === target.line_id);
  const menuIds = target.menu_ids ?? (target.menu_id ? [target.menu_id] : []);
  return state.lines.filter(
    (line) =>
      menuIds.includes(line.menuId) && (!target.temperature || line.temperature === target.temperature),
  );
}

function askTarget(s: OrderState, ctx: Ctx, action: CartAction & { target: LineTarget }, lines: CartLine[]) {
  const question = "어느 항목을 말씀하시는 건가요?";
  s.pending = { kind: "target", action, lineIds: lines.map((line) => line.lineId), question };
  ctx.texts.push(question);
  ctx.choices.push(
    ...lines.map((line) => ({
      label: `${lineTitle(line)} ×${line.quantity}`,
      action: { ...action, target: { line_id: line.lineId } } as CartAction,
    })),
  );
}

/** 같은 메뉴·온도·옵션이면 수량을 합치고, 다르면 새 항목으로 담는다 (HTI 6.1.2) */
function upsertLine(s: OrderState, line: Omit<CartLine, "lineId">, replaceLineId?: string): CartLine {
  if (replaceLineId) s.lines = s.lines.filter((row) => row.lineId !== replaceLineId);
  const existing = s.lines.find((row) => lineKey(row) === lineKey(line));
  if (existing) {
    existing.quantity += line.quantity;
    return existing;
  }
  const created = { ...line, lineId: `L${s.nextLineSeq}` };
  s.nextLineSeq += 1;
  s.lines.push(created);
  return created;
}

function handleAdd(s: OrderState, ctx: Ctx, action: AddAction, replaceLineId?: string) {
  const draft: ItemDraft = {
    menuId: action.menu_id,
    temperature: action.temperature ?? null,
    options: action.options,
    quantity: action.quantity ?? 1,
  };
  const result = validateItem(draft);
  const menu = getMenu(action.menu_id);

  if (!result.ok) {
    const issues = result.issues;
    if (issues.length === 1 && issues[0].code === "temperature_required") {
      // 단계 3 필수 정보 확인: 온도만 짧게 묻는다 (HTI 6.1.1)
      const question = issues[0].message;
      s.pending = { kind: "temperature", action, replaceLineId, question };
      ctx.texts.push(question);
      ctx.choices.push(...temperatureChoices(action));
      return;
    }
    for (const issue of issues) ctx.texts.push(issue.message);

    const tempIssue = issues.find((issue) => issue.code === "temperature_unavailable");
    if (tempIssue && menu && action.temperature) {
      // HTI 8.2.1: 가능한 온도로 주문할지, 요청 온도가 되는 대안을 고를지
      const available = tempIssue.availableTemperatures ?? [];
      for (const temperature of available) {
        ctx.choices.push({
          label: `${josa(`${temperatureLabel(temperature)} ${menu.name}`.trim(), "으로/로")} 담기`,
          action: { ...action, temperature },
          variant: "primary",
        });
      }
      const alternatives = temperatureAlternatives(menu.id, action.temperature);
      if (alternatives.length) {
        const names = alternatives.map((id) => getMenu(id)?.name).join("·");
        ctx.texts.push(`${temperatureLabel(action.temperature)} 음료로는 ${names}도 있어요.`);
        ctx.menuIds.push(...alternatives);
      }
      ctx.promptDone = true;
      return;
    }

    const fixable = issues.find((issue) => issue.fixedDraft);
    if (fixable?.fixedDraft) {
      // HTI 5.6: 최대 수량으로 담을지 묻는다
      const fixed = fixable.fixedDraft;
      ctx.texts.push(`${josa(optionLabels(fixed.options ?? {}).join(", "), "으로/로")} 담을까요?`);
      ctx.choices.push(
        { label: "네, 그렇게 담아 주세요", action: { ...action, options: fixed.options }, variant: "primary" },
        { label: "다시 고를게요", action: { type: "cancel_pending" } },
      );
      ctx.promptDone = true;
      return;
    }

    const optionIssue = issues.find((issue) => issue.code === "option_not_applicable");
    if (optionIssue && menu) {
      // HTI 5.3.1, 5.4.1: 불가 이유 + 옵션 없이 담기 / 옵션이 되는 다른 메뉴 제안
      const withoutInvalid: OptionSelection = { ...(action.options ?? {}) };
      for (const issue of issues) if (issue.optionId) delete withoutInvalid[issue.optionId];
      ctx.choices.push({
        label: `${menu.name} 옵션 없이 담기`,
        action: { ...action, options: withoutInvalid },
      });
      if (optionIssue.alternativeMenuIds?.length) {
        const names = optionIssue.alternativeMenuIds.map((id) => getMenu(id)?.name).join(", ");
        ctx.texts.push(`이 옵션은 ${names} 같은 메뉴에서 가능해요.`);
        ctx.menuIds.push(...optionIssue.alternativeMenuIds);
      }
      ctx.promptDone = true;
    }
    return;
  }

  const { item } = result;
  if (item.quantity >= QUANTITY_CONFIRM_THRESHOLD && !action.confirmed) {
    // HTI 6.1.3: 10잔 이상이면 한 번 확인
    const lineTotal = priceLine({
      lineId: "preview",
      menuId: item.menu.id,
      variantId: item.variant.id,
      temperature: item.temperature,
      options: item.options,
      quantity: item.quantity,
    }).lineTotal;
    const title = lineTitle({ menuId: item.menu.id, temperature: item.temperature, options: item.options } as CartLine);
    const question = `${title} ${item.quantity}${unitWord(item.menu.id)}(${won(lineTotal)}) 맞으세요?`;
    s.pending = { kind: "quantity_confirm", action, question };
    ctx.texts.push(question);
    ctx.choices.push(
      { label: "네, 맞아요", action: { ...action, confirmed: true }, variant: "primary" },
      { label: "아니요", action: { type: "cancel_pending" } },
    );
    return;
  }

  const line = upsertLine(
    s,
    {
      menuId: item.menu.id,
      variantId: item.variant.id,
      temperature: item.temperature,
      options: item.options,
      quantity: item.quantity,
    },
    replaceLineId,
  );
  const priced = priceLine({ ...line, quantity: item.quantity });
  const unit = unitWord(item.menu.id);
  const title = lineTitle(line);
  if (line.quantity !== item.quantity) {
    ctx.texts.push(
      `${title} ${josa(`${item.quantity}${unit}`, "을/를")} 더 담았어요. 지금 ${line.quantity}${unit}(${won(priceLine(line).lineTotal)})이에요.`,
    );
  } else {
    ctx.texts.push(`${title} ${item.quantity}${unit}(${won(priced.lineTotal)})을 담았어요.`);
  }
  if (isCoffee(item.menu) && !s.coffeeHintShown) {
    // 단계 4: 커피를 처음 담을 때만 한 번 덧붙임
    s.coffeeHintShown = true;
    ctx.texts.push("샷·시럽 추가도 가능해요.");
    ctx.choices.push({ label: "옵션 바꾸기", action: { type: "edit_line", line_id: line.lineId } });
  }
  ctx.changed = true;
}

function handleUpdate(s: OrderState, ctx: Ctx, action: Extract<CartAction, { type: "update_item" }>) {
  const lines = resolveTarget(s, action.target);
  if (lines.length === 0) {
    ctx.texts.push("장바구니에서 해당 메뉴를 찾지 못했어요.");
    return;
  }
  if (lines.length > 1) {
    askTarget(s, ctx, action, lines);
    return;
  }
  const line = lines[0];
  const options: OptionSelection = action.replace_options
    ? { ...(action.set_options ?? {}) }
    : { ...line.options, ...(action.set_options ?? {}) };
  const quantity = action.quantity ?? line.quantity;
  const result = validateItem({
    menuId: line.menuId,
    temperature: action.temperature ?? line.temperature,
    options,
    quantity,
  });
  if (!result.ok) {
    for (const issue of result.issues) ctx.texts.push(issue.message);
    ctx.promptDone = true;
    return;
  }
  const { item } = result;
  if (item.quantity >= QUANTITY_CONFIRM_THRESHOLD && item.quantity > line.quantity && !action.confirmed) {
    const question = `${lineTitle(line)} ${item.quantity}${unitWord(line.menuId)} 맞으세요?`;
    s.pending = { kind: "quantity_confirm", action, question };
    ctx.texts.push(question);
    ctx.choices.push(
      { label: "네, 맞아요", action: { ...action, confirmed: true }, variant: "primary" },
      { label: "아니요", action: { type: "cancel_pending" } },
    );
    return;
  }

  const before = priceLine(line);
  const changes: string[] = [];
  if (item.temperature !== line.temperature) changes.push(`온도 ${temperatureLabel(item.temperature)}`);
  if (optionKey(item.options) !== optionKey(line.options)) {
    const labels = optionLabels(item.options);
    changes.push(`옵션 ${labels.length ? labels.join(", ") : "없음"}`);
  }
  if (item.quantity !== line.quantity) changes.push(`수량 ${item.quantity}${unitWord(line.menuId)}`);

  if (changes.length === 0) {
    ctx.texts.push(action.temperature ? `${lineSpoken(line)}는 이미 ${temperatureLabel(line.temperature)}로 담겨 있어요.` : `${lineSpoken(line)}에서 바뀐 내용이 없어요.`);
    return;
  }

  const updated: Omit<CartLine, "lineId"> = {
    menuId: line.menuId,
    variantId: item.variant.id,
    temperature: item.temperature,
    options: item.options,
    quantity: item.quantity,
  };
  const index = s.lines.findIndex((row) => row.lineId === line.lineId);
  const twin = s.lines.find((row) => row.lineId !== line.lineId && lineKey(row) === lineKey(updated));
  let changedLine: CartLine;
  if (twin) {
    // 바꾼 결과가 다른 항목과 같아지면 하나로 합친다
    twin.quantity += updated.quantity;
    s.lines.splice(index, 1);
    changedLine = twin;
  } else {
    s.lines[index] = { ...updated, lineId: line.lineId };
    changedLine = s.lines[index];
  }
  const after = priceLine(changedLine);
  const diff = after.lineTotal - before.lineTotal;
  const diffText = diff === 0 ? "" : `(${diff > 0 ? "+" : "-"}${won(Math.abs(diff))})`;
  ctx.texts.push(
    changes.length
      ? `${lineSpoken(line)} 변경: ${changes.join(", ")}. 이 항목은 ${won(after.lineTotal)}${diffText}이에요.`
      : `${lineSpoken(line)}에서 바뀐 내용이 없어요.`,
  );
  ctx.changed = true;
}

function handleRemove(s: OrderState, ctx: Ctx, action: Extract<CartAction, { type: "remove_item" }>) {
  const lines = resolveTarget(s, action.target);
  if (lines.length === 0) {
    ctx.texts.push("장바구니에서 해당 메뉴를 찾지 못했어요.");
    return;
  }
  if (lines.length > 1) {
    // HTI 6.5.1: 대상이 모호하면 되묻기
    askTarget(s, ctx, action, lines);
    return;
  }
  const line = lines[0];
  s.lines = s.lines.filter((row) => row.lineId !== line.lineId);
  ctx.texts.push(`${josa(lineTitle(line), "을/를")} 뺐어요.`);
  ctx.changed = true;
}

function handleReplace(s: OrderState, ctx: Ctx, action: Extract<CartAction, { type: "replace_menu" }>) {
  const lines = resolveTarget(s, action.target);
  const menu = getMenu(action.menu_id);
  if (!menu) {
    ctx.texts.push("판매하지 않는 메뉴예요.");
    return;
  }
  if (lines.length === 0) {
    ctx.texts.push("장바구니에서 바꿀 메뉴를 찾지 못했어요.");
    return;
  }
  if (lines.length > 1) {
    askTarget(s, ctx, action, lines);
    return;
  }
  const line = lines[0];
  // HTI 6.5: 온도는 기존 값 유지. 새 메뉴에 안 되는 옵션은 빼고 알린다
  const keptOptions: OptionSelection = {};
  const dropped: string[] = [];
  for (const [optionId, count] of Object.entries(line.options)) {
    const check = validateItem({ menuId: menu.id, temperature: null, options: { [optionId]: count } });
    const notApplicable = !check.ok && check.issues.some((issue) => issue.optionId === optionId);
    if (notApplicable) dropped.push(...optionLabels({ [optionId]: count }));
    else keptOptions[optionId] = count;
  }
  const temperature = line.temperature && menu.temperatures.includes(line.temperature) ? line.temperature : null;
  ctx.texts.push(`${josa(lineSpoken(line), "을/를")} 빼고 ${josa(menu.name, "으로/로")} 바꿀게요.`);
  if (dropped.length) ctx.texts.push(`${dropped.join(", ")} 옵션은 ${menu.name}에 적용할 수 없어 뺐어요.`);
  const add: AddAction = {
    type: "add_item",
    menu_id: menu.id,
    temperature,
    options: keptOptions,
    quantity: line.quantity,
    confirmed: true,
  };
  handleAdd(s, ctx, add, line.lineId);
}

function startCheckout(s: OrderState, ctx: Ctx) {
  if (s.lines.length === 0) {
    // HTI 7.3.1
    s.stage = "greeting";
    ctx.texts.push("아직 담긴 메뉴가 없어요. 어떤 메뉴로 드릴까요?");
    ctx.choices.push(...GREETING_CHOICES);
    ctx.promptDone = true;
    return;
  }
  if (!s.dineOption) {
    // HTI 6.8, 7.1.1: 매장/포장부터
    s.stage = "dine_option";
    ctx.promptDone = true;
    pushStagePrompt(s, ctx);
    return;
  }
  s.stage = "final_confirm";
  ctx.promptDone = true;
  pushStagePrompt(s, ctx);
}

function pushStagePrompt(s: OrderState, ctx: Ctx) {
  const prompt = stagePrompt(s);
  if (prompt.text) ctx.texts.push(prompt.text);
  ctx.choices.push(...prompt.choices);
  ctx.menuIds.push(...prompt.menuIds);
}

/**
 * 현재 단계에서 키오스크가 다시 해야 할 질문.
 * 딴 질문(HTI 8.4.2) 뒤 멈췄던 질문을 되묻을 때도 사용한다.
 */
export function stagePrompt(state: OrderState): KioskReply {
  const pending = state.pending;
  if (pending) {
    if (pending.kind === "temperature") {
      return { text: pending.question, choices: temperatureChoices(pending.action), menuIds: [] };
    }
    if (pending.kind === "quantity_confirm") {
      return {
        text: pending.question,
        choices: [
          { label: "네, 맞아요", action: { ...pending.action, confirmed: true } as CartAction, variant: "primary" },
          { label: "아니요", action: { type: "cancel_pending" } },
        ],
        menuIds: [],
      };
    }
    if (pending.kind === "clear_confirm") {
      return {
        text: pending.question,
        choices: [
          { label: "네, 비워 주세요", action: { type: "clear_cart", confirmed: true }, variant: "primary" },
          { label: "아니요", action: { type: "cancel_pending" } },
        ],
        menuIds: [],
      };
    }
    const lines = state.lines.filter((line) => pending.lineIds.includes(line.lineId));
    return {
      text: pending.question,
      choices: lines.map((line) => ({
        label: `${lineTitle(line)} ×${line.quantity}`,
        action: { ...pending.action, target: { line_id: line.lineId } } as CartAction,
      })),
      menuIds: [],
    };
  }

  switch (state.stage) {
    case "greeting":
      return greetingReply();
    case "ordering":
    case "more":
      return {
        text: `지금까지 총 ${won(total(state))}이에요. 더 필요하신 건 없으세요?`,
        choices: [
          { label: "주문 마치기", action: { type: "finish_ordering" }, variant: "primary" },
          { label: "장바구니 보기", action: { type: "show_cart" } },
        ],
        menuIds: [],
      };
    case "dine_option":
      return {
        text: "매장에서 드시나요, 포장하시나요?",
        choices: [
          { label: "매장에서 먹을게요", action: { type: "set_dine_option", value: "dine_in" } },
          { label: "포장할게요", action: { type: "set_dine_option", value: "takeout" } },
        ],
        menuIds: [],
      };
    case "final_confirm":
      return {
        text: `주문 내역을 확인해 주세요.\n${summaryText(state)}\n이대로 주문할까요?`,
        choices: [
          { label: "네, 주문할게요", action: { type: "confirm_order" }, variant: "primary" },
          { label: "더 담을게요", action: { type: "focus_input" } },
        ],
        menuIds: [],
      };
    case "payment":
      return {
        text: "카드, 간편결제, 현금 중 선택해 주세요.",
        choices: [
          { label: "카드", action: { type: "select_payment", method: "card" }, variant: "primary" },
          { label: "간편결제", action: { type: "select_payment", method: "easy_pay" }, variant: "primary" },
          { label: "현금", action: { type: "select_payment", method: "cash" }, variant: "primary" },
        ],
        menuIds: [],
      };
    case "pay_confirm":
      return {
        text: state.paymentMethod === "cash"
          ? `현금으로 총 ${won(total(state))}을 결제할게요. 주문 후 카운터에서 결제해 주세요. 이대로 주문할까요?`
          : `${paymentLabel(state.paymentMethod)}로 총 ${won(total(state))}을 결제할게요. 이대로 결제할까요?`,
        choices: [
          { label: "결제하기", action: { type: "pay_now" }, variant: "primary" },
          { label: "결제 수단 바꾸기", action: { type: "confirm_order" } },
        ],
        menuIds: [],
      };
    case "complete":
      return {
        text: "",
        choices: [{ label: "새 주문 시작", action: { type: "new_order" }, variant: "primary" }],
        menuIds: [],
      };
  }
}

function handle(s: OrderState, ctx: Ctx, action: EngineAction, opts: EngineOptions) {
  if (s.stage === "complete" && action.type !== "new_order") {
    ctx.texts.push(`주문이 이미 완료됐어요. 주문번호는 ${s.orderNumber}번이에요.`);
    ctx.choices.push({ label: "새 주문 시작", action: { type: "new_order" }, variant: "primary" });
    ctx.promptDone = true;
    return;
  }
  switch (action.type) {
    case "add_item":
      handleAdd(s, ctx, action);
      return;
    case "update_item":
      handleUpdate(s, ctx, action);
      return;
    case "remove_item":
      handleRemove(s, ctx, action);
      return;
    case "replace_menu":
      handleReplace(s, ctx, action);
      return;
    case "clear_cart":
      if (s.lines.length === 0) {
        ctx.texts.push("장바구니가 이미 비어 있어요.");
        return;
      }
      if (!action.confirmed) {
        const question = "담긴 메뉴를 모두 비울까요?";
        s.pending = { kind: "clear_confirm", question };
        ctx.texts.push(question);
        ctx.choices.push(...stagePrompt(s).choices);
        return;
      }
      Object.assign(s, initialOrderState());
      ctx.texts.push("장바구니를 비웠어요.");
      ctx.promptDone = true;
      pushStagePrompt(s, ctx);
      return;
    case "set_dine_option":
      s.dineOption = action.value;
      ctx.texts.push(action.value === "takeout" ? "포장으로 준비할게요." : "매장에서 드시는 걸로 할게요.");
      if (s.stage === "dine_option" || POST_CONFIRM_STAGES.has(s.stage)) {
        startCheckout(s, ctx);
      }
      return;
    case "finish_ordering":
      startCheckout(s, ctx);
      return;
    case "show_cart":
      if (s.lines.length === 0) {
        ctx.texts.push("장바구니가 비어 있어요.");
        return;
      }
      ctx.texts.push(`지금 담긴 메뉴예요.\n${summaryText(s)}\n이대로 주문할까요, 더 담으실 게 있나요?`);
      ctx.choices.push(
        { label: "주문 마치기", action: { type: "finish_ordering" }, variant: "primary" },
        { label: "더 담을게요", action: { type: "focus_input" } },
      );
      ctx.promptDone = true;
      return;
    case "confirm_order":
      if (s.lines.length === 0 || !s.dineOption || !POST_CONFIRM_STAGES.has(s.stage)) {
        // HTI 7.1: 요약을 먼저 보여 주고 마지막 확인을 받는다
        startCheckout(s, ctx);
        return;
      }
      s.stage = "payment";
      s.paymentMethod = null;
      ctx.promptDone = true;
      pushStagePrompt(s, ctx);
      return;
    case "select_payment":
      s.paymentMethod = action.method;
      if (s.stage === "payment" || s.stage === "pay_confirm") {
        s.stage = "pay_confirm";
        ctx.promptDone = true;
        pushStagePrompt(s, ctx);
      } else {
        ctx.texts.push(`${paymentLabel(action.method)} 결제로 기억해 둘게요.`);
        startCheckout(s, ctx);
      }
      return;
    case "pay_now":
      if (s.stage !== "pay_confirm" || !s.paymentMethod || s.lines.length === 0 || !s.dineOption) {
        startCheckout(s, ctx);
        return;
      }
      s.orderNumber = (opts.makeOrderNumber ?? defaultOrderNumber)();
      s.stage = "complete";
      ctx.texts.push(
        s.paymentMethod === "cash"
          // 키오스크에 현금 투입구가 없으므로 주문만 접수하고 카운터 결제로 안내한다
          ? `주문이 접수됐어요. 주문번호는 ${s.orderNumber}번이에요.\n카운터에서 주문번호를 말씀하시고 현금 ${won(total(s))}을 결제해 주세요.\n(시연용 모의 결제로 실제 결제는 이뤄지지 않았어요.)`
          : `결제가 완료됐어요. 주문번호는 ${s.orderNumber}번이에요.\n메뉴가 준비되면 번호로 불러 드릴게요.\n(시연용 모의 결제로 실제 결제는 이뤄지지 않았어요.)`,
      );
      ctx.choices.push({ label: "새 주문 시작", action: { type: "new_order" }, variant: "primary" });
      ctx.promptDone = true;
      return;
    case "new_order":
      Object.assign(s, initialOrderState());
      ctx.promptDone = true;
      pushStagePrompt(s, ctx);
      return;
    case "cancel_pending":
      ctx.texts.push("알겠어요.");
      return;
  }
}

/** 보류 중인 질문에 대한 답인지 판단한다. 답이면 멈춰 둔 액션을 이어서 처리한다. */
function resolvesPending(state: OrderState, action: EngineAction): boolean {
  const pending = state.pending;
  if (!pending) return false;
  if (action.type === "cancel_pending") return false;
  if (pending.kind === "temperature") return action.type === "add_item" && action.pending_resolution === true && action.menu_id === pending.action.menu_id && !!action.temperature;
  if (pending.kind === "quantity_confirm") return action.type === pending.action.type && "confirmed" in action && !!action.confirmed;
  if (pending.kind === "clear_confirm") return action.type === "clear_cart" && !!action.confirmed;
  return action.type === pending.action.type && "target" in action && !!action.target.line_id && pending.lineIds.includes(action.target.line_id);
}

export interface EngineResult {
  state: OrderState;
  reply: KioskReply;
}

/**
 * 액션을 순서대로 처리한다. 중간에 확인 질문이 생기면 거기서 멈추고
 * 나머지는 state.queued에 보관했다가 답을 받은 뒤 이어서 처리한다.
 */
export function runActions(prev: OrderState, actions: EngineAction[], opts: EngineOptions = {}): EngineResult {
  if (actions.length === 1 && actions[0].type === "update_item" && actions[0].target.pending) {
    const action = actions[0];
    if (prev.pending?.kind !== "temperature") {
      return { state: prev, reply: { text: "수정할 주문 초안이 없어요.", choices: [], menuIds: [] } };
    }
    const draft = prev.pending.action;
    const options = action.replace_options ? { ...(action.set_options ?? {}) } : { ...draft.options, ...action.set_options };
    for (const [id, count] of Object.entries(options)) if (count <= 0) delete options[id];
    const revised: AddAction = {
      ...draft,
      quantity: action.quantity ?? draft.quantity,
      options,
      temperature: action.temperature ?? draft.temperature,
    };
    const validation = validateItem({ menuId: revised.menu_id, temperature: revised.temperature, quantity: revised.quantity, options });
    const issues = validation.ok ? [] : validation.issues.filter((issue) => issue.code !== "temperature_required");
    if (issues.length) {
      return { state: prev, reply: { text: issues.map((issue) => issue.message).join("\n"), choices: stagePrompt(prev).choices, menuIds: [] } };
    }
    const state = structuredClone(prev);
    if (state.pending?.kind !== "temperature") throw new Error("pending changed during draft update");
    state.pending.action = revised;
    const changed = action.quantity !== undefined ? `수량을 ${revised.quantity}${unitWord(revised.menu_id)}으로 바꿨어요.`
      : action.set_options ? `주문 초안의 옵션을 ${Object.keys(options).length ? optionLabels(options).join(", ") : "없음"}으로 바꿨어요.`
      : "주문 초안을 수정했어요.";
    return { state, reply: { text: changed, choices: stagePrompt(state).choices, menuIds: [] } };
  }
  if (prev.pending && actions.length && !resolvesPending(prev, actions[0]) && actions[0].type !== "cancel_pending") {
    const immediate = actions.every((action) =>
      ["add_item", "update_item", "remove_item", "replace_menu", "set_dine_option", "show_cart"].includes(action.type),
    );
    if (immediate) {
      // 새 요청은 먼저 시도하되 기존 질문과 대기 액션은 별도로 보존한다.
      const unpaused = { ...structuredClone(prev), pending: null, queued: [] };
      const attempted = runActions(unpaused, actions, { ...opts, suppressStagePrompt: true });
      if (!attempted.state.pending) {
        const state = { ...attempted.state, pending: structuredClone(prev.pending), queued: structuredClone(prev.queued) };
        const prompt = stagePrompt(state);
        return {
          state,
          reply: {
            text: [attempted.reply.text, prompt.text].filter(Boolean).join("\n"),
            choices: dedupeChoices([...attempted.reply.choices, ...prompt.choices]),
            menuIds: attempted.reply.menuIds,
          },
        };
      }
      if (prev.pending.kind === "temperature" && !prev.pending.replaceLineId) {
        // 새 요청에도 확인이 필요하면 그 확인을 먼저 마치고 원래 온도를 다시 묻는다.
        attempted.state.queued = [
          ...attempted.state.queued,
          structuredClone(prev.pending.action),
          ...structuredClone(prev.queued),
        ];
        return attempted;
      }
    }
    // 교체 중 온도 확인처럼 재구성할 수 없는 보류 요청은 액션을 보관한다.
    const state = structuredClone(prev);
    state.queued = [...actions.filter((action): action is CartAction => action.type !== "cancel_pending"), ...state.queued];
    return { state, reply: stagePrompt(state) };
  }
  const s: OrderState = structuredClone(prev);
  const ctx: Ctx = {
    texts: [],
    choices: [],
    menuIds: [],
    changed: false,
    promptDone: false,
    stageBefore: prev.stage,
    opts,
  };

  let queue: EngineAction[] = [...actions];
  if (s.pending && queue.length) {
    const resolution = resolvesPending(s, queue[0]);
    const pendingReplace = s.pending.kind === "temperature" ? s.pending.replaceLineId : undefined;
    if (resolution) queue = [queue[0], ...s.queued, ...queue.slice(1)];
    s.pending = null;
    s.queued = [];
    if (resolution && pendingReplace && queue[0].type === "add_item") {
      const [first, ...rest] = queue;
      handleAdd(s, ctx, first as AddAction, pendingReplace);
      queue = rest;
    }
  }

  while (queue.length && !s.pending) {
    const action = queue.shift()!;
    handle(s, ctx, action, opts);
  }
  if (s.pending) s.queued = queue as CartAction[];

  if (!s.pending && !ctx.promptDone && !opts.suppressStagePrompt) {
    if (ctx.changed) {
      if (s.lines.length === 0) {
        // 공통 규칙 5: 장바구니를 모두 비우면 인사로
        s.stage = "greeting";
        s.dessertSuggested = false;
        s.coffeeHintShown = false;
        pushStagePrompt(s, ctx);
      } else if (POST_CONFIRM_STAGES.has(ctx.stageBefore)) {
        // HTI 7.1.2: 확정 직전 변경 → 바뀐 요약으로 다시 확인
        s.stage = s.dineOption ? "final_confirm" : "dine_option";
        pushStagePrompt(s, ctx);
      } else {
        s.stage = "more";
        maybeSuggestDessert(s, ctx);
        pushStagePrompt(s, ctx);
      }
    } else if (ctx.texts.length === 0 || actions.some((a) => a.type === "cancel_pending")) {
      pushStagePrompt(s, ctx);
    }
  }

  return {
    state: s,
    reply: {
      text: ctx.texts.join("\n"),
      choices: dedupeChoices(ctx.choices),
      menuIds: [...new Set(ctx.menuIds)],
    },
  };
}

/** 단계 5: 음료만 담겨 있으면 어울리는 디저트를 주문당 한 번만 제안 (pairings.csv) */
function maybeSuggestDessert(s: OrderState, ctx: Ctx) {
  if (s.dessertSuggested) return;
  const menus = s.lines.map((line) => getMenu(line.menuId)).filter((menu) => menu !== undefined);
  if (menus.length === 0 || !menus.every(isDrink)) return;
  const pairing = dessertPairingFor(menus.map((menu) => menu.id).reverse());
  if (!pairing) return;
  s.dessertSuggested = true;
  const names = pairing.dessertMenuIds.map((id) => getMenu(id)?.name).filter(Boolean);
  ctx.texts.push(`함께 드시기 좋은 디저트로 ${names.join(", ")}도 있어요.`);
  ctx.menuIds.push(...pairing.dessertMenuIds);
}

function dedupeChoices(choices: Choice[]): Choice[] {
  const seen = new Set<string>();
  return choices.filter((choice) => {
    const key = `${choice.label}|${JSON.stringify(choice.action)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function cartSnapshot(state: OrderState) {
  const cart = priceCart(state.lines);
  return {
    stage: state.stage,
    dine_option: state.dineOption,
    payment_method: state.paymentMethod,
    total_krw: cart.total,
    item_count: cart.itemCount,
    lines: cart.lines.map((line) => ({
      line_id: line.lineId,
      menu_id: line.menuId,
      variant_id: line.variantId,
      menu_name: line.menuName,
      temperature: line.temperature,
      options: line.options,
      option_labels: line.optionLabels,
      quantity: line.quantity,
      unit_price_krw: line.unitPrice,
      line_total_krw: line.lineTotal,
    })),
    pending_question: state.pending?.question ?? null,
  };
}

