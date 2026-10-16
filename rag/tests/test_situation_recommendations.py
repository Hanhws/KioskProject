"""실제 카탈로그·필터·서비스를 사용하고 외부 모델/검색 호출만 대체한 회귀 시험."""

import unittest
from pathlib import Path
from unittest.mock import patch

from rag.data_loader import RagConfig, load_rag_data
from rag.generator import normalize_query_plan, sugar_limit_request
from rag.menu_lookup import filter_recommendation_candidates, recommendation_attributes
from rag.prompts import AnswerDraft, QueryPlan
from rag.service import CafeRAGService


ROOT = Path(__file__).resolve().parents[2]


class SituationRecommendationTests(unittest.TestCase):
    def test_constraint_evidence_has_fixed_object_schema_for_structured_output(self):
        from langchain_core.utils.function_calling import convert_to_openai_tool
        schema = convert_to_openai_tool(QueryPlan, strict=True)["function"]["parameters"]

        def check(node):
            if not isinstance(node, dict):
                return
            if node.get("type") == "object":
                self.assertIs(node.get("additionalProperties"), False)
                self.assertEqual(set(node.get("required", [])), set(node.get("properties", {})))
            for value in node.values():
                if isinstance(value, dict):
                    check(value)
                elif isinstance(value, list):
                    for item in value:
                        check(item)
        check(schema)

    @classmethod
    def setUpClass(cls):
        cls.data = load_rag_data(ROOT / "data/megacoffee_rag_enriched")

    def ask(self, question, plan, prior=None, draft=None):
        config = RagConfig(ROOT / "data/megacoffee_rag_enriched", ROOT / "unused-index", ROOT / "unused.json")
        service = CafeRAGService(self.data, None, config)
        with patch("rag.service.plan_question", return_value=plan) as planner, \
             patch("rag.service.retrieve_evidence", return_value=[]), \
             patch("rag.service.generate_rag_answer", return_value=draft or AnswerDraft(answer="추천", status="ok")):
            result = service.ask(question, prior_conditions=prior, recent_history=[{"role": "user", "content": "메뉴를 고르는 중이에요"}])
        self.assertEqual(planner.call_args.args[0], question)
        self.assertEqual(planner.call_args.kwargs["recent_history"][0]["content"], "메뉴를 고르는 중이에요")
        return result

    def test_varied_situations_rank_existing_attributes_not_emotion_word_matches(self):
        # 여기의 계획은 모델 대역의 출력이며, 실제 LLM 해석 통과로 간주하지 않는다.
        cases = [
            ("오늘 마음이 축 처지네", ["달콤함", "기분 전환"], "flavor_tags", "달콤함"),
            ("밤새 과제해서 졸려", ["커피", "진함"], "category", "커피"),
            ("입맛이 영 없네", ["상큼함", "맑고 산뜻함"], "flavor_tags", "상큼함"),
            ("맨날 먹던 것 말고 색다른 거", ["색다른 달콤한 한입 디저트를 원하는 사람"], "category", "마카롱"),
            ("오늘 좋은 일이 있었어", ["기분 전환", "달콤함"], "flavor_tags", "달콤함"),
            ("아침부터 아무것도 못 먹었어", ["샌드위치·브런치", "가벼운 식사"], "category", "샌드위치·브런치"),
        ]
        for question, preferences, field, expected in cases:
            with self.subTest(question=question):
                plan = QueryPlan(intent="recommend", inferred_preferences=preferences,
                                 preferred_terms=[question], required_situations=[question],
                                 situation_response="말씀하신 상황에 맞춰 골라 볼게요.")
                result = self.ask(question, plan)
                self.assertEqual(result["status"], "ok")
                self.assertIn(expected, self.data.catalog_by_id[result["related_menu_ids"][0]][field])
                self.assertTrue(result["answer"].startswith("말씀하신 상황에 맞춰"))
                self.assertEqual([row["value"] for row in result["choices"]], result["related_menu_ids"])
                self.assertLessEqual(len(result["related_menu_ids"]), 4)
                self.assertNotIn("cart_actions", result)

    def test_soft_preferences_never_relax_existing_constraints_or_repeat_excluded_menus(self):
        prior = {"required_coffee_strength": "없음", "max_price_krw": 4000,
                 "max_caffeine_mg": 10, "excluded_allergens": ["우유"], "excluded_menu_ids": ["MENU-019"]}
        result = self.ask("피곤한데 뭐 마실까", QueryPlan(intent="recommend", inferred_preferences=["커피", "진함"],
                          required_coffee_strength="진함", situation_response="많이 피곤하신가 봐요."), prior)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["related_menu_ids"])
        self.assertNotIn("MENU-019", result["related_menu_ids"])
        self.assertEqual(result["applied_conditions"]["max_caffeine_mg"], 10)
        self.assertEqual(result["applied_conditions"]["required_coffee_strength"], "없음")
        for menu_id in result["related_menu_ids"]:
            menu = self.data.catalog_by_id[menu_id]
            self.assertEqual(menu["coffee_strength"], "없음")
            self.assertTrue(any(row["price_krw"] <= 4000 and row.get("caffeine_mg") is not None and row["caffeine_mg"] <= 10
                                for row in self.data.variants_by_menu_name[menu["menu_name"]]))
            self.assertTrue(all("우유" not in (row.get("allergens") or "") for row in self.data.variants_by_menu_name[menu["menu_name"]]))
        self.assertIn("우유가 포함된 것으로 표시된 메뉴를 제외하고 추천했어요.", result["answer"])

    def test_explicit_budget_change_is_allowed_but_inferred_change_is_not(self):
        for updates, expected in [([], 3000), (["max_price_krw"], 5000)]:
            with self.subTest(updates=updates):
                result = self.ask("이제 5천 원까지 괜찮아", QueryPlan(intent="recommend", max_price_krw=5000,
                                  explicit_constraint_updates=updates, constraint_evidence=[{"field": "max_price_krw", "quote": "5천 원"}] if updates else []), {"max_price_krw": 3000})
                self.assertEqual(result["applied_conditions"]["max_price_krw"], expected)

    def test_unsupported_inferred_properties_do_not_become_claims_or_hard_filters(self):
        plan = normalize_query_plan(QueryPlan(intent="recommend", inferred_preferences=["우울증 치료", "우주 맛"],
                                    situation_response="같이 메뉴를 골라 볼까요?"), self.data, "색다른 걸 원해")
        self.assertEqual(plan.inferred_preferences, [])
        self.assertTrue(filter_recommendation_candidates(self.data, plan))
        vocabulary = recommendation_attributes(self.data)
        self.assertNotIn("우울증 치료", str(vocabulary))

    def test_no_candidates_only_when_hard_constraints_exclude_every_menu(self):
        result = self.ask("배고파", QueryPlan(intent="recommend", inferred_preferences=["샌드위치·브런치"]),
                          {"max_price_krw": 1})
        self.assertEqual(result["status"], "no_candidates")
        self.assertEqual(result["related_menu_ids"], [])
        self.assertEqual(result["choices"][0]["value"], "free_text")
        self.assertIn("바꿔서", result["answer"])

    def test_emotion_sharing_does_not_force_recommendation_or_erase_filters(self):
        prior = {"max_price_krw": 3000, "excluded_allergens": ["우유"]}
        result = self.ask("그냥 얘기하고 싶어요. 추천은 괜찮아요.", QueryPlan(intent="general"), prior,
                          AnswerDraft(answer="그랬군요. 편하게 말씀해 주세요.", status="ok"))
        self.assertEqual(result["related_menu_ids"], [])
        self.assertEqual(result["choices"], [])
        for key, value in prior.items():
            self.assertEqual(result["applied_conditions"][key], value)

    def test_router_hint_keeps_recommendation_when_rag_plan_says_general(self):
        result = self.ask("지쳐서 뭔가 마시고 싶어", QueryPlan(intent="general", inferred_preferences=["커피"]),
                          {"_dialogue_intent": "recommend"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["related_menu_ids"])
        self.assertEqual(result["query_plan"]["intent"], "recommend")
        self.assertNotIn("_dialogue_intent", result["applied_conditions"])

    def test_information_hint_does_not_turn_emotion_sharing_into_recommendation(self):
        result = self.ask("그냥 들어주세요", QueryPlan(intent="general", inferred_preferences=["달콤함"]),
                          {"_dialogue_intent": "information"},
                          AnswerDraft(answer="네, 편하게 말씀해 주세요.", status="ok"))
        self.assertEqual(result["related_menu_ids"], [])
        self.assertEqual(result["choices"], [])

    def test_inferred_situation_is_soft_but_explicit_prior_situation_remains_required(self):
        plan = QueryPlan(intent="recommend", inferred_preferences=["디저트"], required_situations=["가벼운 식사"])
        result = self.ask("허기가 져요", plan, {"required_situations": ["저녁"]})
        self.assertIn("가벼운 식사", result["applied_conditions"]["inferred_preferences"])
        self.assertEqual(result["applied_conditions"]["required_situations"], ["저녁"])
        self.assertEqual(result["status"], "ok")

    def test_unverified_health_effect_in_acknowledgment_is_not_displayed(self):
        result = self.ask("기분 전환하고 싶어요", QueryPlan(intent="recommend", inferred_preferences=["달콤함"],
                          situation_response="달콤한 음료가 우울함 개선에 도움이 돼요."))
        self.assertNotIn("도움", result["answer"])
        self.assertNotIn("개선", result["answer"])

    def test_cold_then_sleepiness_does_not_accumulate_inferred_hard_filters(self):
        cold = self.ask("감기 걸릴 것 같은데 추천해 줘", QueryPlan(
            intent="recommend", required_category="따뜻한 차", temperature="HOT", max_caffeine_mg=0,
            inferred_preferences=["티", "향긋함"], situation_response="몸이 좋지 않으신가 봐요.",
            explicit_constraint_updates=["temperature"], constraint_evidence=[{"field": "temperature", "quote": "감기 걸릴 것"}],
        ), draft=AnswerDraft(answer="조건이 없어요", status="no_candidates"))
        self.assertEqual(cold["status"], "ok")
        self.assertTrue(cold["related_menu_ids"])
        self.assertEqual(cold["applied_conditions"]["hard_constraints"], {})
        self.assertIsNone(cold["query_plan"]["temperature"])
        self.assertIsNone(cold["query_plan"]["max_caffeine_mg"])
        sleepy = self.ask("나 좀 졸려 ㅋㅋ", QueryPlan(
            intent="recommend", required_category="커피", max_caffeine_mg=0,
            inferred_preferences=["커피", "진함"], situation_response="많이 졸리신가 봐요.",
        ), cold["applied_conditions"])
        self.assertEqual(sleepy["status"], "ok")
        self.assertEqual(sleepy["related_menu_ids"][0], "MENU-001")
        self.assertEqual(sleepy["applied_conditions"]["hard_constraints"], {})

    def test_explicit_constraints_survive_situations_and_other_recommendations(self):
        first = self.ask("우유 빼고 커피 말고 4천 원 이하로 추천해 줘", QueryPlan(
            intent="recommend", max_price_krw=4000, required_coffee_strength="없음", excluded_allergens=["우유"],
            constraint_evidence=[{"field": "max_price_krw", "quote": "4천 원 이하"}, {"field": "required_coffee_strength", "quote": "커피 말고"}],
        ))
        self.assertEqual(first["applied_conditions"]["hard_constraints"], {"max_price_krw": 4000, "required_coffee_strength": "없음"})
        prior = {**first["applied_conditions"], "excluded_menu_ids": first["related_menu_ids"]}
        next_result = self.ask("피곤한데 다른 거 추천해 줘", QueryPlan(
            intent="recommend", required_coffee_strength="진함", max_price_krw=10000,
            inferred_preferences=["커피", "진함"],
        ), prior)
        self.assertEqual(next_result["applied_conditions"]["hard_constraints"], first["applied_conditions"]["hard_constraints"])
        self.assertEqual(next_result["applied_conditions"]["excluded_allergens"], ["우유"])
        self.assertFalse(set(next_result["related_menu_ids"]) & set(first["related_menu_ids"]))
        for menu_id in next_result["related_menu_ids"]:
            menu = self.data.catalog_by_id[menu_id]
            self.assertEqual(menu["coffee_strength"], "없음")
            self.assertTrue(any(v["price_krw"] <= 4000 for v in self.data.variants_by_menu_name[menu["menu_name"]]))

    def test_legacy_invalid_category_and_emotion_search_term_are_not_retained(self):
        result = self.ask("졸려", QueryPlan(intent="recommend", inferred_preferences=["커피"]),
                          {"required_category": "따뜻한 차", "preferred_terms": ["감기"]})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["related_menu_ids"])
        self.assertEqual(result["applied_conditions"]["hard_constraints"], {})

    def test_unknown_explicit_property_is_not_silently_removed(self):
        result = self.ask("글루텐프리만 추천해 줘", QueryPlan(intent="recommend", required_category="글루텐프리",
                          explicit_constraint_updates=["required_category"], constraint_evidence=[{"field": "required_category", "quote": "글루텐프리만"}]))
        self.assertEqual(result["related_menu_ids"], [])
        self.assertIn("확인하기 어려워요", result["answer"])
        self.assertEqual(result["answer"].count("?"), 1)

    def assert_recorded_low_sugar(self, menu_ids, limit, menu_type=None):
        self.assertTrue(menu_ids)
        lowest = []
        for menu_id in menu_ids:
            menu = self.data.catalog_by_id[menu_id]
            if menu_type:
                self.assertEqual(menu["type"], menu_type)
            recorded = [row["sugar_g"] for row in self.data.variants_by_menu_name[menu["menu_name"]]
                        if row.get("sugar_g") is not None and row["sugar_g"] <= limit]
            self.assertTrue(recorded, menu["menu_name"])
            lowest.append(min(recorded))
        self.assertEqual(lowest, sorted(lowest))

    def test_diabetes_recommendation_uses_recorded_sugar_not_flavor_guesses(self):
        # 실제 서버에서 토마토 스무디·레몬에이드·플레인크로플·딸기라떼를 낸 모델 계획을 재현한다.
        result = self.ask("당뇨환자가 먹을만한 음료 추천해줘", QueryPlan(
            intent="recommend", menu_terms=["음료"],
            inferred_preferences=["담백함", "과일", "과채", "상큼함", "가벼운 식사", "가벼운 휴식"],
            situation_response="당뇨 환자분이 드시기 좋은 음료를 추천해드릴게요.",
        ))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(set(result["related_menu_ids"]), {"MENU-001", "MENU-007", "MENU-003", "MENU-002"})
        self.assert_recorded_low_sugar(result["related_menu_ids"], 10, "음료")
        self.assertEqual(result["applied_conditions"]["hard_constraints"], {"required_type": "음료", "max_sugar_g": 10})
        self.assertIn("아메리카노 (당류 0g)", result["answer"])
        self.assertIn("카푸치노 (따뜻한 것 기준 당류 7g)", result["answer"])
        self.assertIn("의료진", result["answer"])
        self.assertNotIn("드시기 좋은", result["answer"])
        self.assertEqual([row["value"] for row in result["choices"]], result["related_menu_ids"])
        prior = {**result["applied_conditions"], "excluded_menu_ids": result["related_menu_ids"]}
        other = self.ask("다른 거 추천해 주세요", QueryPlan(intent="recommend", situation_response="다른 음료를 추천해 드릴게요."), prior)
        self.assertEqual(other["status"], "no_candidates")
        self.assertIn("10g 이하로 확인된 음료 중에는 더 추천할 메뉴가 없어요.", other["answer"])
        self.assertNotIn("추천해 드릴게요", other["answer"])

    def test_low_sugar_limit_survives_follow_up_and_overrides_sweet_requirement(self):
        first = self.ask("혈당 관리 중인데 달달한 거 추천해 줘", QueryPlan(
            intent="recommend", required_sweetness_level="높음", inferred_preferences=["달콤함"],
            constraint_evidence=[{"field": "required_sweetness_level", "quote": "달달한"}],
        ))
        self.assertEqual(first["status"], "ok")
        self.assertNotIn("required_sweetness_level", first["applied_conditions"]["hard_constraints"])
        self.assert_recorded_low_sugar(first["related_menu_ids"], 10)
        prior = {**first["applied_conditions"], "excluded_menu_ids": first["related_menu_ids"]}
        other = self.ask("다른 거 추천해 줘", QueryPlan(intent="recommend", inferred_preferences=["달콤함", "과일"]), prior)
        self.assertEqual(other["applied_conditions"]["hard_constraints"]["max_sugar_g"], 10)
        self.assertFalse(set(other["related_menu_ids"]) & set(first["related_menu_ids"]))
        self.assert_recorded_low_sugar(other["related_menu_ids"], 10)
        self.assertIn("10g 이하", other["answer"])

    def test_sugar_free_and_explicit_sugar_amount_use_recorded_values(self):
        sugar_free = self.ask("무설탕만 추천해 줘", QueryPlan(intent="recommend", required_category="무설탕",
                              explicit_constraint_updates=["required_category"], constraint_evidence=[{"field": "required_category", "quote": "무설탕만"}]))
        self.assertEqual(sorted(sugar_free["related_menu_ids"]), ["MENU-001", "MENU-007"])
        self.assertIn("당류가 0g으로 확인된 메뉴만", sugar_free["answer"])
        explicit = self.ask("당류 5g 이하 음료 추천", QueryPlan(intent="recommend", max_sugar_g=5,
                            constraint_evidence=[{"field": "max_sugar_g", "quote": "당류 5g 이하"}]))
        self.assertEqual(explicit["applied_conditions"]["hard_constraints"]["max_sugar_g"], 5)
        self.assertEqual(sorted(explicit["related_menu_ids"]), ["MENU-001", "MENU-007"])
        guessed = self.ask("단 건 싫어요", QueryPlan(intent="recommend", max_sugar_g=5))
        self.assertNotIn("max_sugar_g", guessed["applied_conditions"]["hard_constraints"])

    def test_sugar_request_needs_sugar_words_not_taste_or_lookalike_words(self):
        for text in ["알레르기 해당 없는 메뉴 추천", "적당히 달달한 거", "달지 않은 음료", "당근 케이크 있어요?"]:
            self.assertIsNone(sugar_limit_request(text), text)
        for text, limit in [("당뇨가 있어요", 10), ("저당 음료", 10), ("당류 적은 거", 10),
                            ("혈당 관리 중", 10), ("무가당 음료", 0), ("설탕 없는 음료", 0)]:
            self.assertEqual(sugar_limit_request(text), limit, text)

    def test_drink_request_keeps_desserts_out_unless_desserts_are_asked(self):
        drink = normalize_query_plan(QueryPlan(intent="recommend", required_type="디저트"), self.data, "먹을만한 음료 추천해줘")
        self.assertEqual(drink.required_type, "음료")
        for question in ["음료랑 디저트 추천해 줘", "음료 말고 먹을 거 추천"]:
            self.assertIsNone(normalize_query_plan(QueryPlan(intent="recommend"), self.data, question).required_type, question)

    def test_decimal_and_korean_budget_quotes_are_checked_against_amount(self):
        for question, amount in [("3.5천 원 이하로 추천", 3500), ("삼천원 이하로 추천", 3000)]:
            result = self.ask(question, QueryPlan(intent="recommend", max_price_krw=amount,
                              constraint_evidence=[{"field": "max_price_krw", "quote": question}]))
            self.assertEqual(result["applied_conditions"]["hard_constraints"]["max_price_krw"], amount)


if __name__ == "__main__":
    unittest.main()
