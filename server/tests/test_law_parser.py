from __future__ import annotations

import importlib
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


with patch.dict(os.environ, {"DB_AUTO_INIT": "0"}):
    app_module = importlib.import_module("app")


class EgoveLawParserTest(unittest.TestCase):
    def test_article_aliases_cover_kanji_arabic_and_raw_num(self) -> None:
        aliases = set(app_module.egov_article_aliases("1_2", "第一条の二"))
        self.assertIn("1_2", aliases)
        self.assertIn("第一条の二", aliases)
        self.assertIn("第1条の2", aliases)

    def test_structured_parser_keeps_chapter_article_and_appendix_table(self) -> None:
        root = ET.fromstring(
            """
            <DataRoot>
              <LawBody>
                <TOC><TOCLabel>目次</TOCLabel></TOC>
                <MainProvision>
                  <Chapter Num="1">
                    <ChapterTitle>第一章 総則</ChapterTitle>
                    <Article Num="1_2">
                      <ArticleCaption>（目的）</ArticleCaption>
                      <ArticleTitle>第一条の二</ArticleTitle>
                      <Paragraph><ParagraphSentence><Sentence>本文です。</Sentence></ParagraphSentence></Paragraph>
                    </Article>
                  </Chapter>
                </MainProvision>
                <AppdxTable>
                  <AppdxTableTitle>別表第一</AppdxTableTitle>
                  <RelatedArticleNum>第一条関係</RelatedArticleNum>
                  <TableStruct>
                    <Table><TableRow><TableColumn>項目</TableColumn><TableColumn>内容</TableColumn></TableRow></Table>
                  </TableStruct>
                </AppdxTable>
              </LawBody>
            </DataRoot>
            """
        )

        articles = app_module.iter_egov_articles(root)
        article = next(item for item in articles if item["article_number"] == "第一条の二")
        appendix = next(item for item in articles if item["article_number"] == "別表第一")

        self.assertEqual(article["parent_path"], "本則 / 第一章 総則")
        self.assertIn("第1条の2", article["source_anchor_ids"])
        self.assertIn("別表第1", appendix["source_anchor_ids"])
        self.assertIn("__TABLE_START__", appendix["text"])
        self.assertIn("項目\t内容", appendix["text"])

    def test_japanese_number_conversion_handles_units(self) -> None:
        self.assertEqual(app_module.japanese_number_to_int("二百四十四"), 244)
        self.assertEqual(app_module.japanese_number_to_int("１２"), 12)
        self.assertIsNone(app_module.japanese_number_to_int("第一"))

    def test_search_terms_are_written_in_bounded_batches(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.batches: list[list[tuple[object, ...]]] = []

            def executemany(self, _sql: str, rows: list[tuple[object, ...]]) -> None:
                self.batches.append(rows)

        cursor = Cursor()
        terms = {f"語{index}": index for index in range(1_201)}

        app_module.insert_search_terms(cursor, "article", 1, 2, 3, terms)

        self.assertEqual([len(batch) for batch in cursor.batches], [500, 500, 201])
        self.assertEqual(sum(len(batch) for batch in cursor.batches), len(terms))

    def test_meili_deletion_reports_actual_deleted_documents(self) -> None:
        responses = [
            {"taskUid": 42},
            {
                "status": "succeeded",
                "details": {"providedIds": 2, "deletedDocuments": 1},
            },
        ]
        with (
            patch.object(app_module, "meili_is_enabled", return_value=True),
            patch.object(app_module, "meili_request", side_effect=responses),
        ):
            deleted = app_module.delete_meili_documents(["a1", "a2"])

        self.assertEqual(deleted, 1)

    def test_reset_meili_index_does_not_hide_delete_failure(self) -> None:
        responses = [
            {"uid": "mine_city_reiki_articles"},
            {"taskUid": 43},
            {
                "status": "failed",
                "error": {"message": "disk full"},
            },
        ]
        with (
            patch.object(app_module, "meili_is_enabled", return_value=True),
            patch.object(app_module, "meili_request", side_effect=responses),
        ):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                app_module.reset_meili_index()


if __name__ == "__main__":
    unittest.main()
