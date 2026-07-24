from __future__ import annotations

import unittest

import app as app_module
from meeting_minutes.speaker_tagger import TaggedUtterance


def utterance(name: str, title: str = "委員") -> TaggedUtterance:
    return TaggedUtterance(
        order=1,
        speaker_name=name,
        speaker_title=title,
        speaker_role="questioner",
        speaker_group="議員・委員",
        speech_type="question",
        text="質問します。",
        page_start=1,
        page_end=1,
        position_top_start=0.0,
        position_top_end=0.0,
        confidence=0.9,
        reason="title includes council member alias",
    )


class MinutesRosterProfileTests(unittest.TestCase):
    def test_same_day_roster_corrects_one_character_name_error(self) -> None:
        tagged = utterance("杉山武司")
        profiles = {
            "杉山武志": {
                "displayName": "杉山武志",
                "title": "委員",
                "role": "questioner",
                "speakerGroup": "議員・委員",
                "confidence": 0.96,
                "sourceTableKey": "minutes-1356-p1-t1",
                "profileScope": "meeting-day",
            },
        }

        result = app_module.apply_roster_profiles_to_utterances([tagged], profiles)

        self.assertEqual("杉山武志", result[0].speaker_name)
        self.assertIn("same-day roster corrected speaker name", result[0].reason)

    def test_year_dictionary_does_not_fuzzy_merge_names(self) -> None:
        tagged = utterance("杉山武司")
        profiles = {
            "杉山武志": {
                "displayName": "杉山武志",
                "title": "委員",
                "role": "questioner",
                "speakerGroup": "議員・委員",
                "confidence": 0.96,
                "sourceTableKey": "roster",
                "profileScope": "meeting-year",
            },
        }

        result = app_module.apply_roster_profiles_to_utterances([tagged], profiles)

        self.assertEqual("杉山武司", result[0].speaker_name)

    def test_same_day_roster_overrides_exact_typo_in_year_dictionary(self) -> None:
        tagged = utterance("杉山武司")
        profiles = {
            "杉山武志": {
                "displayName": "杉山武志",
                "title": "委員",
                "role": "questioner",
                "speakerGroup": "議員・委員",
                "confidence": 0.96,
                "sourceTableKey": "minutes-1356-p1-t1",
                "profileScope": "meeting-day",
            },
            "杉山武司": {
                "displayName": "杉山武司",
                "title": "委員",
                "role": "questioner",
                "speakerGroup": "議員・委員",
                "confidence": 0.9,
                "sourceTableKey": "utterance",
                "profileScope": "meeting-year",
            },
        }

        result = app_module.apply_roster_profiles_to_utterances([tagged], profiles)

        self.assertEqual("杉山武志", result[0].speaker_name)

    def test_ambiguous_roster_match_is_not_applied(self) -> None:
        tagged = utterance("杉山武司")
        profiles = {
            "杉山武志": {
                "displayName": "杉山武志",
                "title": "委員",
                "role": "questioner",
                "speakerGroup": "議員・委員",
                "confidence": 0.96,
                "sourceTableKey": "minutes-1-p1-t1",
                "profileScope": "meeting-day",
            },
            "杉山武史": {
                "displayName": "杉山武史",
                "title": "委員",
                "role": "questioner",
                "speakerGroup": "議員・委員",
                "confidence": 0.96,
                "sourceTableKey": "minutes-1-p1-t2",
                "profileScope": "meeting-day",
            },
        }

        result = app_module.apply_roster_profiles_to_utterances([tagged], profiles)

        self.assertEqual("杉山武司", result[0].speaker_name)


if __name__ == "__main__":
    unittest.main()
