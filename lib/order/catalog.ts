import raw from "../generated/catalog.json";
import type { Menu, MenuOption, MenuVariant, Pairing, Temperature } from "./types";

const data = raw as unknown as {
  menus: Menu[];
  options: MenuOption[];
  pairings: Pairing[];
  unavailable: { requested: string; alternativeMenuIds: string[]; reason: string }[];
};

export const MENUS: Menu[] = data.menus;
export const OPTIONS: MenuOption[] = data.options;
export const PAIRINGS: Pairing[] = data.pairings;

const menuById = new Map(MENUS.map((menu) => [menu.id, menu]));
const optionById = new Map(OPTIONS.map((option) => [option.id, option]));

export function getMenu(menuId: string): Menu | undefined {
  return menuById.get(menuId);
}

export function getOption(optionId: string): MenuOption | undefined {
  return optionById.get(optionId);
}

export function isDrink(menu: Menu): boolean {
  return menu.type === "음료";
}

export function isCoffee(menu: Menu): boolean {
  return menu.category === "커피" || menu.category === "콜드브루";
}

/** 디저트처럼 온도가 없는 메뉴는 temperature=null 변형 하나를 가진다. */
export function findVariant(menu: Menu, temperature: Temperature | null): MenuVariant | undefined {
  if (menu.temperatures.length === 0) return menu.variants[0];
  return menu.variants.find((variant) => variant.temperature === temperature);
}

export function optionsForMenu(menuId: string): MenuOption[] {
  return OPTIONS.filter((option) => option.menuIds.includes(menuId));
}

/** 메뉴 화면의 분류 순서. HTI 2.7의 안내 순서를 따른다. */
export const CATEGORY_ORDER = [
  "커피",
  "콜드브루",
  "논커피 라떼",
  "티",
  "에이드·주스",
  "스무디·프라페",
  "케이크",
  "구움과자",
  "빵류",
  "샌드위치·브런치",
  "마카롱",
];

export function menusByCategory(): { category: string; menus: Menu[] }[] {
  const categories = [...new Set([...CATEGORY_ORDER, ...MENUS.map((menu) => menu.category)])];
  return categories
    .map((category) => ({ category, menus: MENUS.filter((menu) => menu.category === category) }))
    .filter((group) => group.menus.length > 0);
}

/** 음료에 어울리는 디저트 (pairings.csv). 담긴 음료 순서대로 찾는다. */
export function dessertPairingFor(menuIds: string[]): { dessertMenuIds: string[]; reason: string } | null {
  for (const menuId of menuIds) {
    const pairing = PAIRINGS.find((row) => row.beverageMenuIds.includes(menuId));
    if (pairing) return { dessertMenuIds: pairing.dessertMenuIds.slice(0, 3), reason: pairing.reason };
  }
  return null;
}

export function lowestPrice(menu: Menu): number {
  return Math.min(...menu.variants.map((variant) => variant.price));
}

export function primaryImage(menu: Menu): string | null {
  const ice = menu.variants.find((variant) => variant.temperature === "ICE");
  return (ice ?? menu.variants[0])?.imageUrl ?? null;
}
