import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import evidence
from lib.core import digest, write_json


class EvidenceIndexTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / '附件/paper-a/v1.pdf'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'fixed-pdf-bytes')
        self.manifest = '.research/sources/paper-a/fixed/manifest.json'
        manifest = self.root / self.manifest
        manifest.parent.mkdir(parents=True)
        write_json(manifest, {'paper_id': 'paper-a', 'source_path': '附件/paper-a/v1.pdf',
                              'source_sha256': digest(source.read_bytes()), 'source_kind': 'pdf',
                              'blocks': [{'page': 1, 'text': 'alpha beta gamma',
                                          'text_sha256': digest('alpha beta gamma')}]})
        state = self.root / '.research/work/probe/workflow.json'
        state.parent.mkdir(parents=True)
        write_json(state, {'units': {'paper-a': {'inputs': [
            {'path': self.manifest, 'sha256': digest(manifest.read_bytes())}]}}})
        self.spec = '.research/work/probe/windows.json'
        write_json(self.root / self.spec, [{'id': 'alpha', 'manifest': self.manifest, 'page': 1,
                                           'start': 0, 'end': 5, 'purpose': 'check input'}])

    def test_selective_get_and_no_source_text_in_index(self):
        result = evidence.build(self.root, 'probe', 'paper-a', self.spec)
        self.assertEqual(result['entries'], 1)
        path = self.root / result['index']
        self.assertNotIn('alpha beta gamma', path.read_text())
        retrieved = evidence.get(self.root, 'probe', 'paper-a', 'alpha')
        self.assertEqual(retrieved['results'][0]['text'], 'alpha')
        self.assertEqual(retrieved['results'][0]['path'], '附件/paper-a/v1.pdf#page=1')

    def test_detects_source_change_and_unbound_manifest(self):
        evidence.build(self.root, 'probe', 'paper-a', self.spec)
        (self.root / '附件/paper-a/v1.pdf').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '原文哈希改变'):
            evidence.get(self.root, 'probe', 'paper-a', 'alpha')
        windows = json.loads((self.root / self.spec).read_text())
        windows[0]['manifest'] = '.research/sources/other.json'
        write_json(self.root / self.spec, windows)
        with self.assertRaisesRegex(ValueError, '绑定'):
            evidence.build(self.root, 'probe', 'paper-a', self.spec)

    def test_rejects_oversized_or_invented_window(self):
        windows = json.loads((self.root / self.spec).read_text())
        windows[0]['end'] = 999
        write_json(self.root / self.spec, windows)
        with self.assertRaisesRegex(ValueError, '区间'):
            evidence.build(self.root, 'probe', 'paper-a', self.spec)
