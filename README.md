# KioskProject RAG 1차 구현

카페 자연어 키오스크에서 메뉴 자료를 검색하고, CSV에 확인되는 사실을 바탕으로 메뉴 설명·비교·추천·옵션 안내 답변을 만드는 RAG 모듈입니다.

이 브랜치는 프론트엔드와 전체 대화 흐름을 합치기 전의 1차 전달본입니다. UI, 장바구니, 주문 변경, 금액 계산, 결제, 복합 요청 분해와 최종 답변 병합은 포함하지 않습니다.

## 폴더 구조

```text
KioskProject/
├── data/
│   └── megacoffee_rag_enriched/
│       ├── menu_catalog.csv
│       ├── menu_variants.csv
│       ├── options.csv
│       ├── faq_policy.csv
│       ├── pairings.csv
│       ├── unavailable_menu_alternatives.csv
│       └── rag_documents.csv
├── rag/
│   ├── __init__.py
│   ├── data_loader.py
│   ├── build_index.py
│   ├── retriever.py
│   ├── menu_lookup.py
│   ├── prompts.py
│   ├── generator.py
│   └── service.py
└── README.md
```

### 모듈별 책임

| 파일 | 책임 |
| --- | --- |
| `data_loader.py` | 환경 설정, CSV 로딩, 숫자 타입 변환, 메뉴·별칭·상세 ID 연결, 연결 진단 |
| `build_index.py` | `rag_documents.csv`를 LangChain 문서로 변환, 입력 길이 검사, OpenAI 임베딩, FAISS 저장·재사용, 임베딩 JSON 저장 |
| `retriever.py` | 의미 검색, 메뉴·상세·옵션·페어링 문서의 정확 연결, 검색 결과 반환 |
| `menu_lookup.py` | 메뉴·온도별 상세·추천 조건·옵션·페어링·없는 메뉴 대안·온도 대안 정확 조회 |
| `prompts.py` | `QueryPlan`, 구조화 답변 모델, 질문 계획·답변 생성 프롬프트 |
| `generator.py` | 단일 질문의 LLM 계획 생성, 근거 기반 답변 생성, 수치 근거 검증 |
| `service.py` | 데이터·검색·정확 조회·답변 생성을 연결하는 `CafeRAGService` |

## 구현된 기능

- `rag_documents.csv` 각 행을 하나의 문서로 임베딩
- 제목, 검색 표현, 내용을 라벨이 있는 임베딩 문자열로 구성
- 원문, 문서 ID·종류·출처, 메뉴 ID·상세 ID·온도 등 메타데이터 보관
- 저장된 FAISS 인덱스와 임베딩 JSON 재사용
- 메뉴명·별칭·오타 후보 확인
- 메뉴 설명, HOT·ICE 가격, 용량, 영양, 카페인 조회
- 같은 기준을 사용한 메뉴 비교
- 전체 메뉴에서 필수 조건을 먼저 검사한 뒤 취향·상황 기반 추천
- 옵션 적용 가능 여부, 추가금, 최대 수량, 제외 대상 확인
- 메뉴·디저트 페어링과 지원하지 않는 온도의 대안 조회
- 없는 메뉴의 등록된 대안과 유사 후보 안내
- 빈 정보, 모호한 요청, 충돌 조건, 무관한 질문 대응
- 답변에 포함된 메뉴 ID·문서 ID·수치 근거 검증
- UI에서 사용할 선택지, 다음 행동, 중단된 질문 정보를 답변 본문과 별도로 반환

## 설치 패키지

```bash
pip install python-dotenv langchain-community langchain-openai faiss-cpu pydantic tiktoken openai
```

## 환경변수

프로젝트 루트에 `.env`를 만들고 다음 값을 설정합니다. API 키는 저장소에 커밋하지 않습니다.

```dotenv
OPENAI_API_KEY=본인의_API_키

MEGACOFFEE_DATA_DIR=./data/megacoffee_rag_enriched
RAG_INDEX_DIR=./rag_index/faiss_openai_v1
RAG_EMBEDDING_JSON_PATH=./rag_embeddings.json

RAG_EMBEDDING_MODEL=text-embedding-3-small
RAG_ANSWER_MODEL=gpt-4.1-mini
RAG_TOP_K=6
RAG_FETCH_K=20
RAG_REBUILD_INDEX=0
RAG_RUN_LLM=1
```

경로는 실행 위치에 따라 달라질 수 있으므로 팀 통합 환경에서는 절대 경로를 사용하거나, 프로젝트 루트에서 프로그램을 실행합니다.

## 인덱스 생성과 재사용

최초 한 번은 다음 값을 사용합니다.

```dotenv
RAG_REBUILD_INDEX=1
```

실행하면 다음 결과가 생성됩니다.

- `rag_index/faiss_openai_v1/`: FAISS 인덱스
- `rag_embeddings.json`: 원문·메타데이터·실제 임베딩 벡터를 포함한 JSON

생성 후에는 다음처럼 바꿔 기존 결과를 재사용합니다.

```dotenv
RAG_REBUILD_INDEX=0
```

CSV 내용이나 임베딩 모델이 달라지면 저장된 서명과 현재 설정이 일치하지 않으므로 명시적으로 재생성해야 합니다.

## 기본 호출 방법

```python
from rag import CafeRAGService, load_config

config = load_config()
service = CafeRAGService.from_config(config)

result = service.ask("아메리카노 HOT 가격과 카페인을 알려 주세요.")
print(result["answer"])
```

`plan`을 생략하면 `generator.py`의 `plan_question()`이 OpenAI LLM을 호출해 단일 질문을 `QueryPlan`으로 구조화합니다.

```text
자연어 질문
→ LLM QueryPlan 생성
→ 의미 검색
→ CSV 정확 조회
→ 근거 기반 LLM 답변
→ 반환 ID·수치 검증
```

## 외부 분류기와 연결

통합 담당 코드가 이미 요청 종류와 조건을 분류했다면 `QueryPlan` 또는 같은 모양의 `dict`를 전달합니다.

```python
classified_plan = {
    "intent": "menu_info",
    "menu_terms": ["아메리카노"],
    "requested_fields": ["가격", "카페인"],
    "temperature": "HOT",
}

result = service.ask(
    "아메리카노 HOT 가격과 카페인을 알려 주세요.",
    plan=classified_plan,
)
```

`plan`을 전달하면 RAG 내부 질문 계획 LLM은 실행하지 않고 전달받은 계획을 사용합니다.

### 이전 대화 조건

내부 계획 LLM을 사용할 때는 이전 조건을 `prior_conditions`로 전달할 수 있습니다.

```python
result = service.ask(
    "그럼 달콤한 걸로 추천해 줘.",
    prior_conditions={
        "temperature": "ICE",
        "max_price_krw": 4000,
    },
)
```

외부에서 `plan`을 직접 전달하는 경우 현재 서비스는 `prior_conditions`와 `plan`을 다시 합치지 않습니다. 통합 담당 코드가 이전 조건과 새 조건을 병합한 완성된 `plan`을 전달해야 합니다.

## 복합 질문 통합 규격

현재 RAG는 단일 요청의 계획과 답변을 담당합니다. 복합 질문을 여러 하위 요청으로 나누는 LLM 라우터와 여러 결과를 하나의 답변으로 병합하는 기능은 통합 담당 영역입니다.

예를 들어 다음 질문은 통합 코드가 두 요청으로 분해합니다.

```text
아메리카노 가격을 알려 주고 달콤한 디저트도 추천해 줘.
```

권장 분해 결과 형식:

```python
{
    "sub_requests": [
        {
            "question": "아메리카노 가격을 알려 줘.",
            "plan": {
                "intent": "menu_info",
                "menu_terms": ["아메리카노"],
                "requested_fields": ["가격"],
            },
        },
        {
            "question": "달콤한 디저트를 추천해 줘.",
            "plan": {
                "intent": "recommend",
                "required_type": "디저트",
                "required_sweetness_level": "높음",
            },
        },
    ]
}
```

통합 코드는 분해된 요청마다 `service.ask()`를 호출한 뒤, 반환 결과를 원래 순서대로 하나의 사용자 답변으로 합칩니다. 위 구조는 질문마다 사람이 작성하는 규칙이 아니라 LLM 구조화 출력으로 생성해야 합니다.

## 반환값

`CafeRAGService.ask()`의 주요 반환 필드는 다음과 같습니다.

| 필드 | 용도 |
| --- | --- |
| `answer` | 사용자에게 보여줄 쉬운 존댓말 답변 |
| `status` | `ok`, `partial`, `needs_clarification`, `not_found`, `no_candidates`, `out_of_scope` 등 처리 상태 |
| `related_menu_ids` | 실제 CSV에 존재하며 검증된 관련 메뉴 ID |
| `evidence_document_ids` | 실제 답변 근거로 사용한 문서 ID |
| `retrieved_documents` | 검색·정확 연결로 확보한 문서와 출처 |
| `choices` | UI 버튼 등에 사용할 실제 선택지와 자유 입력 선택지 |
| `applied_conditions` | 이번 처리에 적용한 조건 |
| `query_plan` | 정규화된 질문 계획 |
| `claims` | 가격·카페인·옵션 추가금 등 검증된 사실과 실제 행 ID |
| `grounding_validation` | 생성 답변의 근거 검증 결과 |
| `next_action` | 대화 흐름이 이어서 수행할 행동 힌트 |
| `resume_prompt` | 무관한 질문 후 다시 이어갈 중단 질문 |
| `error` | 검색 장애·모델 장애 등 시스템 오류. 메뉴 정보 부족과 구분함 |

## 통합 담당자에게 전달할 사항

1. 단일 질문은 `plan` 없이 호출해도 RAG 내부 LLM이 `QueryPlan`을 생성합니다.
2. 외부 분류 결과를 사용할 때는 `plan`을 전달하면 내부 계획 LLM을 건너뜁니다.
3. 복합 질문은 통합 코드의 LLM 라우터가 여러 단일 요청으로 분해해야 합니다.
4. 외부 `plan`에는 이전 대화 조건까지 반영해 전달합니다.
5. 여러 RAG 결과를 하나의 최종 답변으로 합치는 것은 통합 코드에서 담당합니다.
6. `choices`, `next_action`, `resume_prompt`는 답변 문자열과 분리해 UI·대화 흐름에 사용합니다.
7. 이 서비스는 실제 장바구니를 변경하거나 주문 완료를 확정하지 않습니다.

## 현재 한계

- 복합 질문 LLM 분해기와 최종 답변 병합기는 포함하지 않습니다.
- UI, 장바구니, 주문 옵션 적용, 금액 계산, 결제는 포함하지 않습니다.
- 알레르기 빈 값은 알레르기 없음으로 해석하지 않습니다.
- 우유가 없는 메뉴처럼 부재를 증명해야 하는 추천은 명시적인 음성 데이터가 보완되기 전까지 보장하지 않습니다.
- 디카페인 가능 여부는 카페인 0mg을 의미하지 않습니다.
- 옵션 적용 후 영양·카페인 값은 추정하지 않습니다.
- 팀 정의 프로필과 정책은 실제 브랜드의 공식 정책이라고 표현하지 않습니다.

## 확인한 항목

- Python 문법 및 모듈 import
- 7개 CSV 로딩과 숫자·빈 값 처리
- 메뉴·별칭·상세·문서 ID 연결
- `rag_documents.csv` 132개 문서 변환
- 저장된 FAISS 132개 문서 로딩
- OpenAI 질문 임베딩을 이용한 실제 검색
- 외부 `plan`과 이전 조건을 전달하는 서비스 호출 규격
- 노트북 전용 `input()`, `display()`, 검증용 `assert`가 배포 모듈에 포함되지 않았는지 확인
