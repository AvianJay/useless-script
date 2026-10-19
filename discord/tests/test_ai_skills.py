import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_skills import ResourceStore, SkillRegistry, extract_attachment, skill_session


class SkillRegistryTests(unittest.TestCase):
    def test_bundled_skills_have_discriminating_metadata_and_readable_references(self):
        registry = SkillRegistry()
        self.assertEqual(set(registry.skills), {
            'browser', 'web-research', 'discord-investigate', 'file-reader', 'data-analysis', 'threads-posting',
        })
        for skill in registry.skills.values():
            self.assertTrue(skill.description)
            self.assertTrue(skill.instructions)
        self.assertTrue(registry.read_reference('browser', 'references/repl.md').strip())

    def test_reference_cannot_escape_skill_or_read_non_markdown(self):
        registry = SkillRegistry()
        for value in ('../../ai.py', '../data-analysis/SKILL.md', '/etc/passwd', '.env', 'references/.env.md', 'references/../../../config.json'):
            with self.subTest(path=value), self.assertRaises(ValueError):
                registry.read_reference('browser', value)


class ResourceTests(unittest.TestCase):
    def test_resources_are_scoped_to_one_request_and_read_in_slices(self):
        first, second = {}, {}
        resource = skill_session(first).resources.add('abcdef', filename='table.csv')
        self.assertEqual(skill_session(first).resources.read(resource.resource_id, offset=1, limit=3)['text'], 'bcd')
        self.assertEqual(skill_session(first).resources.read(resource.resource_id, offset=1, limit=3)['next_offset'], 4)
        with self.assertRaises(ValueError):
            skill_session(second).resources.get(resource.resource_id)

    def test_storage_budget_counts_utf8_bytes_and_rejects_overflow(self):
        store = ResourceStore()
        with patch('ai_skills.MAX_RESOURCE_BYTES', 6):
            store.add('中文')
            with self.assertRaises(ValueError):
                store.add('x')

    def test_last_slice_marks_end_and_does_not_repeat_content(self):
        store = ResourceStore()
        resource = store.add('a' * 20000)
        first = store.read(resource.resource_id, limit=99999)
        last = store.read(resource.resource_id, offset=first['next_offset'])
        self.assertEqual(first['text'] + last['text'], resource.text)
        self.assertIsNone(last['next_offset'])


class AttachmentExtractionTests(unittest.TestCase):
    def test_text_encoding_and_structured_data_survive_extraction(self):
        for filename, content, data in (
            ('data.csv', 'name,value\n"a,b",2\n', b'name,value\n"a,b",2\n'),
            ('data.json', '{"value": 2}', b'{"value": 2}'),
            ('note.txt', '中文', '中文'.encode('utf-16')),
            ('note.txt', '中文', '中文'.encode('cp950')),
        ):
            with self.subTest(filename=filename):
                text, _, truncated = extract_attachment(data, filename)
                self.assertEqual(text, content)
                self.assertFalse(truncated)

    def test_secrets_binary_and_unsupported_formats_are_rejected(self):
        for filename, data in (('.env', b'x=y'), ('.env.production', b'x=y'), ('private.key', b'key'),
                               ('file.txt', b'\x00\x01'), ('file.exe', b'code')):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                extract_attachment(data, filename)

    def test_docx_paragraphs_are_extracted_without_executing_fields(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p><w:p><w:r><w:t>World</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract_attachment(output.getvalue(), 'document.docx')[0], 'Hello\nWorld')

    def test_xlsx_preserves_formula_text_without_evaluating_it(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(['value', 'calculation'])
        book.active.append([2, '=A2*3'])
        output = io.BytesIO()
        book.save(output)
        text, _, _ = extract_attachment(output.getvalue(), 'table.xlsx')
        self.assertIn('=A2*3', text)
        self.assertIn('[Sheet:', text)

    def test_pdf_encryption_is_reported_and_blank_pages_remain_visible(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        output = io.BytesIO()
        writer.write(output)
        self.assertIn('[Page 1]', extract_attachment(output.getvalue(), 'file.pdf')[0])
        writer.encrypt('test-password')
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaisesRegex(ValueError, 'Encrypted'):
            extract_attachment(output.getvalue(), 'file.pdf')


if __name__ == '__main__':
    unittest.main()
