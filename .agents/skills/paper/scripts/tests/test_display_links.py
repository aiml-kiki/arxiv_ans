"""Obsidian links must survive validation and the review/publication round trip."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.notes import (block_anchors, source_target, table_render_issues,
                       validate_link, wiki_parts, wiki_target)
from lib.publishing import rewrite_wiki
from lib.semantics import _refs


class DisplayLinks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for folder in ('论文', '知识', '专题', '附件', '审阅/r0001'):
            (self.root / folder).mkdir(parents=True)
        self.paper = self.root / '论文/A.md'
        self.paper.write_text('# A\n\n## 方法\n\n原有判断。 ^claim-a\n')
        self.concept = self.root / '知识/前提.md'
        self.concept.write_text('# 前提\n\n## 条件\n\n有条件判断。 ^claim-boundary\n')
        self.pdf = self.root / '附件/v1.pdf'
        self.pdf.write_bytes(b'fixture; no PDF extraction in link validation')
        (self.root / '专题/资料库.base').write_text('views:\n  - type: table\n    name: 最近编辑\n')

    def test_plain_and_table_aliases_resolve_to_the_same_heading_and_block(self):
        for fragment in ('#条件', '#^claim-boundary'):
            for divider in ('|', r'\|'):
                destination = '知识/前提' + fragment + divider + '核对边界'
                self.assertEqual(wiki_target(self.root, self.paper, destination), self.concept)
                validate_link(self.root, self.paper, destination, {}, wiki=True)
        validate_link(self.root, self.paper, r'#方法\|看方法', {}, wiki=True)
        with self.assertRaisesRegex(ValueError, '页内定位不存在'):
            validate_link(self.root, self.paper, r'知识/前提#不存在\|查条件', {}, wiki=True)

    def test_pdf_physical_page_and_base_view_aliases(self):
        validate_link(self.root, self.paper, r'附件/v1.pdf#page=2\|原文',
                      {self.pdf: 2}, wiki=True)
        with self.assertRaisesRegex(ValueError, 'PDF页码无效'):
            validate_link(self.root, self.paper, r'附件/v1.pdf#page=3\|原文',
                          {self.pdf: 2}, wiki=True)
        validate_link(self.root, self.paper, r'专题/资料库.base#最近编辑\|继续阅读', {}, wiki=True)
        with self.assertRaisesRegex(ValueError, 'Base视图不存在'):
            validate_link(self.root, self.paper, r'专题/资料库.base#不存在\|入口', {}, wiki=True)

    def test_review_round_trip_preserves_alias_escaping_and_referenced_scope(self):
        content = ('[[知识/前提#^claim-boundary|查边界]]\n'
                   '| 条件 | [[知识/前提#条件\\|查条件]] |\n'
                   '| 本页 | [[#方法\\|看方法]] |\n'
                   '```text\n[[知识/前提|不应改代码示例]]\n```\n')
        preview = self.root / '审阅/r0001/01-A.md'
        concept_preview = self.root / '审阅/r0001/02-前提.md'
        mapping = {self.paper: preview, self.concept: concept_preview}
        projected = rewrite_wiki(self.root, content, self.paper, mapping)
        self.assertIn(r'[[审阅/r0001/02-前提#条件\|查条件]]', projected)
        self.assertIn('[[审阅/r0001/02-前提#^claim-boundary|查边界]]', projected)
        self.assertIn(r'[[审阅/r0001/01-A#方法\|看方法]]', projected)
        self.assertIn('[[知识/前提|不应改代码示例]]', projected)
        restored = rewrite_wiki(self.root, projected, preview, {v: k for k, v in mapping.items()})
        self.assertIn(r'[[知识/前提#条件\|查条件]]', restored)
        self.assertIn('[[知识/前提#^claim-boundary|查边界]]', restored)
        self.assertIn(r'[[论文/A#方法\|看方法]]', restored)
        self.assertEqual(table_render_issues(restored), [])

    def test_table_rejects_every_unescaped_pipe_in_a_wiki_link(self):
        self.assertEqual(table_render_issues(r'| 条件 | [[知识/前提\|解释]] |'), [])
        for link in (r'[[知识/前提|解释]]', r'[[知识/前提\\|解释]]',
                     r'[[知识/前提\|解释|另一个词]]'):
            self.assertTrue(table_render_issues('| 条件 | ' + link + ' |'))

    def test_link_fragments_are_not_block_id_declarations(self):
        body = ('判断。 [[知识/前提#^claim-boundary|查条件]]\n\n'
                '另一个判断。 [证据](../知识/前提.md#^claim-boundary)\n\n'
                '实际命题。 ^claim-real\n')
        self.assertEqual(block_anchors(body), (['claim-real'], []))
        anchors, issues = block_anchors('误放^claim-bad\n\n命题。 ^claim-中文\n')
        self.assertEqual(anchors, [])
        self.assertEqual(len(issues), 2)
        anchors, issues = block_anchors('命题。 ^claim-before [[知识/前提]]\n')
        self.assertEqual(anchors, [])
        self.assertTrue(issues)

    def test_alias_support_does_not_unescape_paths_or_escape_the_vault(self):
        self.assertEqual(wiki_parts(r'知识/前提#条件\|解释'), ('知识/前提#条件', r'\|', '解释'))
        self.assertEqual(wiki_parts(r'知识\前提\|解释')[0], r'知识\前提')
        with self.assertRaises(ValueError):
            wiki_target(self.root, self.paper, r'../outside\|解释')

    def test_source_property_and_semantic_references_share_alias_rules(self):
        self.assertEqual(source_target(self.root, r'[[附件/v1.pdf\|固定来源]]'), self.pdf)
        for divider in ('|', r'\|'):
            refs = _refs('[[知识/前提#^claim-boundary' + divider + '查边界]]', '论文/A.md')
            self.assertEqual(refs, ['知识/前提.md#^claim-boundary'])


if __name__ == '__main__':
    unittest.main()
