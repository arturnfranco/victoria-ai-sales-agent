"""Tests for the canonical conversation-stage schema."""

import unittest

from app.schemas import ConversationStage


class ConversationStageTests(unittest.TestCase):
    def test_contains_all_documented_stages_in_journey_order(self) -> None:
        self.assertEqual(
            list(ConversationStage),
            [
                ConversationStage.OPENING,
                ConversationStage.DISCOVERY,
                ConversationStage.QUALIFICATION,
                ConversationStage.OBJECTION,
                ConversationStage.BOOKING,
                ConversationStage.BOOKED,
                ConversationStage.NO_FIT,
                ConversationStage.CLOSED,
            ],
        )

    def test_values_match_the_structured_output_contract(self) -> None:
        self.assertEqual(
            [stage.value for stage in ConversationStage],
            [
                "OPENING",
                "DISCOVERY",
                "QUALIFICATION",
                "OBJECTION",
                "BOOKING",
                "BOOKED",
                "NO_FIT",
                "CLOSED",
            ],
        )

    def test_members_are_string_compatible(self) -> None:
        self.assertTrue(all(isinstance(stage, str) for stage in ConversationStage))
        self.assertEqual(ConversationStage("DISCOVERY"), ConversationStage.DISCOVERY)

    def test_unknown_stage_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ConversationStage("FOLLOW_UP")


if __name__ == "__main__":
    unittest.main()
