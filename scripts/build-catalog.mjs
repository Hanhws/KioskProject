// data/megacoffee_rag_enriched CSV를 프론트엔드 주문 모듈용 JSON으로 변환한다.
// 옵션 적용 대상 계산은 rag/build_index.py의 option_relation()과 같은 규칙을 따른다.
// (RAG 답변과 장바구니 검증 결과가 서로 어긋나지 않도록 하기 위함)

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dataDir = process.env.MEGACOFFEE_DATA_DIR
  ? join(root, process.env.MEGACOFFEE_DATA_DIR)
  : join(root, "data", "megacoffee_rag_enriched");
const outPath = join(root, "lib", "generated", "catalog.json");

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  text = text.replace(/^﻿/, "");
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') {
        quoted = false;
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      row.push(cell);
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  row.push(cell);
  if (row.some((value) => value !== "")) rows.push(row);
  const [header, ...body] = rows;
  return body.map((values) =>
    Object.fromEntries(header.map((key, index) => [key, (values[index] ?? "").trim() || null])),
  );
}

const readTable = (name) => parseCsv(readFileSync(join(dataDir, `${name}.csv`), "utf8"));
const pipe = (value) => String(value ?? "").split("|").map((part) => part.trim()).filter(Boolean);
const toInt = (value) => (value == null ? null : Math.trunc(Number(value)));

const catalog = readTable("menu_catalog");
const variants = readTable("menu_variants");
const options = readTable("options");
const pairings = readTable("pairings");
const unavailable = readTable("unavailable_menu_alternatives");

const menuIdByName = new Map(catalog.map((menu) => [menu.menu_name, menu.menu_group_id]));

// build_index.resolve_category_or_menu_terms 와 동일: 메뉴명 정확 일치 → category 일치 → 미해결
function resolveCategoryOrMenuTerms(terms) {
  const ids = new Set();
  const unresolved = [];
  for (const term of terms) {
    if (!term || term === "없음") continue;
    if (menuIdByName.has(term)) {
      ids.add(menuIdByName.get(term));
      continue;
    }
    const categoryIds = catalog.filter((menu) => menu.category === term).map((menu) => menu.menu_group_id);
    if (categoryIds.length) categoryIds.forEach((id) => ids.add(id));
    else unresolved.push(term);
  }
  return { ids, unresolved };
}

// build_index.option_relation 과 동일
function optionMenuIds(option) {
  const appliesTo = option.applies_to ?? "";
  let included = new Set();
  if (appliesTo === "우유가 들어가는 음료") {
    const names = new Set(variants.filter((v) => pipe(v.allergens).includes("우유")).map((v) => v.base_menu_name));
    included = new Set(catalog.filter((m) => names.has(m.menu_name) && m.type === "음료").map((m) => m.menu_group_id));
  } else if (appliesTo === "decaf_available=Y인 커피") {
    const names = new Set(variants.filter((v) => v.decaf_available === "Y").map((v) => v.base_menu_name));
    included = new Set(catalog.filter((m) => names.has(m.menu_name) && m.category === "커피").map((m) => m.menu_group_id));
  } else {
    included = resolveCategoryOrMenuTerms(pipe(appliesTo)).ids;
  }
  const excluded = resolveCategoryOrMenuTerms(pipe(option.excluded_menus)).ids;
  return [...included].filter((id) => !excluded.has(id)).sort();
}

const menus = catalog.map((menu) => ({
  id: menu.menu_group_id,
  type: menu.type,
  category: menu.category,
  name: menu.menu_name,
  aliases: pipe(menu.aliases),
  temperatures: pipe(menu.available_temperatures).filter((t) => t === "HOT" || t === "ICE"),
  description: menu.short_description,
  flavorTags: pipe(menu.flavor_tags),
  sweetness: menu.sweetness_level,
  coffeeStrength: menu.coffee_strength,
  variants: variants
    .filter((variant) => variant.base_menu_name === menu.menu_name)
    .map((variant) => ({
      id: variant.variant_id,
      displayName: variant.display_name,
      temperature: variant.temperature === "HOT" || variant.temperature === "ICE" ? variant.temperature : null,
      price: toInt(variant.price_krw),
      volumeMl: toInt(variant.volume_ml),
      caffeineMg: toInt(variant.caffeine_mg),
      allergens: variant.allergens,
      decafAvailable: variant.decaf_available,
      imageUrl: variant.image_url,
    })),
}));

const missing = menus.filter((menu) => menu.variants.length === 0).map((menu) => menu.name);
if (missing.length) throw new Error(`variant가 없는 메뉴: ${missing.join(", ")}`);

const output = {
  generatedFrom: "data/megacoffee_rag_enriched",
  menus,
  options: options.map((option) => ({
    id: option.option_id,
    group: option.option_group,
    name: option.option_name,
    appliesTo: option.applies_to,
    excludedMenus: option.excluded_menus,
    maxQuantity: toInt(option.max_quantity),
    priceDelta: toInt(option.price_delta_krw),
    rule: option.selection_rule,
    menuIds: optionMenuIds(option),
  })),
  pairings: pairings.map((pairing) => ({
    id: pairing.pairing_id,
    beverageMenuIds: [...resolveCategoryOrMenuTerms(pipe(pairing.beverage_menu_names)).ids],
    dessertMenuIds: [...resolveCategoryOrMenuTerms(pipe(pairing.dessert_menu_names)).ids],
    reason: pairing.pairing_reason,
  })),
  unavailable: unavailable.map((row) => ({
    requested: row.requested_menu,
    alternativeMenuIds: [...resolveCategoryOrMenuTerms(pipe(row.alternatives)).ids],
    reason: row.reason,
  })),
};

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, `${JSON.stringify(output, null, 2)}\n`);
console.log(`catalog.json: 메뉴 ${menus.length}개, 옵션 ${options.length}개, 페어링 ${pairings.length}개`);
