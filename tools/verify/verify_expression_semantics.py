"""表情分派、真实 state 循环及缺槽降级的回归检查，不依赖 Blender。"""
import sys
import unittest
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "lib"))
import moe_anim_catalog as CAT
import moe_expression_contract as C


class ExpressionSemanticsTests(unittest.TestCase):
    def test_distinct_emotions_and_speech_composition(self):
        cases = {
            "head_AI_content": ("smile", False),
            "head_emotion_happy": ("happy", False),
            "head_emotion_laugh": ("laugh", False),
            "head_emotion_flirtyLeft": ("flirty", False),
            "head_emotion_worry": ("worry", False),
            "head_emotion_sadness": ("sadness", False),
            "head_emotion_shame": ("shame", False),
            "head_emotion_disgust": ("disgust", False),
            "head_emotion_angry": ("angry", False),
            "head_emotion_boredom": ("boredom", False),
            "head_emotion_thinking": ("thinking", False),
            "head_emotion_fear": ("fear", False),
            "head_emotion_shock": ("shock", False),
            "head_emotion_happy_teaching_loop1": ("happy", True),
            "head_emotion_stressed_teaching_loop2": ("worry", True),
            "head_throneRoom_conversation1": ("neutral", True),
            "head_wedding_objection_loop": ("angry", True),
            "head_wedding_happy_cry_loop1": ("happy", False),
            "head_emotion_slightlyWinningOneHandedAggressive": ("happy", False),
            "head_emotion_stronglyLosingTwoHandedAggressive1": ("sadness", False),
            "head_chessCertainWin": ("happy", False),
            "head_winter": ("neutral", False),
            "head_AI_irrational": ("angry", False),
            "head_AI_rational": ("thinking", False),
        }
        for name, expected in cases.items():
            for spelling in (name, name.lower(), name.upper()):
                with self.subTest(name=spelling):
                    plan = CAT.performance_for(spelling)
                    self.assertEqual((plan["emotion"], plan["talk"]), expected)
                    self.assertEqual(CAT.expr_for(spelling), plan["expression"])

    def test_state_graph_includes_two_state_cycle_and_shared_entry(self):
        states = [
            ("happy_entry", "head_happy_start", 'next_state = "happy_a"'),
            ("happy_a", "head_happy_a", 'looping = no next_state = "happy_b"'),
            ("happy_b", "head_happy_b", 'looping = no next_state = "happy_a"'),
            ("talk_entry", "head_talk", 'next_state = "talk"'),
            ("talk", "head_talk", 'looping = no next_state = "talk"'),
            ("happy_exit", "head_happy_exit", 'next_state = "idle"'),
            ("bow", "head_bow", 'next_state = "idle"'),
            ("idle", "head_idle", 'looping = yes'),
        ]
        with patch.object(CAT, "_EXPRESSION_PHASES", None), \
                patch.object(CAT, "_read", return_value=""), \
                patch.object(CAT, "_states", return_value=states):
            self.assertEqual(CAT.performance_for("head_happy_start")["phase"], "entry")
            for base in ("head_happy_a", "head_happy_b", "head_talk", "head_idle"):
                self.assertEqual(CAT.performance_for(base)["phase"], "loop")
            self.assertEqual(CAT.performance_for("head_happy_exit")["phase"], "exit")
            self.assertEqual(CAT.performance_for("head_bow")["phase"], "oneshot")

    def test_reading_is_independent_of_emotion(self):
        for name in ("head_reading_loop", "head_writing", "head_throneRoom_writerWriting1",
                     "head_page_flipping_loop"):
            self.assertEqual(CAT.performance_for(name)["gaze"], "reading")
        self.assertIsNone(CAT.performance_for("head_throneRoom_conversation1")["gaze"])

    def test_new_emotions_preserve_legacy_native_breathing(self):
        self.assertTrue(CAT.performance_for("head_emotion_thinking")["native_breathing"])
        self.assertTrue(CAT.performance_for("head_AI_compassionate")["native_breathing"])
        self.assertFalse(CAT.performance_for("head_emotion_happy")["native_breathing"])
        self.assertFalse(CAT.performance_for("head_throneRoom_conversation1")["native_breathing"])
        # Keep the legacy broad substring rule isolated to the native layer.
        self.assertFalse(CAT.native_breathing_for("head_winter"))
        self.assertEqual(CAT.performance_for("head_winter")["emotion"], "neutral")

    def test_existing_native_alias_precedence(self):
        split, _ = C.resolve_bindings([
            "ウィンク", "ウィンク右", "まばたき", "びっくり左", "びっくり右",
            "びっくり", "怒り左", "怒り右", "怒り", "あ", "あ２", "あ３",
            "ｷﾘｯ", "じと目", "真面目", "笑い",
        ], scheme="mmd_jp")
        self.assertEqual(split["eye_wide_l"], "びっくり左")
        self.assertEqual(split["eye_wide_r"], "びっくり右")
        self.assertEqual(split["brow_down_l"], "怒り左")
        self.assertEqual(split["mouth_open"], "あ３")
        self.assertEqual(split["eye_narrow_l"], "ｷﾘｯ")
        self.assertEqual(split["eye_squint_l"], "笑い")

    def test_missing_narrow_falls_back_only_to_present_blink(self):
        source = [{"eye_narrow_l": .8, "eye_blink_l": .3}]
        curve, used = C.substitute_missing(source, {"eye_narrow_l"})
        self.assertEqual(curve, [{"eye_blink_l": .3}])
        self.assertEqual(used, {"eye_narrow_l": "eye_blink_l"})
        curve, used = C.substitute_missing(source, {"eye_narrow_l", "eye_blink_l"})
        self.assertEqual(used, {})
        self.assertEqual(curve, source)

    def test_decals_not_needed_to_distinguish_emotions(self):
        # 单个双侧键 + 无贴花的模型，仍能用眉眼嘴区别十三种情绪。
        keys = ["まばたき", "びっくり", "困る", "怒り", "上", "口角上げ",
                "口角下げ", "あ２", "い", "ｷﾘｯ", "笑い"]
        bindings, missing = C.resolve_bindings(keys, scheme="mmd_jp")
        poses = {}
        for name, style in C.EXPRESSION_STYLE.items():
            if name == "neutral":
                continue
            curve, _ = C.substitute_missing(C.sample_expression(name, 201), missing)
            values = curve[round(style["hold_sample"] * 200)]
            target = {}
            for slot, value in values.items():
                if slot in bindings:
                    key = bindings[slot]
                    target[key] = max(target.get(key, 0.0), value)
            poses[name] = target
        for a, b in combinations(poses, 2):
            distance = sum(abs(poses[a].get(k, 0.0) - poses[b].get(k, 0.0)) for k in keys)
            self.assertGreater(distance, .1, (a, b, distance))

    def test_new_recipes_use_existing_animated_slots(self):
        baseline = ("happy", "angry", "shock", "sadness", "talk", "life", "reading_gaze")
        slots = {s for n in baseline for _, d in C.EXPRESSIONS[n] for s in d}
        for name, keys in C.EXPRESSIONS.items():
            self.assertTrue({s for _, d in keys for s in d} <= slots, name)
            self.assertIn(name, C.OUTPUT_FRAMES)
            self.assertIn(name, C.ANIM_IDS)
            self.assertIn(name, C.STATE_NAMES)

    def test_installed_game_catalog_case_invariance(self):
        if not (CAT.GAME / "female_head/female_head.asset").is_file():
            self.skipTest("No CK3 installation")
        for base in CAT.Catalog("head").bases:
            original = CAT.performance_for(base)
            self.assertEqual(original, CAT.performance_for(base.upper()), base)
            self.assertEqual(original, CAT.performance_for(base.lower()), base)


if __name__ == "__main__":
    unittest.main(verbosity=2)
