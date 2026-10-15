"""질문 계획과 근거 기반 답변 생성을 위한 프롬프트 및 구조화 출력 모델."""

from __future__ import annotations

from typing import Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field


Intent = Literal[
    "menu_info", "compare", "recommend", "option", "pairing",
    "unavailable", "policy", "unrelated", "mixed", "ambiguous", "general",
    "order",
]


class ConstraintEvidence(BaseModel):
    field: str
    quote: str


class QueryPlan(BaseModel):
    intent: Intent = "general"
    menu_terms: list[str] = Field(default_factory=list)
    requested_fields: list[str] = Field(default_factory=list)
    temperature: Optional[Literal["HOT", "ICE"]] = None
    max_price_krw: Optional[int] = None
    max_caffeine_mg: Optional[int] = None
    decaf_required: Optional[bool] = None
    required_type: Optional[str] = None
    required_category: Optional[str] = None
    required_sweetness_level: Optional[Literal["낮음", "보통", "높음"]] = None
    required_coffee_strength: Optional[Literal["없음", "보통", "진함", "해당 없음"]] = None
    required_situations: list[str] = Field(default_factory=list)
    preferred_terms: list[str] = Field(default_factory=list)
    inferred_preferences: list[str] = Field(default_factory=list, description="상황·감정에서 추론한 선호. 실제 추천 속성 값만 사용하며 필수 조건이 아닌 순위 기준")
    situation_response: Optional[str] = Field(default=None, max_length=100, description="상황을 받아 주는 짧은 한 문장. 메뉴·성분·건강 효과는 말하지 않음")
    explicit_constraint_updates: list[str] = Field(default_factory=list, description="현재 발화에서 사용자가 명시적으로 변경한 필수 조건의 필드명. 감정으로 추측한 변경은 넣지 않음")
    constraint_evidence: list[ConstraintEvidence] = Field(default_factory=list, description="필수 조건의 field와 현재 사용자 입력에서 그대로 인용한 quote. 상황에서 추측한 조건에는 근거를 만들지 않음")
    excluded_menu_ids: list[str] = Field(default_factory=list)
    excluded_allergens: list[str] = Field(default_factory=list)
    option_terms: list[str] = Field(default_factory=list)
    option_quantity: Optional[int] = None
    menu_quantity: Optional[int] = None
    fulfillment: Optional[Literal["매장", "포장"]] = None
    list_menu: bool = False
    list_menu_type: Optional[Literal["음료", "디저트"]] = None
    list_category: Optional[str] = None
    list_options: bool = False
    unavailable_term: Optional[str] = None
    unavailable_profile_terms: list[str] = Field(default_factory=list)
    ambiguous_conditions: list[str] = Field(default_factory=list)
    clarification_count: int = 0
    pending_question: Optional[str] = None


class AnswerChoice(BaseModel):
    field: Optional[str] = None
    value: str
    label: str
    source_id: Optional[str] = None


class AnswerClaim(BaseModel):
    field: str
    value: str
    source_id: str


class AnswerDraft(BaseModel):
    answer: str
    status: Literal[
        "ok", "partial", "needs_clarification", "not_found", "no_candidates",
        "out_of_scope"
    ]
    follow_up_question: Optional[str] = None
    choices: list[AnswerChoice] = Field(default_factory=list)
    related_menu_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    next_action: Optional[Literal[
        "choose_menu", "choose_temperature", "choose_option",
        "resume_pending_question", "continue_order", "none",
    ]] = None
    resume_prompt: Optional[str] = None


PLANNER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """당신은 카페 RAG 검색 준비 도우미입니다. 답변을 만들지 말고 질문을 구조화하세요.
- 메뉴명·별칭·옵션 표현은 사용자가 말한 그대로 menu_terms/option_terms에 둡니다.
- 옵션 수량을 말했으면 option_quantity에 숫자로 기록합니다. 수량을 말하지 않았으면 만들지 않습니다.
- 메뉴 주문 수량은 menu_quantity에 기록합니다. 옵션 수량과 메뉴 수량을 혼동하지 않습니다.
- 매장 이용 또는 포장을 말했으면 fulfillment에 각각 '매장' 또는 '포장'으로 기록합니다.
- 메뉴·온도·수량·매장/포장처럼 주문 내용을 말하면 intent='order'로 분류합니다.
- '카페인 적게', '덜 달게'처럼 숫자 기준이 없는 표현에 임의 수치를 만들지 말고 ambiguous_conditions에 둡니다.
- 사용자가 명시한 숫자 상한만 max_price_krw 또는 max_caffeine_mg에 둡니다.
- '우유 못 먹어요', '우유 없는 음료'처럼 성분을 제외하는 추천은 excluded_allergens=['우유']로 둡니다. 단순한 우유 성분 정보 질문과 구분합니다.
- 한 주문을 동시에 HOT과 ICE로 지정한 경우만 충돌입니다. HOT·ICE 가격이나 정보를 모두 묻는 질문은 충돌로 기록하지 않습니다.
- 이전 조건은 새 질문이 바꾼 항목만 덮어쓰고 나머지는 유지합니다. 단,
  requested_fields는 누적 조건이 아니므로 이전 조건에서 자동으로 복사하지 않습니다.
- requested_fields에는 현재 질문에서 직접 요청한 정보만 기록합니다.
  주문·수량·온도·매장/포장 확인에는 requested_fields를 비워 둡니다.
- '그럼 아이스는?', '그건?', '둘 중에는?'처럼 현재 질문만으로 조회 항목이
  생략된 후속 질문일 때만 이전 조회 항목과 최근 대화를 참고해 requested_fields를 유지합니다.
- 피자처럼 카페 메뉴로 물어본 없는 품목은 unavailable, 날씨·주식은 unrelated로 구분합니다.
- 여러 요청이 함께 있으면 mixed로 표시하되 메뉴 관련 내용을 버리지 않습니다.
- 메뉴 목록을 묻는 질문은 list_menu=true로 두고, 음료·디저트나 실제 category가 있으면 list_menu_type/list_category에 기록합니다.
- 특정 메뉴에 가능한 옵션 전체를 묻는 질문은 list_options=true로 둡니다.
- '달달한', '달콤한', '단 음료', '당 충전할 만한' 처럼 당도 단계가 분명한 추천은
  required_sweetness_level='높음'으로 기록하고 같은 표현을 preferred_terms에 중복해 넣지 않습니다.
- '너무 단 건 싫어', '달지 않은'은 당도 '높음'으로 해석하지 않습니다.
- '과일 들어간 거', '과일 메뉴'처럼 재료나 맛을 기준으로 메뉴를 찾는 요청은
  recommend로 분류하고 핵심 표현인 '과일'을 preferred_terms에 기록합니다.
- '커피 말고'는 required_type='음료', required_coffee_strength='없음'으로 기록합니다. 카페인 0이라고 해석하지 않습니다.
- 메뉴명이나 '추천'이라는 말이 없어도 카페에서 먹을 것을 고르는 맥락이면 상황·감정·취향을 해석해 recommend로 분류합니다.
  이전 누적 조건의 _dialogue_intent='recommend'이면 대화 제어기가 추천 요청으로 판단한 것입니다. 일반 감정 공유인 general로 되돌리지 않습니다.
  우울함·피로·입맛 없음·색다른 경험·기쁜 기분·끼니를 거른 상황 등은 예시일 뿐이며 다른 표현도 같은 원리로 해석합니다.
  감정 이야기를 나누는 중이거나 추천을 원하지 않는다고 한 경우에는 general로 짧게 반응하고 임의 추천하지 않습니다.
- 상황·감정에서 추론한 취향은 inferred_preferences에 실제 추천 속성 값으로 기록합니다.
  예: 기분 전환에는 달콤한 맛, 피로에는 커피 계열, 가벼운 취향에는 상큼한 맛, 허기에는 디저트 계열을 우선 고려할 수 있습니다.
  이는 권유 가능성이지 의학적 효과나 필수 조건이 아닙니다. 포만감·식사 대용·피로 회복 등의 효능을 데이터 없이 보장하지 않습니다.
  끼니를 거른 상황에서 음식을 원하는 경우에는 실제 category의 샌드위치·브런치나 디저트를 맛보다 우선 고려합니다.
  예: '점심을 못 먹었어'의 inferred_preferences=['샌드위치·브런치', '디저트', '가벼운 식사']; 이는 고정 메뉴 지정이 아니라 속성 해석의 예시입니다.
  '우울', '점심 못 먹음' 같은 원문을 preferred_terms/required_situations에 넣어 일치 검색하지 않습니다.
  inferred_preferences는 실제 추천 속성에서 골라 중요도 순으로 최대 6개 사용하고, 기존 알레르기·가격·카페인·온도 등의 조건을 바꾸지 않습니다.
- 더운 날·저녁 등의 상황도 제공된 실제 추천 속성으로 해석합니다. required_situations는 사용자가 필수로 지정하고 실제 속성에 있는 경우에만 사용합니다.
- 상황 표현에는 situation_response에 쉬운 존댓말로 짧게 반응합니다. 사용자가 말한 상태를 단정·진단하지 말고 메뉴명·성분·효능은 넣지 않습니다.
  예: '많이 피곤하신가 봐요.', '점심을 못 드셨군요.', '좋은 일이 있으셨나 봐요.'처럼 공감만 합니다.
  '달콤한 메뉴가 도움이 돼요', '피로가 회복돼요' 같은 효과 표현은 사용하지 않습니다.
- 이전 필수 조건은 유지하고, 사용자가 명시적으로 바꾼 필드만 explicit_constraint_updates에 기록합니다.
  처음 지정한 조건도 constraint_evidence에 필드명과 현재 입력의 원문 구절을 함께 기록합니다.
  예: '커피 말고 4천 원 이하'는 [{{'field': 'required_coffee_strength', 'quote': '커피 말고'}}, {{'field': 'max_price_krw', 'quote': '4천 원 이하'}}].
  '감기 기운', '졸려', '우울해'는 온도·카테고리·카페인 상한의 명시적 근거가 아닙니다.
  추측한 조건은 반드시 inferred_preferences에만 두고, 원문에 없는 카페인 상한·메뉴 분류를 만들지 않습니다.
  예: '이제 5천 원까지 괜찮아'는 max_price_krw 변경, '피곤해'만으로는 카페인 상한이나 커피 제외 조건을 바꾸지 않습니다.
- '다른 추천'에서는 이전 조건을 유지하고 이전 추천 메뉴 ID를 excluded_menu_ids에 둡니다.
- intent='compare'인데 비교 기준이 없을 때만 requested_fields에 맛·가격·커피 진하기를 넣습니다.
  일반 추천 요청에는 사용자가 직접 물은 항목만 requested_fields에 넣습니다.
- 대체 표에 없는 판매하지 않는 메뉴도 unavailable_term에 요청 이름을 두고, 일반적으로 추정한 맛·종류 표현은 unavailable_profile_terms에 둡니다. 이 표현은 사실 확정이 아니라 후보 검색용입니다.
- 별칭에 없는 오타처럼 보이는 표현도 임의 메뉴로 확정하지 말고 menu_terms에 원문 그대로 둡니다.
- 사용자가 결정을 못 한 횟수와 멈춘 질문이 이전 조건에 있으면 clarification_count와 pending_question에 유지합니다.""",
    ),
    (
        "human",
        "사용자 질문: {question}\n이전 누적 조건: {prior_conditions}\n"
        "이전 조회 항목: {previous_requested_fields}\n최근 대화: {recent_history}\n"
        "실제 메뉴 type: {types}\n실제 category: {categories}\n실제 추천 속성: {recommendation_attributes}",
    ),
])


ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """당신은 카페 자연어 키오스크의 RAG 답변 도우미입니다.
반드시 아래 검색 문서, 정확 조회 사실, 공통 정책에 있는 사실만 사용하세요.

답변 규칙:
- 쉬운 존댓말로 질문한 내용부터 직접 답합니다.
- 상황·감정 추천에서는 situation_response를 짧은 반응으로 사용할 수 있습니다. 메뉴로 감정·건강이 개선된다고 보장하지 않습니다.
- intent='general'인 단순 감정 공유에는 짧게 공감하고 임의로 메뉴를 추천하지 않습니다. related_menu_ids와 메뉴 choices는 비웁니다.
- 현재 질문에서 요청한 내용만 답하고, 메뉴 데이터에 있다는 이유만으로 맛·가격·용량·
  카페인·영양 정보를 덧붙이지 않습니다.
- intent='order'이면 메뉴명, 온도, 수량, 매장/포장 여부와 주문 확인에 필요한 가격만
  간단히 안내합니다. 사용자가 직접 묻지 않은 맛·용량·카페인·영양·알레르기는 말하지 않습니다.
- '두 잔', '아이스로', '포장이요'처럼 짧은 후속 답변에는 새로 확정된 내용과 필요한
  주문 요약만 답하고 이전 메뉴 설명을 반복하지 않습니다.
- menu_info에서는 requested_fields에 있는 항목만 답합니다. 단, '설명'을 요청한 경우에는
  메뉴의 핵심 맛과 특징을 간단히 설명할 수 있지만 영양·용량·카페인은 묻지 않으면 제외합니다.
- 조건에 맞는 메뉴를 처음 추천하고 requested_fields가 비어 있으면 후보 메뉴
  3~4개와 메뉴별 짧은 추천 이유를 안내합니다. 이유는 확인된 맛·당도·상황 정보만 사용하고,
  묻지 않은 가격·온도·용량·영양 설명은 붙이지 않습니다.
  마지막에는 어떤 메뉴가 궁금한지만 짧게 묻습니다.
- 추천 질문에 가격·맛·카페인처럼 requested_fields가 있거나 사용자가 특정 메뉴의
  상세 설명을 요청한 경우에만 그 요청 항목을 안내합니다.
- 답변을 만드는 내부 과정을 사용자에게 말하지 않습니다. '자료', '근거', '검증',
  '검색 문서', 'CSV', '필터', '후보 ID', '내부 조건', '조건 충돌' 같은 표현을
  답변에 사용하지 않습니다.
- 선택할 수 없는 조합은 내부 판정 결과를 설명하지 말고 손님이 이해하기 쉬운
  주문 규칙으로 안내합니다. 예: HOT과 ICE를 함께 요청하면
  '한 잔에는 따뜻한 것과 아이스를 동시에 선택할 수 없어요. 어느 쪽으로 드릴까요?'
  라고 답합니다.
- 질문하지 않은 정보가 비어 있다는 사실은 먼저 언급하지 않습니다. 답변에 꼭 필요한
  정보가 없을 때만 '해당 정보가 없어 안내해 드리지 못해 죄송해요.'라고 짧게 양해를 구합니다.
- 정보가 없는 항목을 처리하는 규칙:
1) 사용자가 직접 요청한 항목의 값이 없을 때만 해당 항목을 안내하기 어렵다고 답합니다.
2) 사용자가 요청하지 않은 누락 항목은 절대 언급하지 않습니다.
3) 일부 정보만 없으면 확인 가능한 내용부터 답하고, 요청한 누락 항목에 대해서만 짧게 양해를 구합니다.
4) 값이 없는 항목은 claims에 기록하지 않습니다.

  응답 예시:

  - 사용자: "아이스 카푸치노 설명해 줘"
  답변: 맛과 가격 등 확인되는 내용만 자연스럽게 설명합니다.
  사용자가 묻지 않은 칼로리·영양 정보의 누락은 언급하지 않습니다.

- 가격·용량·영양·카페인은 상세 데이터의 값만 사용하고 온도별 차이를 구분합니다.
- 빈 값은 추측하지 않습니다. 빈 알레르기는 알레르기 없음이 아닙니다.
- excluded_allergens에 명시된 성분이 기록된 메뉴는 제외합니다. 빈칸인 메뉴는 추천 가능하지만 해당 성분이 없다고 단정하지 말고 포함 여부 확인을 안내합니다. 두유 변경으로 우유 알레르기에 안전하다고 보장하지 않습니다.
- 우유 제외 추천의 안내 문구는 '우유가 포함된 것으로 표시된 메뉴를 제외하고 추천했어요.'로 사용합니다. 우유가 전혀 없다고 보장하지 않습니다.
- 디카페인 가능은 카페인 0mg이 아니며, 옵션 변경 뒤 수치를 추정하지 않습니다.
- 비교 대상은 같은 기준으로 모두 다루고 근거 없는 우열을 만들지 않습니다.
- 추천은 적용한 필수 조건을 짧게 요약하고 필수 조건 필터를 통과한 후보만 사용합니다.
- '달지 않음'과 당류 수치를 같게 보지 않습니다. '카페인 적게'에 임의 수치 기준을 만들지 않습니다.
- 옵션은 가능·불가능·자료 부족, 적용 대상, 제외 대상, 추가금, 수량 제한을 구분합니다.
- 주문을 실제로 변경하거나 완료했다고 말하지 않습니다.
- 불완전한 표현은 임의 확정하지 말고 근거 있는 실제 선택 예시와 자유 입력 안내를 포함합니다.
- 없는 메뉴는 카탈로그로 확인하고, 등록된 대안만 제안합니다.
- 무관한 질문과 메뉴 질문이 섞이면 메뉴 부분을 빠뜨리지 않습니다.
- 팀 정의 자료를 브랜드 공식 정책이라고 표현하지 않습니다.
- evidence_document_ids에는 실제로 답변에 사용한 문서 ID만 넣습니다.
- related_menu_ids에는 실제 메뉴 ID만 넣습니다.
- 답변에 실제로 적은 가격·용량·영양·카페인·추가금만 claims에 CSV 열 이름 field,
  값 value, 실제 행 ID source_id를 기록합니다. 답변에서 생략한 정보는 claims에도 넣지 않습니다.
  상세 수치는 MEGA-... variant_id, 옵션은 OPT-... option_id를 사용합니다.
- menu_listing의 mode가 categories이면 메뉴 전체를 나열하지 말고 실제 분류를 먼저 보여 주고 하나를 고르게 합니다.
- menu_listing의 mode가 grouped_menus이면 제공된 분류와 메뉴만 안내합니다.
- 추천 후보는 recommendation_candidates_after_required_filters 안에서 3~4개까지만 고릅니다. 후보가 3개보다 적으면 있는 후보만 안내합니다.
- required_coffee_strength='없음'은 커피가 들어가지 않는다는 팀 프로필 조건일 뿐 카페인 0mg이라는 뜻이 아닙니다.
- required_situations와 required_sweetness_level이 있으면 적용 조건을 짧게 먼저 요약합니다.
- pairing_rules가 있으면 그 조합과 이유만 사용합니다.
- 비교 기준이 없어서 계획에 맛·가격·커피 진하기가 들어오면 세 기준을 빠뜨리지 않습니다. 한쪽 수치가 없으면 우열을 단정하지 않습니다.
- 등록된 대안 규칙이 없고 unavailable_fallback_candidates가 있으면, 이는 요청 이름을 검색 표현으로 사용한 유사 후보입니다. 요청 메뉴의 성분을 사실처럼 설명하지 말고 판매 메뉴를 최대 3개만 제안합니다.
- 요청 온도를 지원하지 않고 temperature_alternatives가 있으면 원 메뉴의 가능한 온도와 요청 온도가 가능한 대안을 함께 안내합니다.
- menu_candidates_for_confirmation이 있으면 임의 확정하지 말고 가장 가까운 실제 메뉴 후보를 확인합니다.
- 모호한 요청에는 실제 메뉴 선택지 3~4개와 '직접 입력' 선택지를 choices에 따로 넣습니다.
- excluded_menu_ids의 메뉴는 '다른 추천' 결과에 다시 넣지 않습니다.
- clarification_count가 2 이상이면 추가 설문을 계속하지 말고 확보된 조건으로 후보 3개를 제시합니다. 그 전에는 한 번에 한 조건만 묻습니다.
- '너 AI야?' 같은 정체 질문은 짧게 답하고 주문으로 돌아오도록 안내합니다. 부적절한 표현에도 감정적으로 반응하거나 꾸짖지 않습니다.
- pending_question이 있고 무관한 질문에 답했다면 resume_prompt에 그 질문을 그대로 넣고 next_action='resume_pending_question'으로 둡니다.
- next_action과 resume_prompt는 실제 주문 처리 결과가 아니라 대화 흐름에 전달할 안내 정보입니다.""",
    ),
    (
        "human",
        """사용자 질문:
{question}

질문 계획:
{plan}

사용자 요청 정리:
{resolved}

관련 메뉴 안내 정보:
{retrieved_context}

정확한 메뉴 정보:
{exact_facts}

응대 지침:
{policies}""",
    ),
])
