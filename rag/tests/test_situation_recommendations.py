"""실제 카탈로그·필터·서비스를 사용하고 외부 모델/검색 호출만 대체한 회귀 시험."""

import unittest
from pathlib import Path
from unittest.mock import patch

from rag.data_loader import RagConfig, load_rag_data
from rag.generator import normalize_query_plan
from rag.menu_lookup import filter_recommendation_candidates, recommendation_attributes
from rag.prompts import AnswerDraft, QueryPlan
from rag.service import CafeRAGService


ROOT = Path(__file__).resolve().parents[2]


class SituationRecommendationTests(unittest.TestCase):
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
                                  explicit_constraint_updates=updates), {"max_price_krw": 3000})
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
        self.assertEqual(result["choices"], [])

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


if __name__ == "__main__":
    unittest.main()
