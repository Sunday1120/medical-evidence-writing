"""使用合成文件与模拟响应验证辅助脚本，不访问真实医学数据或网络。"""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest = load_module('make_source_manifest')
download = load_module('download_papers')


def reference(identifier='src-a', number=1, **extra):
    item = {'id': identifier, 'title': '合成资料', 'type': 'paper',
            'used_for': ['C1'], 'citation_number': number}
    item.update(extra)
    if number is None:
        item.pop('citation_number')
    return item


class Response(io.BytesIO):
    def __init__(self, body, content_type='application/pdf', length=None):
        super().__init__(body)
        self.headers = {'Content-Type': content_type}
        if length is not None:
            self.headers['Content-Length'] = str(length)


class ManifestTests(unittest.TestCase):
    def test_default_has_no_preview_or_absolute_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '材料.txt').write_text('合成患者信息', encoding='utf-8')
            result = manifest.make_manifest(root)
            self.assertEqual(result['file_count'], 1)
            self.assertNotIn('preview', result['files'][0])
            self.assertNotIn(str(root), json.dumps(result, ensure_ascii=False))
            self.assertNotIn('source_folder', result)

    def test_gb18030_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '中文.txt'
            path.write_bytes('中文编码测试'.encode('gb18030'))
            result = manifest.make_manifest(Path(folder), preview_chars=4)
            self.assertEqual(result['files'][0]['preview'], '中文编码')
            self.assertEqual(result['files'][0]['preview_encoding'], 'gb18030')

    def test_utf8_bom_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '中文.txt'
            path.write_bytes('测试'.encode('utf-8-sig'))
            result = manifest.make_manifest(Path(folder), preview_chars=20)
            self.assertEqual(result['files'][0]['preview'], '测试')

    def test_csv_and_tsv_are_spreadsheets(self):
        self.assertEqual(manifest.classify(Path('data.csv')), 'spreadsheet')
        self.assertEqual(manifest.classify(Path('data.tsv')), 'spreadsheet')

    def test_excludes_outputs_secrets_and_git(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'source.txt').write_text('素材', encoding='utf-8')
            (root / 'manifest.json').write_text('{}')
            (root / '.env').write_text('合成配置', encoding='utf-8')
            (root / '.git').mkdir()
            (root / '.git' / 'config').write_text('合成配置', encoding='utf-8')
            result = manifest.make_manifest(root, exclude_paths=[root / 'manifest.json'])
            self.assertEqual([x['relative_path'] for x in result['files']], ['source.txt'])

    def test_limit_marks_incomplete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ['a.txt', 'b.txt']:
                (root / name).write_text(name)
            result = manifest.make_manifest(root, max_files=1)
            self.assertEqual(result['file_count'], 1)
            self.assertTrue(result['truncated'])

    def test_cli_does_not_index_previous_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data.txt').write_text('素材', encoding='utf-8')
            output = root / 'list.json'
            args = [sys.executable, str(ROOT / 'scripts/make_source_manifest.py'),
                    str(root), '--output', str(output)]
            for _ in range(2):
                result = subprocess.run(args, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['file_count'], 1)

    def test_missing_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                manifest.make_manifest(Path(folder) / 'missing')


class DownloadTests(unittest.TestCase):
    def test_numbered_sources_sorted_evidence_only_last(self):
        refs = [reference('b', 2), reference('evidence', None), reference('a', 1)]
        self.assertEqual([r['id'] for r in download.validate_references(refs)],
                         ['a', 'b', 'evidence'])

    def test_duplicate_ids_numbers_and_number_gaps_rejected(self):
        cases = [[reference(), reference(number=2)],
                 [reference(), reference('b')], [reference(number=2)]]
        for refs in cases:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                download.validate_references(refs)

    def test_invalid_metadata_rejected(self):
        for extra in [{'citation_number': True}, {'used_for': []}, {'title': ''},
                      {'type': 'unknown'}, {'type': []}, {'access': []},
                      {'pdf_url': 'file:///private.pdf'},
                      {'pdf_url': 'https://user:password@example.org/file.pdf'}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                download.validate_references([reference(**extra)])

    def test_no_implicit_network_lookup(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(download, 'urlopen') as request:
            report = download.download_all([reference(doi='10.0000/synthetic')], Path(folder))
            request.assert_not_called()
            self.assertEqual(report['results'][0]['status'], 'no_pdf_url')
            self.assertEqual(download.report_exit_code(report), 1)

    def test_missing_access_declaration_does_not_download(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(download, 'urlopen') as request:
            report = download.download_all([reference(pdf_url='https://example.org/a.pdf')], Path(folder))
            request.assert_not_called()
            self.assertEqual(report['results'][0]['status'], 'access_unconfirmed')

    def test_mime_type_alone_does_not_accept_html(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'a.pdf'
            with patch.object(download, 'urlopen', return_value=Response(b'<html>error</html>')):
                ok, _ = download.fetch_pdf('https://example.org/a.pdf', target, 1024)
            self.assertFalse(ok)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_existing_file_survives_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'a.pdf'
            target.write_bytes(b'original')
            with patch.object(download, 'urlopen') as request:
                ok, _ = download.fetch_pdf('https://example.org/a.pdf', target, 1024)
            request.assert_not_called()
            self.assertFalse(ok)
            self.assertEqual(target.read_bytes(), b'original')

    def test_download_size_limit_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'a.pdf'
            with patch.object(download, 'urlopen', return_value=Response(b'%PDF-' + b'x' * 100)):
                ok, _ = download.fetch_pdf('https://example.org/a.pdf', target, 20)
            self.assertFalse(ok)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_success_requires_pdf_signature_and_records_sorted_number(self):
        with tempfile.TemporaryDirectory() as folder:
            refs = [reference('b', 2), reference('a', 1)]
            for ref in refs:
                ref.update(pdf_url='https://example.org/a.pdf', access='open-access')
            with patch.object(download, 'urlopen', side_effect=lambda *a, **k: Response(b'%PDF-1.4\nsynthetic')):
                report = download.download_all(refs, Path(folder))
            self.assertEqual(report['downloaded_count'], 2)
            self.assertEqual(download.report_exit_code(report), 0)
            self.assertEqual([r['citation_number'] for r in report['results']], [1, 2])
            self.assertTrue((Path(folder) / '001-a.pdf').exists())

    def test_existing_file_is_not_reported_as_verified_download(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / '001-src-a.pdf').write_bytes(b'old')
            refs = [reference(pdf_url='https://example.org/a.pdf', access='open-access')]
            with patch.object(download, 'urlopen') as request:
                report = download.download_all(refs, Path(folder))
            request.assert_not_called()
            self.assertEqual(report['results'][0]['status'], 'exists_unverified')
            self.assertEqual(download.report_exit_code(report), 1)

    def test_cli_failure_has_nonzero_exit_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'refs.json'
            source.write_text(json.dumps({'references': [reference()]}), encoding='utf-8')
            output = root / 'papers'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/download_papers.py'),
                                     str(source), '--output-dir', str(output)], capture_output=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue((output / 'download_report.json').exists())


if __name__ == '__main__':
    unittest.main()
