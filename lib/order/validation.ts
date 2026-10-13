import { findVariant, getMenu, getOption, MENUS } from "./catalog";
import { josa } from "./format";
import type { ItemDraft, Menu, MenuVariant, OptionSelection, Temperature, ValidationIssue } from "./types";

/** 한 메뉴 10잔 이상이면 확인 질문 (HTI 6.1.3, 팀 응답 규칙) */
export const QUANTITY_CONFIRM_THRESHOLD = 10;
/** 한 항목 최대 수량. 입력 실수 방지를 위한 팀 규칙 */
export const MAX_LINE_QUANTITY = 999;

/**
 * 동시에 고를 수 없는 옵션 묶음 (팀 규칙).
 * - 샷 추가와 연하게(샷 1개 줄임)는 서로 반대 동작
 * - 우유는 한 종류만 바꿀 수 있음
 */
const CONFLICT_GROUPS: string[][] = [
  ["OPT-001", "OPT-002"],
  ["OPT-005", "OPT-006"],
];

export interface ValidItem {
  menu: Menu;
  variant: MenuVariant;
  temperature: Temperature | null;
  options: OptionSelection;
  quantity: number;
}

export type ValidationResult =
  | { ok: true; item: ValidItem }
  | { ok: false; issues: ValidationIssue[]; menu?: Menu };

function cleanOptions(options: OptionSelection | undefined): OptionSelection {
  const cleaned: OptionSelection = {};
  for (const [optionId, count] of Object.entries(options ?? {})) {
    if (Number.isFinite(count) && count > 0) cleaned[optionId] = Math.trunc(count);
  }
  return cleaned;
}

/** 옵션이 가능한 다른 메뉴를 같은 분류부터 최대 3개 제안한다. */
function alternativeMenusForOption(optionId: string, menu: Menu): string[] {
  const option = getOption(optionId);
  if (!option) return [];
  const candidates = option.menuIds.filter((id) => id !== menu.id);
  const sameCategory = candidates.filter((id) => MENUS.find((m) => m.id === id)?.category === menu.category);
  const others = candidates.filter((id) => !sameCategory.includes(id));
  return [...sameCategory, ...others].slice(0, 3);
}

function notApplicableMessage(menu: Menu, optionName: string, optionId: string): string {
  if (optionId === "OPT-005" || optionId === "OPT-006") {
    return `${josa(menu.name, "은/는")} 우유가 들어간 메뉴로 확인되지 않아 ${josa(optionName, "을/를")} 할 수 없어요.`;
  }
  if (optionId === "OPT-009") {
    return `${josa(menu.name, "은/는")} 디카페인으로 바꿀 수 없어요.`;
  }
  return `${menu.name}에는 ${optionName} 옵션을 적용할 수 없어요.`;
}

/**
 * 메뉴 + 온도 + 옵션 + 수량을 검사한다. 금액 계산 전에 반드시 통과해야 한다.
 * 온도가 빠진 경우는 오류가 아니라 '물어볼 정보'이므로 temperature_required 하나만 돌려준다.
 */
export function validateItem(draft: ItemDraft): ValidationResult {
  const menu = getMenu(draft.menuId);
  if (!menu) {
    return { ok: false, issues: [{ code: "unknown_menu", message: "판매하지 않는 메뉴예요." }] };
  }

  const issues: ValidationIssue[] = [];

  // 온도: ICE만 있는 메뉴는 자동 결정, 디저트는 온도 없음
  let temperature: Temperature | null = null;
  if (menu.temperatures.length === 1) {
    temperature = menu.temperatures[0];
    if (draft.temperature && draft.temperature !== temperature) {
      issues.push({
        code: "temperature_unavailable",
        message: `${josa(menu.name, "은/는")} ${temperature === "ICE" ? "아이스" : "따뜻한 것"}만 가능해요.`,
        availableTemperatures: menu.temperatures,
      });
    }
  } else if (menu.temperatures.length > 1) {
    temperature = draft.temperature ?? null;
    if (!temperature) {
      issues.push({
        code: "temperature_required",
        message: `${josa(menu.name, "은/는")} 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?`,
        availableTemperatures: menu.temperatures,
      });
    }
  }

  // 수량
  const quantity = draft.quantity ?? 1;
  if (!Number.isInteger(quantity) || quantity < 1) {
    issues.push({ code: "invalid_quantity", message: "수량은 1개 이상으로 말씀해 주세요." });
  } else if (quantity > MAX_LINE_QUANTITY) {
    issues.push({
      code: "quantity_exceeds_limit",
      message: `한 메뉴는 최대 ${MAX_LINE_QUANTITY}개까지 담을 수 있어요.`,
    });
  }

  // 옵션
  const options = cleanOptions(draft.options);
  const fixedOptions: OptionSelection = { ...options };
  for (const [optionId, count] of Object.entries(options)) {
    const option = getOption(optionId);
    if (!option) {
      issues.push({ code: "unknown_option", optionId, message: "제공하지 않는 옵션이에요." });
      delete fixedOptions[optionId];
      continue;
    }
    if (!option.menuIds.includes(menu.id)) {
      issues.push({
        code: "option_not_applicable",
        optionId,
        message: notApplicableMessage(menu, option.name, optionId),
        alternativeMenuIds: alternativeMenusForOption(optionId, menu),
      });
      delete fixedOptions[optionId];
      continue;
    }
    if (count > option.maxQuantity) {
      issues.push({
        code: "option_quantity_exceeded",
        optionId,
        message: `${josa(option.name, "은/는")} 최대 ${option.maxQuantity}${option.id === "OPT-001" ? "개" : "회"}까지 가능해요.`,
      });
      fixedOptions[optionId] = option.maxQuantity;
    }
  }

  for (const group of CONFLICT_GROUPS) {
    const selected = group.filter((optionId) => options[optionId]);
    if (selected.length > 1) {
      const names = selected.map((optionId) => getOption(optionId)?.name ?? optionId);
      issues.push({
        code: "option_conflict",
        optionId: selected[selected.length - 1],
        message: `${josa(names[0], "과/와")} ${josa(names[1], "은/는")} 함께 고를 수 없어요. 하나만 골라 주세요.`,
      });
    }
  }

  // 디카페인은 변형(온도)별 decaf_available 값도 확인한다
  const variant = findVariant(menu, temperature);
  if (options["OPT-009"] && variant && variant.decafAvailable !== "Y") {
    if (!issues.some((issue) => issue.optionId === "OPT-009")) {
      issues.push({
        code: "option_not_applicable",
        optionId: "OPT-009",
        message: `${josa(menu.name, "은/는")} 디카페인으로 바꿀 수 없어요.`,
        alternativeMenuIds: alternativeMenusForOption("OPT-009", menu),
      });
    }
  }

  if (issues.length > 0) {
    const fixable = issues.every((issue) => issue.code === "option_quantity_exceeded");
    if (fixable) {
      for (const issue of issues) {
        issue.fixedDraft = { ...draft, options: fixedOptions };
      }
    }
    return { ok: false, issues, menu };
  }

  if (!variant) {
    return {
      ok: false,
      menu,
      issues: [
        {
          code: "temperature_unavailable",
          message: `${josa(menu.name, "은/는")} 선택한 온도로 주문할 수 없어요.`,
          availableTemperatures: menu.temperatures,
        },
      ],
    };
  }

  return { ok: true, item: { menu, variant, temperature, options, quantity } };
}
