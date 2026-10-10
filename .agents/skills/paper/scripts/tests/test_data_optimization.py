"""Temporary-library regressions for bounded queries and immutable source publication."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import queries, sources
from lib.core import digest, read_json, write_json


class DataOptimization(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def query(self, command, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = queries.main([command, '--root', str(self.root), '--trace', 'test', *args])
        return code, json.loads(output.getvalue())

    def init(self, *args):
        return self.query('init', '--question', 'q', '--scope', 'scope', '--budget', 'bounded', *args)

    def import_items(self, items):
        write_json(self.root / 'raw.json', {'records': items})
        return self.query('import', '--provider', 'manual', '--input', 'raw.json', '--observed-at',
                          '2026-10-10', '--source', 'actual supplied list', '--query', 'actual q')

    def test_status_pending_pages_and_compact_screen_keep_actual_history(self):
        self.init()
        self.import_items([{'doi': f'10.1234/{i}', 'title': str(i), 'abstract': 'a' * 2000} for i in range(27)])
        _, result = self.query('status')
        self.assertNotIn('events', result)
        self.assertEqual(len(result['page']['records']), 20)
        self.assertEqual(result['page']['next_offset'], 20)
        record_id = result['page']['records'][0]['record_id']
        write_json(self.root / 'decisions.json', [{'record_id': record_id, 'status': '排除', 'reason': '实际不符合条件'}])
        _, screened = self.query('screen', '--input', 'decisions.json')
        self.assertEqual(screened['decisions_count'], 1)
        self.assertNotIn('decisions', screened)
        actual = read_json(self.root / screened['event_ref'])
        self.assertEqual(actual['decisions'][0]['reason'], '实际不符合条件')
        _, pending = self.query('status', '--offset', '20')
        self.assertEqual(pending['page']['total'], 26)
        self.assertEqual(len(pending['page']['records']), 6)
        self.assertEqual(read_json(self.root / '.research/queries/test/state.json')['records'][record_id]['screening']['status'], '排除')

    def test_import_budget_no_partial_write_and_scope_does_not_reset_consumption(self):
        self.init('--candidates-budget', '1', '--queries-budget', '1')
        self.import_items([{'doi': '10.1234/a'}])
        before = (self.root / '.research/queries/test/state.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '预算'):
            self.import_items([{'doi': '10.1234/b'}])
        self.assertEqual((self.root / '.research/queries/test/state.json').read_bytes(), before)
        self.query('scope', '--scope', 'scope', '--budget', 'extended', '--reason', '真实增量需求',
                   '--candidates-budget', '2', '--queries-budget', '2')
        _, result = self.import_items([{'doi': '10.1234/b'}])
        self.assertEqual(result['summary']['budget']['used']['queries'], 2)
        self.assertEqual(result['summary']['budget']['used']['candidates'], 2)
        self.assertEqual(result['summary']['report_decisions']['待判断'], 2)

    def test_search_resume_uses_actual_cursor_and_cumulative_budget(self):
        self.init('--candidates-budget', '3', '--pages-budget', '3')
        cursors = []
        def request(root, trace, event, provider, path, params, page, **kwargs):
            cursors.append(params['cursor'])
            count = params['rows']
            raw = json.dumps({'status': 'ok', 'message': {'items': [{'DOI': f'10.1234/{len(cursors)}-{i}'} for i in range(count)],
                               'total-results': 20, 'next-cursor': 'cursor-' + str(len(cursors))}}).encode()
            target = queries._raw_path(root, trace, event, page, 'json')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            return raw, {'raw_path': str(target.relative_to(root)), 'raw_sha256': digest(raw),
                         'observed_at': '2026-10-10', 'error': None, 'parameters': params}
        with patch.object(queries, '_request', side_effect=request):
            _, first = self.query('search', '--provider', 'crossref', '--query', 'actual', '--limit', '2')
            _, second = self.query('search', '--provider', 'crossref', '--query', 'actual', '--resume', first['event']['id'])
            with self.assertRaisesRegex(ValueError, '预算'):
                self.query('search', '--provider', 'crossref', '--query', 'actual')
        self.assertEqual(cursors, ['*', 'cursor-1'])
        self.assertEqual(second['summary']['budget']['used']['candidates'], 3)
        self.assertEqual(len(second['page']['records']), 1)
        actual = read_json(self.root / second['event_ref'])
        self.assertTrue(actual['budget_limited'])
        self.assertEqual(actual['query'], 'actual')
        self.assertFalse(actual['complete_provider_result_set'])

    def test_fulltext_budget_requires_real_immutable_manifest(self):
        self.init('--fulltexts-budget', '0')
        _, result = self.import_items([{'doi': '10.1234/a'}])
        record_id = result['page']['records'][0]['record_id']
        raw = self.root / 'text.txt'; raw.write_text('Actual fixed text')
        source = sources.source(self.root, 'paper-test', raw, 'v1')
        write_json(self.root / 'decisions.json', [{'record_id': record_id, 'status': '纳入', 'reason': 'actual fulltext',
            'fulltext_manifests': [source['manifest']]}])
        with self.assertRaisesRegex(ValueError, '全文'):
            self.query('screen', '--input', 'decisions.json')
        state = queries._load(self.root, 'test')
        self.assertEqual(state['records'][record_id]['screening']['status'], '待判断')
        self.assertEqual(state['budget']['used']['fulltexts'], 0)

    def test_budget_extension_preserves_actual_screening_under_same_criteria(self):
        self.init()
        _, imported = self.import_items([{'doi': '10.1234/a'}])
        record_id = imported['page']['records'][0]['record_id']
        write_json(self.root / 'decisions.json', [{'record_id': record_id, 'status': '纳入', 'reason': 'actual condition'}])
        self.query('screen', '--input', 'decisions.json')
        _, result = self.query('scope', '--scope', 'scope', '--budget', 'extend', '--reason', 'budget only',
                               '--candidates-budget', '600')
        self.assertEqual(result['scope_revision'], 0)
        self.assertEqual(result['summary']['report_decisions']['纳入'], 1)
        self.assertEqual(len(queries._load(self.root, 'test')['budget_history']), 1)

    def test_expand_shares_candidate_budget_and_counts_only_actual_forward_items(self):
        self.init('--candidates-budget', '4', '--pages-budget', '3', '--expansions-budget', '1')
        _, seed = self.import_items([{'openalex': 'W1', 'title': 'actual seed'}])
        record_id = seed['page']['records'][0]['record_id']
        def request(root, trace, event, provider, path, params, page, **kwargs):
            body = {'id': 'https://openalex.org/W1', 'referenced_works': ['https://openalex.org/W2']} if not params else {
                'results': [{'id': 'https://openalex.org/W3'}, {'id': 'https://openalex.org/W4'}],
                'meta': {'count': 20, 'next_cursor': 'actual-next'}}
            raw = json.dumps(body).encode()
            target = queries._raw_path(root, trace, event, page, 'json')
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
            return raw, {'raw_path': str(target.relative_to(root)), 'raw_sha256': digest(raw),
                         'observed_at': '2026-10-10', 'error': None}
        with patch.object(queries, '_request', side_effect=request) as actual_request:
            _, expanded = self.query('expand', '--provider', 'openalex', '--seed', record_id, '--direction', 'both')
            self.assertEqual(actual_request.call_count, 2)
            with self.assertRaisesRegex(ValueError, '预算'):
                self.query('expand', '--provider', 'openalex', '--seed', record_id)
            self.assertEqual(actual_request.call_count, 2)
        budget = expanded['summary']['budget']['used']
        self.assertEqual(budget['candidates'], 4)
        self.assertEqual(budget['pages'], 3)
        self.assertEqual(budget['expansions'], 1)
        actual = read_json(self.root / expanded['event_ref'])
        self.assertEqual(actual['forward_retrieved_records'], 2)
        self.assertEqual(actual['retrieved_records'], 3)
        self.assertEqual(expanded['summary']['report_decisions']['待判断'], 4)

    def pdf(self, name, width=100):
        import pypdf
        path = self.root / name
        writer = pypdf.PdfWriter(); writer.add_blank_page(width=width, height=100)
        with path.open('wb') as stream:
            writer.write(stream)
        return path

    def test_cache_never_extracts_and_conflict_never_overwrites(self):
        pdf = self.pdf('paper.pdf')
        first = sources.source(self.root, 'paper-test', pdf, 'v1')
        with patch.object(sources, 'extract_pdf', side_effect=AssertionError('cache must not parse')):
            second = sources.source(self.root, 'paper-test', pdf, 'v1')
        self.assertEqual(first['manifest'], second['manifest'])
        other = self.pdf('other.pdf', 110)
        with self.assertRaisesRegex(ValueError, '同名版本'):
            sources.source(self.root, 'paper-test', other, 'v1')
        self.assertEqual((self.root / first['source']).read_bytes(), pdf.read_bytes())

    def test_extraction_does_not_hold_shared_source_lock(self):
        slow, fast = self.pdf('slow.pdf'), self.pdf('fast.pdf', 110)
        slow_bytes = slow.read_bytes()
        started, release, completed = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def extract(raw):
            if raw == slow_bytes:
                started.set()
                if not release.wait(5):
                    raise RuntimeError('shared source lock blocked independent parsing')
            return ['actual parsed text']
        def import_slow():
            try:
                sources.source(self.root, 'paper-slow', slow, 'v1')
            except Exception as exc:
                errors.append(exc)
        def import_fast():
            try:
                sources.source(self.root, 'paper-fast', fast, 'v1')
                completed.set()
            except Exception as exc:
                errors.append(exc)
        with patch.object(sources, 'extract_pdf', side_effect=extract):
            worker = threading.Thread(target=import_slow); worker.start()
            self.assertTrue(started.wait(2))
            other = threading.Thread(target=import_fast); other.start()
            free = completed.wait(2)
            release.set(); worker.join(5); other.join(5)
        self.assertTrue(free, 'independent source should publish before slow parsing ends')
        self.assertFalse(errors)

    def test_parallel_misses_cache_hits_and_identical_bytes_are_not_parsed_twice(self):
        a, b = self.pdf('a.pdf'), self.pdf('b.pdf', 110)
        specs = [{'id': 'paper-a', 'file': a.name, 'version': 'v1'},
                 {'id': 'paper-b', 'file': b.name, 'version': 'v1'}]
        result = sources.source_batch(self.root, specs, workers=2)
        self.assertEqual(result['workers'], 2)
        self.assertEqual(result['parsed_files'], 2)
        self.assertEqual(result['errors'], 0)
        with patch.object(sources, 'ProcessPoolExecutor', side_effect=AssertionError('cache must not start pool')), \
             patch.object(sources, 'extract_pdf', side_effect=AssertionError('cache must not parse')):
            cached = sources.source_batch(self.root, specs, workers=2)
        self.assertEqual(cached['cache_hits'], 2)
        self.assertEqual(cached['workers'], 0)
        duplicate = [{'id': 'paper-c', 'file': a.name, 'version': 'v1'},
                     {'id': 'paper-d', 'file': a.name, 'version': 'v1'}]
        with patch.object(sources, 'extract_pdf', return_value=['text']) as extract:
            result = sources.source_batch(self.root, duplicate, workers=2)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(result['parsed_files'], 1)

    def test_fetch_fixed_provenance_cache_skips_network(self):
        raw = self.root / 'a.txt'; raw.write_text('fixed downloaded original')
        imported = sources.source(self.root, 'paper-a', raw, 'v1')
        url = 'https://example.org/a'
        location = self.root / f'.research/provenance/paper-a/{digest(raw.read_bytes())}/{digest(url)}.json'
        write_json(location, {'requested_url': url, 'version': 'v1', 'source_path': imported['source'],
                              'source_sha256': digest(raw.read_bytes())})
        with patch.object(sources, 'urlopen', side_effect=AssertionError('fixed fetch cache should not use network')):
            result = sources.fetch_source(self.root, 'paper-a', url, 'v1')
        self.assertTrue(result['cache_hit'])
        (self.root / imported['source']).write_text('tampered')
        with self.assertRaisesRegex(ValueError, '哈希变化'):
            sources.fetch_source(self.root, 'paper-a', url, 'v1')


if __name__ == '__main__':
    unittest.main()
