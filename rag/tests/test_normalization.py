import unittest
from pathlib import Path

from rag.data_loader import load_rag_data
from rag.generator import normalize_query_plan
from rag.prompts import QueryPlan


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


if __name__ == "__main__":
    unittest.main()
