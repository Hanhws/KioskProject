import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from rag.data_loader import RagConfig, load_rag_data
from rag.generator import normalize_query_plan
from rag.menu_lookup import filter_recommendation_candidates
from rag.prompts import AnswerDraft, QueryPlan
from rag.service import CafeRAGService, brief_recommendation_answer


ROOT = Path(__file__).resolve().parents[2]


class QueryPlanNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_rag_data(ROOT / "data" / "megacoffee_rag_enriched")

    def test_sweet_recommendation_uses_structured_level_not_literal_term(self):
        plan = QueryPlan(
            intent="recommend",
            requested_fields=["맛", "가격", "커피 진하기"],
            preferred_terms=["달달한"],
            required_coffee_strength="없음",
        )
        result = normalize_query_plan(
            plan, self.data, "커피 말고 달달한 음료 추천해 줘"
        )
        self.assertEqual(result.required_sweetness_level, "높음")
        self.assertEqual(result.preferred_terms, [])
        self.assertEqual(result.requested_fields, [])

    def test_negative_sweetness_expression_is_not_forced_to_high(self):
        plan = QueryPlan(intent="recommend", preferred_terms=["달지 않은"])
        result = normalize_query_plan(
            plan, self.data, "달지 않은 음료 추천해 줘"
        )
        self.assertIsNone(result.required_sweetness_level)
        self.assertEqual(result.preferred_terms, ["달지 않은"])

    def test_explicit_recommendation_details_are_preserved(self):
        plan = QueryPlan(
            intent="recommend",
            requested_fields=["가격"],
            preferred_terms=["달콤한"],
        )
        result = normalize_query_plan(
            plan, self.data, "달콤한 음료 추천하고 가격도 알려 줘"
        )
        self.assertEqual(result.required_sweetness_level, "높음")
        self.assertEqual(result.requested_fields, ["가격"])

    def test_recommendation_reasons_use_catalog_facts_in_id_order(self):
        answer = brief_recommendation_answer(["MENU-008", "MENU-010"], self.data)
        self.assertLess(answer.index("초코라떼"), answer.index("고구마라떼"))
        for menu_id in ["MENU-008", "MENU-010"]:
            reason = self.data.catalog_by_id[menu_id]["flavor_tags"].split("|")[0]
            self.assertIn(reason, answer)
        self.assertNotIn("원", answer)

    def test_milk_constraint_is_not_a_preferred_search_word(self):
        plan = normalize_query_plan(QueryPlan(intent="recommend", preferred_terms=["우유 없는"]),
                                    self.data, "우유 못 먹는데 뭐 마실 수 있어요?")
        self.assertEqual(plan.excluded_allergens, ["우유"])
        self.assertEqual(plan.preferred_terms, [])
        candidates = filter_recommendation_candidates(self.data, plan)
        self.assertIn("MENU-001", candidates)  # 빈칸은 제외하지 않되 응답에서 확인 안내
        self.assertNotIn("MENU-002", candidates)

    def test_allergen_filter_excludes_only_explicit_milk_in_any_variant(self):
        data = deepcopy(self.data)
        plan = QueryPlan(intent="recommend", excluded_allergens=["우유"])
        variants = data.variants_by_menu_name["아메리카노"]
        for variant in variants:
            variant["allergens"] = "대두"
        self.assertIn("MENU-001", filter_recommendation_candidates(data, plan))
        variants[0]["allergens"] = None
        self.assertIn("MENU-001", filter_recommendation_candidates(data, plan))
        variants[0]["allergens"] = "우유|대두"
        self.assertNotIn("MENU-001", filter_recommendation_candidates(data, plan))

    def service_answer(self, plan, draft, prior_conditions=None):
        config = RagConfig(ROOT / "data/megacoffee_rag_enriched", ROOT / "unused-index", ROOT / "unused.json")
        service = CafeRAGService(self.data, None, config)
        # 검색/모델 경계만 대체하고 후보 필터·응답 검증·조건 유지의 실제 코드는 실행한다.
        with patch("rag.service.retrieve_evidence", return_value=[]), \
             patch("rag.service.generate_rag_answer", return_value=draft):
            return service.ask("추천해 주세요", plan=plan, prior_conditions=prior_conditions)

    def test_second_clarification_forces_three_candidates_and_preserves_filters(self):
        plan = QueryPlan(intent="recommend", required_sweetness_level="높음",
                         required_coffee_strength="없음", excluded_menu_ids=["MENU-008"])
        result = self.service_answer(plan, AnswerDraft(answer="온도도 알려 주세요?", status="needs_clarification",
                                                      follow_up_question="온도도 알려 주세요?"),
                                     {"clarification_count": 2})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["related_menu_ids"]), 3)
        self.assertNotIn("MENU-008", result["related_menu_ids"])
        self.assertNotIn("온도도", result["answer"])
        self.assertIsNone(result["follow_up_question"])
        self.assertEqual([row["value"] for row in result["choices"]], result["related_menu_ids"])
        for menu_id in result["related_menu_ids"]:
            self.assertEqual(self.data.catalog_by_id[menu_id]["sweetness_level"], "높음")
            self.assertEqual(self.data.catalog_by_id[menu_id]["coffee_strength"], "없음")

    def test_first_clarification_is_not_replaced_by_unfiltered_candidates(self):
        result = self.service_answer(QueryPlan(intent="recommend"),
                                     AnswerDraft(answer="커피가 들어간 음료로 드릴까요?", status="needs_clarification",
                                                 follow_up_question="커피가 들어간 음료로 드릴까요?"),
                                     {"clarification_count": 1})
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["related_menu_ids"], [])

    def test_ambiguous_plan_also_stops_clarifying_after_two_questions(self):
        result = self.service_answer(QueryPlan(intent="ambiguous"),
                                     AnswerDraft(answer="단맛도 골라 주세요?", status="needs_clarification"),
                                     {"clarification_count": 2})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["related_menu_ids"]), 3)
        self.assertNotIn("단맛도 골라", result["answer"])
        self.assertEqual(len(result["choices"]), 3)

    def test_milk_constraint_survives_followup_and_blocks_unsafe_model_answer(self):
        result = self.service_answer(QueryPlan(intent="recommend"),
                                     AnswerDraft(answer="라떼를 드세요", status="ok", related_menu_ids=["MENU-002"]),
                                     {"excluded_allergens": ["우유"], "clarification_count": 2})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["related_menu_ids"]), 3)
        self.assertNotIn("MENU-002", result["related_menu_ids"])
        self.assertEqual([row["value"] for row in result["choices"]], result["related_menu_ids"])
        self.assertIn("직원", result["answer"])
        self.assertIn("알레르기 정보가 없어", result["answer"])
        self.assertTrue(result["answer"].startswith("우유가 포함된 것으로 표시된 메뉴를 제외하고 추천했어요."))
        self.assertNotIn("우유가 없", result["answer"])
        self.assertNotIn("라떼", result["answer"])


if __name__ == "__main__":
    unittest.main()
