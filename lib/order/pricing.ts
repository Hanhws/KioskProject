import { getMenu, getOption, OPTIONS } from "./catalog";
import { temperatureLabel } from "./format";
import type { CartLine, OptionSelection, PricedLine } from "./types";

/** 옵션 표시 순서는 options.csv 순서를 따른다. */
export function optionLabels(options: OptionSelection): string[] {
  return OPTIONS.filter((option) => options[option.id]).map((option) => {
    const count = options[option.id];
    return count > 1 ? `${option.name} ×${count}` : option.name;
  });
}

export function optionsPrice(options: OptionSelection): number {
  return Object.entries(options).reduce((sum, [optionId, count]) => {
    const option = getOption(optionId);
    return sum + (option ? option.priceDelta * count : 0);
  }, 0);
}

/** 단가 = 온도별 기본가(menu_variants.price_krw) + Σ(옵션 추가금 × 횟수), 금액 = 단가 × 수량 */
export function priceLine(line: CartLine): PricedLine {
  const menu = getMenu(line.menuId);
  const variant = menu?.variants.find((row) => row.id === line.variantId);
  if (!menu || !variant) throw new Error(`장바구니 항목의 메뉴를 찾을 수 없습니다: ${line.menuId}`);
  const extra = optionsPrice(line.options);
  const unitPrice = variant.price + extra;
  return {
    ...line,
    menuName: menu.name,
    basePrice: variant.price,
    optionsPrice: extra,
    unitPrice,
    lineTotal: unitPrice * line.quantity,
    optionLabels: optionLabels(line.options),
  };
}

export function priceCart(lines: CartLine[]) {
  const priced = lines.map(priceLine);
  return {
    lines: priced,
    itemCount: priced.reduce((sum, line) => sum + line.quantity, 0),
    total: priced.reduce((sum, line) => sum + line.lineTotal, 0),
  };
}

/** 'ICE 카페라떼 (두유 변경)' 처럼 항목을 한 줄로 부른다. */
export function lineTitle(line: CartLine, withOptions = true): string {
  const menu = getMenu(line.menuId);
  const temp = line.temperature ? `${line.temperature} ` : "";
  const labels = withOptions ? optionLabels(line.options) : [];
  return `${temp}${menu?.name ?? line.menuId}${labels.length ? ` (${labels.join(", ")})` : ""}`;
}

export function lineSpoken(line: CartLine): string {
  const menu = getMenu(line.menuId);
  const temp = temperatureLabel(line.temperature);
  return `${temp ? `${temp} ` : ""}${menu?.name ?? line.menuId}`;
}

export function unitWord(menuId: string): string {
  return getMenu(menuId)?.type === "음료" ? "잔" : "개";
}
