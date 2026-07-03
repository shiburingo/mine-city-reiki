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

    def test_unknown_report_closing_is_report(self) -> None:
        utterances = tag_utterances([
            line(1, "○社会復帰サポート美祢常務取締役（太田幸充君） 今後とも御理解の上で支援いただきたいと思っております。"),
            line(2, "私からの報告以上でございます。どうもありがとうございました。"),
        ])
        self.assertEqual(utterances[0].speaker_role, "report")
        self.assertEqual(utterances[0].speaker_group, "報告")
        self.assertEqual(utterances[0].speech_type, "report")

    def test_council_secretariat_is_not_reclassified_by_answer_opening(self) -> None:
        utterances = tag_utterances([
            line(1, "○議会事務局長（寺杢真輔君） お答えします。資料は配付済みです。"),
        ])
        self.assertEqual(utterances[0].speaker_role, "secretariat")

    def test_chair_alias_zacho_is_proceeding(self) -> None:
        self.assertEqual(classify_speaker("座長", "美祢太郎")[0], "chair")

    def test_committee_member_title_suffix_is_questioner(self) -> None:
        self.assertEqual(classify_speaker("総務企業委員", "安富法明")[0], "questioner")

    def test_number_only_title_is_questioner(self) -> None:
        self.assertEqual(classify_speaker("10", "秋枝秀稔")[0], "questioner")

    def test_temporary_seat_title_is_questioner(self) -> None:
        self.assertEqual(classify_speaker("仮議席6番", "岡山隆")[0], "questioner")

    def test_waterworks_business_administrator_is_answerer(self) -> None:
        role, group, _confidence, _reason = classify_speaker("上下水道事業管理者", "波佐間敏")
        self.assertEqual(role, "answerer")
        self.assertEqual(group, "執行部")

    def test_hospital_clerk_manager_answer_opening_is_answerer(self) -> None:
        utterances = tag_utterances([
            line(1, "○市立病院事務部事務長（古川和則君） では、岡山委員の質問にお答えします。"),
        ])
        self.assertEqual(utterances[0].speaker_role, "answerer")
        self.assertEqual(utterances[0].speaker_group, "執行部")
        self.assertEqual(utterances[0].speech_type, "answer")

    def test_chief_clerk_title_is_answerer(self) -> None:
        role, group, _confidence, _reason = classify_speaker("建設経済部商工労働課主査", "河村充展")
        self.assertEqual(role, "answerer")
        self.assertEqual(group, "執行部")

    def test_executive_subtitle_aliases_are_answerers(self) -> None:
        for title in [
            "総合政策部地域情報課係長",
            "まちづくり推進班長",
            "病院事業統括管理者",
            "山口ケーブルビジョン株式会社顧問",
        ]:
            with self.subTest(title=title):
                role, group, _confidence, _reason = classify_speaker(title, "美祢太郎")
                self.assertEqual(role, "answerer")
                self.assertEqual(group, "執行部")

    def test_unknown_between_question_context_is_reclassified_as_answerer(self) -> None:
        utterances = tag_utterances([
            line(1, "○5番（山田太郎君） 水道事業についてお尋ねします。"),
            line(2, "○水道担当（波佐間敏君） ただいまの御質問について説明申し上げます。"),
        ])
        self.assertEqual(utterances[1].speaker_role, "answerer")
        self.assertEqual(utterances[1].speech_type, "answer")

    def test_unknown_after_chair_prompt_and_question_context_is_answerer(self) -> None:
        utterances = tag_utterances([
            line(1, "○委員（南口彰夫君） 地元中小企業対策について質問します。"),
            line(2, "○委員長（南口彰夫君） はい、河村商工労働課主査。"),
            line(3, "○地域担当（河村充展君） 只今のご質問ですが、商業の関係につきましては融資制度を設けております。"),
        ])
        self.assertEqual(utterances[2].speaker_role, "answerer")
        self.assertEqual(utterances[2].speaker_group, "執行部")
        self.assertEqual(utterances[2].speech_type, "answer")

    def test_unknown_question_closing_is_questioner(self) -> None:
        utterances = tag_utterances([
            line(1, "○地域代表（安富法明君） 今南口委員の質問と市長の答弁というのはどうかと思いました。"),
            line(2, "直接これとは関係ないかもしれませんけれども、まずお答えをいただいておきたいというふうに思います。"),
        ])
        self.assertEqual(utterances[0].speaker_role, "questioner")
        self.assertEqual(utterances[0].speaker_group, "議員・委員")
        self.assertEqual(utterances[0].speech_type, "question")

    def test_answer_request_closing_is_questioner(self) -> None:
        utterances = tag_utterances([
            line(1, "○地域代表（岡山隆君） 経緯だけでもお答えがあってもいいのではないかと思います。"),
            line(2, "この点についてよろしくお願いします。"),
        ])
        self.assertEqual(utterances[0].speaker_role, "questioner")
        self.assertEqual(utterances[0].speaker_group, "議員・委員")
        self.assertEqual(utterances[0].speech_type, "question")

    def test_same_speaker_after_answer_is_followup_question(self) -> None:
        utterances = tag_utterances([
            line(1, "○地域代表（秋枝秀稔君） 採用試験について質問します。"),
            line(2, "○総務企画部次長（古屋敦子君） 秋枝議員の御質問にお答えします。"),
            line(3, "○副議長（高木法生君） ちょっと手挙げてください。秋枝議員。"),
            line(4, "○地域代表（秋枝秀稔君） 大体分かりました。そういうことでよろしいですか。"),
        ])
        self.assertEqual(utterances[3].speaker_role, "questioner")
        self.assertEqual(utterances[3].speaker_group, "議員・委員")
        self.assertEqual(utterances[3].speech_type, "question")


if __name__ == "__main__":
    unittest.main()
