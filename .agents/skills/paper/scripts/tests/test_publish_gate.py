"""Research-bound proposals cannot bypass candidate/version review gates."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import digest, read_json, write_json
from lib.notes import check
from lib.publishing import import_edits, publish, stage, stage_drafts


def note(body):
    return '---\nid: k\nkind: knowledge\ntags: []\nanalysis: complete\nupdated: 2026-10-10\n---\n\n# K\n\n' + body + '\n'


class PublishGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = '知识/K.md'
        self.draft = '.research/drafts/probe/units/k/candidate.md'
        (self.root / '知识').mkdir()
        path = self.root / self.draft
        path.parent.mkdir(parents=True)
        path.write_text(note('Located condition. ^claim-k'))
        write_json(self.root / '.research/work/probe/workflow.json', {
            'schema_version': 2, 'run_id': 'probe', 'units': {'k': {'id': 'k', 'rounds': []}}})

    def mapping(self):
        return {'mapping': {self.target: {'target': self.target, 'draft': self.draft,
                                         'sha256': digest((self.root / self.draft).read_bytes())}}}

    def test_unreviewed_workflow_rejected_at_stage_and_publish(self):
        with patch('lib.workflow.candidates', side_effect=ValueError('final_review missing')):
            with self.assertRaisesRegex(ValueError, 'final_review'):
                stage_drafts(self.root, {self.target: self.draft}, 'blocked', 'probe')
        with patch('lib.workflow.candidates', return_value=self.mapping()):
            result = stage_drafts(self.root, {self.target: self.draft}, 'p', 'probe')
        with patch('lib.workflow.candidates', side_effect=ValueError('final_review stale')):
            with self.assertRaisesRegex(ValueError, 'final_review'):
                publish(self.root, 'p', result['digest'], 'fixture user authorization')
        self.assertFalse((self.root / self.target).exists())

    def test_changes_cannot_substitute_different_scientific_content(self):
        with patch('lib.workflow.candidates', return_value=self.mapping()):
            with self.assertRaisesRegex(ValueError, '不同于'):
                stage(self.root, {self.target: note('Different condition.')}, 'blocked', 'probe')

    def test_reviewed_same_bytes_publish_and_keep_origin(self):
        with patch('lib.workflow.candidates', return_value=self.mapping()):
            result = stage(self.root, {self.target: (self.root / self.draft).read_text()}, 'p', 'probe')
            publish(self.root, 'p', result['digest'], 'fixture user authorization')
        record = read_json(self.root / '.research/drafts/p/proposal.json')
        self.assertEqual(record['origins'][self.target]['draft'], self.draft)
        self.assertEqual((self.root / self.target).read_bytes(), (self.root / self.draft).read_bytes())

    def test_user_edits_preserved_but_scientific_binding_invalidated(self):
        with patch('lib.workflow.candidates', return_value=self.mapping()):
            result = stage_drafts(self.root, {self.target: self.draft}, 'p', 'probe')
        preview = self.root / result['previews'][self.target]
        preview.write_text(preview.read_text().replace('Located condition.', 'User changed condition.'))
        revised = import_edits(self.root, 'p')
        self.assertTrue(revised['imported'])
        current = read_json(self.root / '.research/drafts/p/proposal.json')
        self.assertIsNone(current['scientific_binding'])
        self.assertIn('User changed condition.', current['entries'][0]['content'])
        with patch('lib.workflow.candidates', return_value=self.mapping()):
            with self.assertRaises(ValueError):
                publish(self.root, 'p', revised['digest'], 'fixture authorization')
        self.assertFalse((self.root / self.target).exists())


class MechanicalCache(unittest.TestCase):
    def test_hidden_link_target_changes_invalidate_cached_anchor_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '知识').mkdir()
            hidden = root / '.research/evidence.md'
            hidden.parent.mkdir()
            hidden.write_text('# Located\nEvidence\n')
            (root / '知识/K.md').write_text(note('[Evidence](../.research/evidence.md#Located)'))
            first = check(root)
            self.assertEqual(first['errors'], 0)
            self.assertEqual(check(root)['mechanical_cache'], 'hit')
            hidden.write_text('# Different\nEvidence\n')
            changed = check(root)
            self.assertEqual(changed['mechanical_cache'], 'miss')
            self.assertGreater(changed['errors'], 0)

    def test_corrupt_cache_is_recomputed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            check(root)
            cached = next((root / '.research/cache/checks').glob('*.json'))
            cached.write_text('{broken')
            self.assertEqual(check(root)['mechanical_cache'], 'miss')
            self.assertEqual(check(root)['mechanical_cache'], 'hit')

    def test_snapshot_cache_is_invalidated_by_incoming_link_edit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '知识').mkdir()
            (root / '知识/K.md').write_text(note('Condition. ^claim-k'))
            first = check(root)
            self.assertEqual(first['mechanical_cache'], 'miss')
            self.assertEqual(check(root)['mechanical_cache'], 'hit')
            (root / '知识/K.md').write_text(note('[[Missing]] ^claim-k'))
            result = check(root)
            self.assertEqual(result['mechanical_cache'], 'miss')
            self.assertGreater(result['errors'], 0)


if __name__ == '__main__':
    unittest.main()
