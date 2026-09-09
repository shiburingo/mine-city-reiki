import hashlib
from pathlib import Path
import unittest

from meeting_minutes.pdf_extractor import extract_pdf_from_bytes


class PdfDependencyCompatibilityTests(unittest.TestCase):
    def test_plain_and_aes_pdf_keep_text_and_page_contract(self):
        expected = (
            "Synthetic council minutes 2026\n"
            "Trout farm dependency compatibility test."
        )
        for name in ("minutes-plain.pdf", "minutes-aes128.pdf"):
            with self.subTest(name=name):
                payload = (Path(__file__).parent / "fixtures" / name).read_bytes()
                document = extract_pdf_from_bytes(payload)
                self.assertEqual(document.sha256, hashlib.sha256(payload).hexdigest())
                self.assertEqual(document.page_count, 1)
                self.assertEqual(document.text, expected)
                self.assertEqual(document.pages[0].page, 1)
                self.assertEqual(len(document.pages[0].lines), 2)
                self.assertTrue(document.pages[0].words)


if __name__ == "__main__":
    unittest.main()
