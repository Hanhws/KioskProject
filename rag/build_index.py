"""RAG 문서 구성, 입력 길이 검사, OpenAI 임베딩 및 FAISS 저장/재사용."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from typing import Any, Iterable, Optional

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from .data_loader import REQUIRED_FILES, RagConfig, RagData, normalize_text, pipe_values


def data_fingerprint(data_dir) -> str:
    digest = hashlib.sha256()
    for filename in sorted(REQUIRED_FILES.values()):
        path = data_dir / filename
        digest.update(filename.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def exact_menu_id(data: RagData, menu_name: str) -> Optional[str]:
    menu = next((row for row in data.tables["menu_catalog"] if row["menu_name"] == menu_name), None)
    return menu["menu_group_id"] if menu else None


def resolve_category_or_menu_terms(
    terms: Iterable[str], data: RagData
) -> tuple[set[str], list[str]]:
    menu_ids: set[str] = set()
    unresolved: list[str] = []
    for term in terms:
        term = str(term or "").strip()
        if not term or term == "없음":
            continue
        menu_id = exact_menu_id(data, term)
        if menu_id:
            menu_ids.add(menu_id)
            continue
        category_ids = {
            menu["menu_group_id"] for menu in data.tables["menu_catalog"]
            if menu["category"] == term
        }
        if category_ids:
            menu_ids.update(category_ids)
        else:
            unresolved.append(term)
    return menu_ids, unresolved


def resolve_exact_menu_terms(
    terms: Iterable[str], data: RagData
) -> tuple[list[str], list[str]]:
    menu_ids, unresolved = set(), []
    for term in terms:
        term = str(term or "").strip()
        if not term:
            continue
        menu_id = exact_menu_id(data, term)
        if menu_id:
            menu_ids.add(menu_id)
        else:
            unresolved.append(term)
    return sorted(menu_ids), sorted(set(unresolved))


def option_relation(option: dict[str, Any], data: RagData) -> tuple[list[str], list[str]]:
    applies_to = option.get("applies_to") or ""
    unresolved: list[str] = []
    if applies_to == "우유가 들어가는 음료":
        confirmed_names = {
            variant["base_menu_name"] for variant in data.tables["menu_variants"]
            if "우유" in pipe_values(variant.get("allergens"))
        }
        included = {
            menu["menu_group_id"] for menu in data.tables["menu_catalog"]
            if menu["menu_name"] in confirmed_names and menu["type"] == "음료"
        }
    elif applies_to == "decaf_available=Y인 커피":
        confirmed_names = {
            variant["base_menu_name"] for variant in data.tables["menu_variants"]
            if variant.get("decaf_available") == "Y"
        }
        included = {
            menu["menu_group_id"] for menu in data.tables["menu_catalog"]
            if menu["menu_name"] in confirmed_names and menu["category"] == "커피"
        }
    elif applies_to == "기본 휘핑 포함 메뉴":
        included = set()
        unresolved.append(applies_to)
    else:
        included, unresolved_applies = resolve_category_or_menu_terms(pipe_values(applies_to), data)
        unresolved.extend(unresolved_applies)
    excluded, unresolved_excluded = resolve_category_or_menu_terms(
        pipe_values(option.get("excluded_menus")), data
    )
    unresolved.extend(unresolved_excluded)
    return sorted(included - excluded), sorted(set(unresolved))


def document_link_metadata(row: dict[str, Any], data: RagData) -> dict[str, Any]:
    document_id = row["document_id"]
    document_type = row["document_type"]
    result = {
        "menu_group_id": None,
        "variant_id": None,
        "temperature": None,
        "related_menu_ids": [],
        "unresolved_relation_terms": [],
    }
    if document_type == "menu" and document_id in data.catalog_by_id:
        result.update(menu_group_id=document_id, related_menu_ids=[document_id])
    elif document_type == "menu_detail" and document_id.startswith("DETAIL-"):
        variant_id = document_id.removeprefix("DETAIL-")
        variant = data.variant_by_id.get(variant_id)
        if variant:
            menu_id = exact_menu_id(data, variant["base_menu_name"])
            result.update(
                menu_group_id=menu_id,
                variant_id=variant_id,
                temperature=variant["temperature"],
                related_menu_ids=[menu_id] if menu_id else [],
            )
    elif document_type == "option" and document_id in data.option_by_id:
        menu_ids, unresolved = option_relation(data.option_by_id[document_id], data)
        result.update(related_menu_ids=menu_ids, unresolved_relation_terms=unresolved)
    elif document_type == "pairing":
        pairing = next(
            (item for item in data.tables["pairings"] if item["pairing_id"] == document_id),
            None,
        )
        if pairing:
            terms = pipe_values(pairing.get("beverage_menu_names")) + pipe_values(
                pairing.get("dessert_menu_names")
            )
            menu_ids, unresolved = resolve_exact_menu_terms(terms, data)
            result.update(related_menu_ids=menu_ids, unresolved_relation_terms=unresolved)
    elif document_type == "alternative":
        requested = re.sub(r"\s*대체 메뉴$", "", row["title"])
        alternative = next(
            (
                item for item in data.tables["unavailable_menu_alternatives"]
                if item["requested_menu"] == requested
            ),
            None,
        )
        if alternative:
            menu_ids, unresolved = resolve_exact_menu_terms(
                pipe_values(alternative.get("alternatives")), data
            )
            result.update(
                alternative_requested_menu=requested,
                related_menu_ids=menu_ids,
                unresolved_relation_terms=unresolved,
            )
    return result


def build_embedding_text(row: dict[str, Any]) -> str:
    keywords = ", ".join(pipe_values(row.get("search_keywords")))
    return (
        f"제목: {row.get('title') or ''}\n"
        f"검색 표현: {keywords}\n"
        f"내용: {row.get('content') or ''}"
    )


def build_langchain_documents(data: RagData) -> list[Document]:
    documents = []
    for row in data.tables["rag_documents"]:
        documents.append(
            Document(
                page_content=build_embedding_text(row),
                metadata={
                    "document_id": row["document_id"],
                    "document_type": row["document_type"],
                    "source_type": row["source_type"],
                    "title": row["title"],
                    "search_keywords": row.get("search_keywords"),
                    "original_content": row.get("content"),
                    **document_link_metadata(row, data),
                },
            )
        )
    return documents


def count_embedding_tokens(text: str, model: str) -> tuple[int, str]:
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text)), "tiktoken"
    except ImportError:
        return len(text.encode("utf-8")), "utf8_byte_upper_bound"


def validate_embedding_inputs(texts: list[str], config: RagConfig) -> dict[str, Any]:
    if not texts:
        raise ValueError("임베딩할 문서가 없습니다.")
    empty_indexes = [index for index, text in enumerate(texts) if not text.strip()]
    if empty_indexes:
        raise ValueError(f"빈 임베딩 입력 위치: {empty_indexes[:10]}")
    counted = [count_embedding_tokens(text, config.embedding_model) for text in texts]
    counts = [count for count, _ in counted]
    methods = sorted({method for _, method in counted})
    too_long = [
        (index, count) for index, count in enumerate(counts)
        if count > config.embedding_max_input_tokens
    ]
    if too_long:
        raise ValueError(
            f"입력별 {config.embedding_max_input_tokens}토큰 한도 초과: {too_long[:10]}"
        )
    batch_totals = []
    for start in range(0, len(counts), config.embedding_batch_size):
        total = sum(counts[start:start + config.embedding_batch_size])
        if total > config.embedding_max_request_tokens:
            raise ValueError(
                f"요청 전체 {config.embedding_max_request_tokens}토큰 한도 초과: "
                f"batch_start={start}, tokens={total}"
            )
        batch_totals.append(total)
    max_index = max(range(len(counts)), key=counts.__getitem__)
    return {
        "passed": True,
        "document_count": len(texts),
        "count_method": methods,
        "max_document_index": max_index,
        "max_document_tokens": counts[max_index],
        "max_batch_tokens": max(batch_totals),
        "max_input_tokens": config.embedding_max_input_tokens,
        "max_request_tokens": config.embedding_max_request_tokens,
    }


def index_signature(config: RagConfig, document_count: int) -> dict[str, Any]:
    return {
        "schema": "langchain-document-faiss-v1",
        "embedding_text_format": "labeled-title-keywords-content-v1",
        "embedding_model": config.embedding_model,
        "data_sha256": data_fingerprint(config.data_dir),
        "document_count": document_count,
    }


def save_embedding_json(
    documents: list[Document],
    vectors: list[list[float]],
    config: RagConfig,
    validation: dict[str, Any],
) -> dict[str, Any]:
    if len(documents) != len(vectors):
        raise ValueError("문서 수와 임베딩 벡터 수가 다릅니다.")
    vector_dimensions = {len(vector) for vector in vectors}
    if len(vector_dimensions) != 1:
        raise ValueError(f"임베딩 차원이 일정하지 않습니다: {sorted(vector_dimensions)}")

    records = []
    for document, vector in zip(documents, vectors):
        metadata = {
            key: value
            for key, value in document.metadata.items()
            if key not in {"document_id", "original_content"}
        }
        records.append({
            "document_id": document.metadata["document_id"],
            "text": document.page_content,
            "content": document.metadata.get("original_content"),
            "metadata": metadata,
            "embedding": vector,
        })

    payload = {
        "schema": "rag-embedding-export-v1",
        "embedding_model": config.embedding_model,
        "vector_dimension": next(iter(vector_dimensions)),
        "document_count": len(records),
        "source_file": "megacoffee_rag_enriched/rag_documents.csv",
        "source_sha256": hashlib.sha256(
            (config.data_dir / "rag_documents.csv").read_bytes()
        ).hexdigest(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_validation": validation,
        "documents": records,
    }
    config.embedding_json_path.parent.mkdir(parents=True, exist_ok=True)
    config.embedding_json_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "path": str(config.embedding_json_path),
        "document_count": len(records),
        "vector_dimension": payload["vector_dimension"],
        "sha256": hashlib.sha256(config.embedding_json_path.read_bytes()).hexdigest(),
        "bytes": config.embedding_json_path.stat().st_size,
    }


def build_or_load_vectorstore(
    documents: list[Document], config: RagConfig
) -> tuple[FAISS, dict[str, Any]]:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError(
            "OPENAI_API_KEY가 없어 실제 OpenAI 임베딩 인덱스를 만들거나 불러올 수 없습니다. "
            "키를 환경변수 또는 .env에 설정하세요."
        )

    config.index_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = config.index_dir / "manifest.json"
    faiss_path = config.index_dir / "index.faiss"
    pickle_path = config.index_dir / "index.pkl"
    embedding_json_path = config.embedding_json_path
    expected = index_signature(config, len(documents))
    embeddings = OpenAIEmbeddings(
        model=config.embedding_model,
        chunk_size=config.embedding_batch_size,
    )

    if not config.rebuild_index:
        if not (
            manifest_path.is_file()
            and faiss_path.is_file()
            and pickle_path.is_file()
            and embedding_json_path.is_file()
        ):
            raise FileNotFoundError(
                f"저장된 FAISS 인덱스 또는 임베딩 JSON이 없습니다: {config.index_dir}. "
                "최초 한 번 RAG_REBUILD_INDEX=1로 실행하세요."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("signature") != expected:
            raise RuntimeError(
                "저장 인덱스와 현재 데이터/임베딩 설정이 다릅니다. "
                "검토 후 RAG_REBUILD_INDEX=1로 재생성하세요."
            )
        vectorstore = FAISS.load_local(
            str(config.index_dir),
            embeddings,
            allow_dangerous_deserialization=True,
            normalize_L2=True,
        )
        manifest["index_action"] = "loaded"
        return vectorstore, manifest

    validation = validate_embedding_inputs(
        [document.page_content for document in documents], config
    )
    texts = [document.page_content for document in documents]
    vectors = embeddings.embed_documents(texts)
    vectorstore = FAISS.from_embeddings(
        text_embeddings=list(zip(texts, vectors)),
        embedding=embeddings,
        metadatas=[document.metadata for document in documents],
        normalize_L2=True,
    )
    vectorstore.save_local(str(config.index_dir))
    embedding_export = save_embedding_json(documents, vectors, config, validation)
    manifest = {
        "signature": expected,
        "input_validation": validation,
        "embedding_export": embedding_export,
        "index_action": "rebuilt",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return vectorstore, manifest
