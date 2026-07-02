from __future__ import annotations

import sys
import unittest
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from meeting_minutes.pdf_extractor import ExtractedLine
from meeting_minutes.speaker_tagger import classify_speaker, tag_utterances


def line(no: int, text: str) -> ExtractedLine:
    return ExtractedLine(page=1, line_no=no, text=text, x0=0.0, top=float(no * 10))


class SpeakerTaggerTest(unittest.TestCase):
    def test_external_testimony_titles_are_answerers(self) -> None:
        self.assertEqual(classify_speaker("参考人", "美祢太郎")[0], "answerer")
        self.assertEqual(classify_speaker("証人", "美祢花子")[0], "answerer")

    def test_answer_opening_reclassifies_questioner_title_as_answerer(self) -> None:
        utterances = tag_utterances([
            line(1, "○6番（岡山隆君） お答えします。御質問の件について説明いたします。"),
        ])
        self.assertEqual(utterances[0].speaker_role, "answerer")
        self.assertEqual(utterances[0].speech_type, "answer")

    def test_chair_report_request_reclassifies_following_explanation_as_report(self) -> None:
        utterances = tag_utterances([
            line(1, "○委員長（山田太郎君） 委員長報告をお願いいたします。"),
            line(2, "○総務企画部長（佐々木昭治君） 説明いたします。前回の協議内容を報告します。"),
        ])
        self.assertEqual(utterances[1].speaker_role, "report")
        self.assertEqual(utterances[1].speech_type, "report")

    def test_council_secretariat_is_not_reclassified_by_answer_opening(self) -> None:
        utterances = tag_utterances([
            line(1, "○議会事務局長（寺杢真輔君） お答えします。資料は配付済みです。"),
        ])
        self.assertEqual(utterances[0].speaker_role, "secretariat")


if __name__ == "__main__":
    unittest.main()
