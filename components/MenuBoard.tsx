"use client";

import { useRef, useState } from "react";
import { menusByCategory } from "@/lib/order/catalog";
import MenuCard from "./MenuCard";

const GROUPS = menusByCategory();

/** 빠른 주문용 메뉴판 (HTI 1.2, 2.7). 분류 탭을 누르면 해당 분류로 이동한다. */
export default function MenuBoard({
  onOpen,
  onAdd,
}: {
  onOpen: (menuId: string) => void;
  onAdd: (menuId: string) => void;
}) {
  const [active, setActive] = useState(GROUPS[0]?.category);
  const sections = useRef<Record<string, HTMLElement | null>>({});

  return (
    <div className="board">
      <nav className="board-tabs" aria-label="메뉴 분류">
        {GROUPS.map(({ category }) => (
          <button
            key={category}
            type="button"
            className="board-tab"
            aria-pressed={active === category}
            onClick={() => {
              setActive(category);
              sections.current[category]?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
          >
            {category}
          </button>
        ))}
      </nav>
      {GROUPS.map(({ category, menus }) => (
        <section key={category} ref={(el) => { sections.current[category] = el; }} style={{ scrollMarginTop: 56 }}>
          <h2>{category}</h2>
          <div className="board-grid">
            {menus.map((menu) => (
              <MenuCard key={menu.id} menuId={menu.id} onOpen={onOpen} onAdd={onAdd} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
