"""CSV 로딩, 타입 변환, 메뉴 연결 및 연결 진단."""

from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv


def find_env_file() -> Optional[Path]:
    for base in [Path.cwd(), *Path.cwd().parents]:
        candidate = base / ".env"
        if candidate.is_file():
            return candidate
    return None


ENV_FILE = find_env_file()
if ENV_FILE:
    load_dotenv(ENV_FILE, override=False)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def resolve_data_dir(explicit: Optional[str] = None) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
        raise FileNotFoundError(f"데이터 폴더를 찾을 수 없습니다: {candidate}")
    for start in [Path.cwd(), Path.cwd().parent]:
        for base in [start, *start.parents]:
            candidate = base / "megacoffee_rag_enriched"
            if candidate.is_dir():
                return candidate.resolve()
    raise FileNotFoundError(
        "megacoffee_rag_enriched를 찾지 못했습니다. "
        "MEGACOFFEE_DATA_DIR 환경변수로 폴더를 지정하세요."
    )


@dataclass(frozen=True)
class RagConfig:
    data_dir: Path
    index_dir: Path
    embedding_json_path: Path
    embedding_model: str = "text-embedding-3-small"
    answer_model: str = "gpt-4.1-mini"
    top_k: int = 6
    fetch_k: int = 20
    embedding_batch_size: int = 64
    embedding_max_input_tokens: int = 8192
    embedding_max_request_tokens: int = 300_000
    rebuild_index: bool = False
    run_llm: bool = True
    run_live_tests: bool = False
    interactive: bool = True


def load_config() -> RagConfig:
    project_root = resolve_data_dir(os.getenv("MEGACOFFEE_DATA_DIR")).parent
    return RagConfig(
        data_dir=project_root / "megacoffee_rag_enriched",
        index_dir=Path(
            os.getenv("RAG_INDEX_DIR", str(project_root / "rag_index" / "faiss_openai_v1"))
        ).expanduser().resolve(),
        embedding_json_path=Path(
            os.getenv("RAG_EMBEDDING_JSON_PATH", str(project_root / "rag_embeddings.json"))
        ).expanduser().resolve(),
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small").strip(),
        answer_model=os.getenv("RAG_ANSWER_MODEL", "gpt-4.1-mini").strip(),
        top_k=int(os.getenv("RAG_TOP_K", "6")),
        fetch_k=int(os.getenv("RAG_FETCH_K", "20")),
        rebuild_index=env_flag("RAG_REBUILD_INDEX", False),
        run_llm=env_flag("RAG_RUN_LLM", True),
        run_live_tests=env_flag("RAG_RUN_LIVE_TESTS", False),
        interactive=env_flag("RAG_INTERACTIVE", True),
    )


REQUIRED_FILES = {
    "menu_catalog": "menu_catalog.csv",
    "menu_variants": "menu_variants.csv",
    "options": "options.csv",
    "faq_policy": "faq_policy.csv",
    "pairings": "pairings.csv",
    "unavailable_menu_alternatives": "unavailable_menu_alternatives.csv",
    "rag_documents": "rag_documents.csv",
}

INT_COLUMNS = {
    "menu_variants": {
        "price_krw", "volume_ml", "calories_kcal", "caffeine_mg",
        "sodium_mg", "cholesterol_mg",
    },
    "options": {"max_quantity", "price_delta_krw"},
}
FLOAT_COLUMNS = {
    "menu_variants": {
        "sugar_g", "protein_g", "saturated_fat_g", "carbohydrate_g",
        "fat_g", "trans_fat_g",
    },
}


def clean_cell(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def parse_number(value: Optional[str], number_type: type) -> Optional[int | float]:
    value = clean_cell(value)
    if value is None:
        return None
    return int(float(value)) if number_type is int else float(value)


def read_csv_records(path: Path, table_name: str) -> tuple[list[str], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = list(reader.fieldnames or [])
        rows = []
        for raw in reader:
            row: dict[str, Any] = {}
            for column in columns:
                value: Any = clean_cell(raw.get(column))
                if column in INT_COLUMNS.get(table_name, set()):
                    value = parse_number(value, int)
                elif column in FLOAT_COLUMNS.get(table_name, set()):
                    value = parse_number(value, float)
                row[column] = value
            rows.append(row)
    return columns, rows


def normalize_text(text: Any) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def pipe_values(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


@dataclass
class RagData:
    tables: dict[str, list[dict[str, Any]]]
    columns: dict[str, list[str]]
    catalog_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    variant_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    variants_by_menu_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    alias_to_menu_ids: dict[str, list[str]] = field(default_factory=dict)
    document_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    option_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    policy_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_rag_data(data_dir: Path) -> RagData:
    missing = [filename for filename in REQUIRED_FILES.values() if not (data_dir / filename).is_file()]
    if missing:
        raise FileNotFoundError(f"필수 CSV가 없습니다: {missing}")

    tables: dict[str, list[dict[str, Any]]] = {}
    columns: dict[str, list[str]] = {}
    for table_name, filename in REQUIRED_FILES.items():
        columns[table_name], tables[table_name] = read_csv_records(data_dir / filename, table_name)

    data = RagData(tables=tables, columns=columns)
    data.catalog_by_id = {row["menu_group_id"]: row for row in tables["menu_catalog"]}
    data.variant_by_id = {row["variant_id"]: row for row in tables["menu_variants"]}
    data.document_by_id = {row["document_id"]: row for row in tables["rag_documents"]}
    data.option_by_id = {row["option_id"]: row for row in tables["options"]}
    data.policy_by_id = {row["policy_id"]: row for row in tables["faq_policy"]}

    for variant in tables["menu_variants"]:
        data.variants_by_menu_name.setdefault(variant["base_menu_name"], []).append(variant)

    alias_map: dict[str, set[str]] = {}
    for menu in tables["menu_catalog"]:
        for alias in [menu["menu_name"], *pipe_values(menu.get("aliases"))]:
            key = normalize_text(alias)
            if key:
                alias_map.setdefault(key, set()).add(menu["menu_group_id"])
    data.alias_to_menu_ids = {key: sorted(ids) for key, ids in alias_map.items()}
    return data


def duplicate_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    seen, duplicates = set(), set()
    for row in rows:
        value = row.get(key)
        if value in seen and value is not None:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def relation_diagnostics(data: RagData) -> dict[str, Any]:
    catalog_names = {row["menu_name"] for row in data.tables["menu_catalog"]}
    variant_names = {row["base_menu_name"] for row in data.tables["menu_variants"]}
    expected_details = {f"DETAIL-{variant_id}" for variant_id in data.variant_by_id}
    actual_details = {
        row["document_id"] for row in data.tables["rag_documents"]
        if row["document_type"] == "menu_detail"
    }
    pairing_terms = {
        term
        for row in data.tables["pairings"]
        for term in pipe_values(row.get("beverage_menu_names")) + pipe_values(row.get("dessert_menu_names"))
    }
    return {
        "catalog_duplicate_ids": duplicate_values(data.tables["menu_catalog"], "menu_group_id"),
        "variant_duplicate_ids": duplicate_values(data.tables["menu_variants"], "variant_id"),
        "document_duplicate_ids": duplicate_values(data.tables["rag_documents"], "document_id"),
        "catalog_without_variant": sorted(catalog_names - variant_names),
        "variant_without_catalog": sorted(variant_names - catalog_names),
        "missing_detail_documents": sorted(expected_details - actual_details),
        "unexpected_detail_documents": sorted(actual_details - expected_details),
        "pairing_terms_not_exact_menu_names": sorted(pairing_terms - catalog_names),
        "planning_data_conflicts": [
            "기획 PDF의 사이즈 예시와 달리 faq_policy.csv 및 menu_variants.csv는 사이즈 선택 없음으로 정의합니다. CSV를 따릅니다."
        ],
    }
