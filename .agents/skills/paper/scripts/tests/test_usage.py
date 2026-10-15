"""Actual metering scope, deduplication, missing-data and privacy regressions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.usage import aggregate, compare_reports, read_records, attach_run_metadata, stage_profile, normalize, context_growth_profile


SCOPE = {'source_versions': ['paper-a:v1'], 'question': 'same', 'research_scope': 'read+compare+knowledge+review',
         'model_capabilities': 'observed same capabilities', 'prompt_background': 'same initial context'}

def record(thread='owner', response='r1', **changes):
    result = {'thread_id': thread, 'response_id': response, 'timestamp': '2026-10-10T02:00:00Z',
              'actual_model': 'actual-returned-model', 'requested_model': 'requested-model',
              'actual_effort': 'high', 'usage': {'input_tokens': 100, 'cached_input_tokens': 40,
                                               'output_tokens': 20, 'reasoning_tokens': 10}}
    result.update(changes)
    return result


class Usage(unittest.TestCase):
    def test_context_growth_is_observation_not_handover_instruction(self):
        rows = [normalize(record(response=f'r{i}', timestamp=f'2026-10-10T02:00:{i:02d}Z',
            usage={'input_tokens': amount, 'cached_input_tokens': 0, 'output_tokens': 1}))
            for i, amount in enumerate([100, 110, 120, 130, 180, 190])]
        result = context_growth_profile(rows, window=3)['owner']
        self.assertTrue(result['available'])
        self.assertEqual(result['observed_mean_change'], 57)
        self.assertNotIn('handover', result)

    def test_response_dedup_and_reasoning_cached_subsets(self):
        result = aggregate([record(), record()], expected_threads=['owner'], evaluation_scope=SCOPE, full_tree=True)
        self.assertEqual(result['total_tokens'], 120)
        self.assertEqual(result['duplicate_observations'], 1)
        self.assertEqual(result['response_count'], 1)
        self.assertTrue(result['coverage_complete'])
        self.assertNotEqual(result['records'][0]['requested_model'], result['records'][0]['actual_model'])

    def test_cumulative_snapshots_become_deltas_without_adding_receipts(self):
        first = record(response=None, measurement_scope='cumulative', meter_id='host',
                       timestamp='2026-10-10T01:00:00Z')
        second = record(response=None, measurement_scope='cumulative', meter_id='host',
                        usage={'input_tokens': 200, 'cached_input_tokens': 80, 'output_tokens': 50, 'reasoning_tokens': 20})
        receipt = record(timestamp='2026-10-10T00:30:00Z')
        result = aggregate([first, second, receipt])
        self.assertEqual(result['total_tokens'], 250)
        self.assertEqual(result['covered_measurements_excluded'], 1)
        self.assertEqual(len(result['records']), 2)

    def test_tree_aggregate_excludes_only_proven_contained_children(self):
        parent = record(thread='root', response='tree', measurement_scope='tree_total',
                        covered_thread_ids=['root', 'owner'], coverage_end='2026-10-10T03:00:00Z',
                        usage={'input_tokens': 300, 'cached_input_tokens': 90, 'output_tokens': 100, 'reasoning_tokens': 40})
        child = record()
        result = aggregate([parent, child], expected_threads=['root', 'owner'], evaluation_scope='complete same scope', full_tree=True)
        self.assertEqual(result['total_tokens'], 400)
        self.assertEqual(result['covered_measurements_excluded'], 1)
        self.assertTrue(result['coverage_complete'])
        parent.pop('coverage_end')
        ambiguous = aggregate([parent, child], expected_threads=['root', 'owner'], full_tree=True)
        self.assertFalse(ambiguous['coverage_complete'])
        self.assertEqual(ambiguous['total_tokens'], 120)
        self.assertEqual(len(ambiguous['unresolved_aggregates']), 1)
        self.assertTrue(any(item['kind'] == 'ambiguous_tree_overlap' for item in ambiguous['diagnostics']))

    def test_missing_usage_or_thread_never_counts_as_zero_or_pass(self):
        baseline = aggregate([record()], expected_threads=['owner'], evaluation_scope=SCOPE, full_tree=True)
        partial = aggregate([record(usage={'input_tokens': 10})], expected_threads=['owner', 'assistant'],
                            evaluation_scope=SCOPE, full_tree=True)
        self.assertIsNone(partial['totals']['output_tokens']['observed'])
        self.assertEqual(partial['unavailable_threads'], ['assistant'])
        self.assertEqual(compare_reports(baseline, partial)['fourfold_cost_gate'], 'unavailable')
        subset = aggregate([record()], evaluation_scope=SCOPE)
        self.assertEqual(compare_reports(baseline, subset)['fourfold_cost_gate'], 'unavailable')

    def test_complete_scope_ratio_includes_cached_and_requires_matched_scope(self):
        baseline = aggregate([record()], expected_threads=['owner'], evaluation_scope=SCOPE, full_tree=True)
        reduced = record(usage={'input_tokens': 20, 'cached_input_tokens': 10, 'output_tokens': 10, 'reasoning_tokens': 5})
        current = aggregate([reduced], expected_threads=['owner'], evaluation_scope=SCOPE, full_tree=True)
        comparison = compare_reports(baseline, current)
        self.assertEqual(comparison['token_ratio'], .25)
        self.assertTrue(comparison['fourfold_cost_gate'])
        self.assertEqual(comparison['fourfold_overall'], 'unavailable')
        current['evaluation_scope'] = 'different research scope'
        self.assertEqual(compare_reports(baseline, current)['fourfold_cost_gate'], 'unavailable')

    def test_codex_jsonl_never_exports_conversation_or_invents_actual_model(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session.jsonl'
            events = [{'type': 'session_meta', 'payload': {'id': 'real-thread'}},
                      {'type': 'turn_context', 'payload': {'model': 'requested-model', 'effort': 'high'}},
                      {'type': 'response_item', 'payload': {'type': 'message', 'content': 'PRIVATE TEXT'}},
                      {'type': 'event_msg', 'timestamp': '2026-10-10T01:00:00Z', 'payload': {'type': 'token_count',
                        'info': {'total_token_usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 20,
                                                       'reasoning_output_tokens': 10}}}},
                      {'type': 'event_msg', 'timestamp': '2026-10-10T02:00:00Z', 'payload': {'type': 'token_count',
                        'info': {'total_token_usage': {'input_tokens': 150, 'cached_input_tokens': 30, 'output_tokens': 40,
                                                       'reasoning_output_tokens': 15}}}}]
            path.write_text('\n'.join(json.dumps(event) for event in events))
            records, diagnostics = read_records([path])
            report = aggregate(records, diagnostics=diagnostics)
        self.assertEqual(report['total_tokens'], 190)
        self.assertIsNone(report['response_count'])
        self.assertEqual(report['actual_model_unavailable'], 2)
        self.assertEqual(report['records'][0]['requested_model'], 'requested-model')
        self.assertNotIn('PRIVATE TEXT', json.dumps(report))
        self.assertNotIn(str(path), json.dumps(report))

    def test_codex_response_receipts_replace_snapshots_and_select_one_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session.jsonl'
            events = [
                {'type': 'session_meta', 'payload': {'id': 'root-thread'}},
                {'type': 'turn_context', 'payload': {'turn_id': 'other-turn', 'model': 'requested'}},
                {'type': 'token_usage_record', 'timestamp': '2026-10-10T00:00:00Z', 'payload': {
                    'thread_id': 'root-thread', 'turn_id': 'other-turn', 'response_id': 'other-response',
                    'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 2},
                    'turn_token_usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 2}}},
                {'type': 'turn_context', 'payload': {'turn_id': 'paper-turn', 'model': 'requested'}},
                {'type': 'response_item', 'payload': {'content': 'PRIVATE RESEARCH BODY'}},
                {'type': 'token_usage_record', 'timestamp': '2026-10-10T01:00:00Z', 'payload': {
                    'thread_id': 'root-thread', 'turn_id': 'paper-turn', 'response_id': 'paper-r1',
                    'usage': {'input_tokens': 100, 'cached_input_tokens': 40, 'output_tokens': 20},
                    'turn_token_usage': {'input_tokens': 100, 'cached_input_tokens': 40, 'output_tokens': 20}}},
                {'type': 'token_usage_record', 'timestamp': '2026-10-10T02:00:00Z', 'payload': {
                    'thread_id': 'root-thread', 'turn_id': 'paper-turn', 'response_id': 'paper-r2',
                    'usage': {'input_tokens': 150, 'cached_input_tokens': 100, 'output_tokens': 30},
                    'turn_token_usage': {'input_tokens': 250, 'cached_input_tokens': 140, 'output_tokens': 50}}},
                {'type': 'event_msg', 'timestamp': '2026-10-10T02:00:00Z', 'payload': {'type': 'token_count',
                    'info': {'total_token_usage': {'input_tokens': 260, 'cached_input_tokens': 140,
                                                   'output_tokens': 52}}}},
            ]
            path.write_text('\n'.join(json.dumps(event) for event in events))
            records, diagnostics = read_records([path], selected_turn_ids={'root-thread': ['paper-turn']})
            report = aggregate(records, expected_threads=['root-thread'], full_tree=True,
                               diagnostics=diagnostics)
        self.assertEqual(report['total_tokens'], 300)
        self.assertEqual(report['response_count'], 2)
        self.assertTrue(report['coverage_complete'])
        self.assertEqual(report['totals']['cached_input_tokens']['observed'], 140)
        self.assertNotIn('PRIVATE RESEARCH BODY', json.dumps(report))

    def test_action_profile_uses_tool_identity_without_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session.jsonl'
            events = [
                {'type': 'session_meta', 'payload': {'id': 'root-thread'}},
                {'type': 'turn_context', 'payload': {'turn_id': 'paper-turn'}},
                {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'wait_agent',
                    'call_id': 'c1', 'arguments': 'PRIVATE WAIT ARGUMENTS'}},
                {'type': 'token_usage_record', 'payload': {'thread_id': 'root-thread',
                    'turn_id': 'paper-turn', 'response_id': 'r1',
                    'usage': {'input_tokens': 100, 'cached_input_tokens': 90, 'output_tokens': 10},
                    'turn_token_usage': {'input_tokens': 100, 'cached_input_tokens': 90, 'output_tokens': 10}}},
                {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'list_agents',
                    'call_id': 'c2', 'arguments': 'PRIVATE STATUS ARGUMENTS'}},
                {'type': 'token_usage_record', 'payload': {'thread_id': 'root-thread',
                    'turn_id': 'paper-turn', 'response_id': 'r2',
                    'usage': {'input_tokens': 120, 'cached_input_tokens': 110, 'output_tokens': 12},
                    'turn_token_usage': {'input_tokens': 220, 'cached_input_tokens': 200, 'output_tokens': 22}}},
            ]
            path.write_text('\n'.join(json.dumps(event) for event in events))
            rows, diagnostics = read_records([path])
            report = aggregate(rows, diagnostics=diagnostics)
        self.assertEqual(report['action_profile']['wait']['input_tokens'], 100)
        self.assertEqual(report['action_profile']['status']['responses'], 1)
        self.assertEqual(rows[0]['call_id'], 'c1')
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_receipt_missing_turn_total_blocks_complete_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session.jsonl'
            path.write_text(json.dumps({'type': 'token_usage_record', 'payload': {
                'thread_id': 'owner', 'turn_id': 'paper-turn', 'response_id': 'r1',
                'usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 10}}}))
            records, diagnostics = read_records([path])
            report = aggregate(records, expected_threads=['owner'], full_tree=True,
                               diagnostics=diagnostics)
        self.assertFalse(report['coverage_complete'])
        self.assertIn({'kind': 'receipt_turn_total_unavailable', 'turn_id': 'paper-turn'}, diagnostics)

    def test_workflow_metadata_uses_actual_exact_dispatch_and_never_guesses_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / '.research/work/real-run/workflow.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'events': [
                {'event_type': 'actual_host_call', 'agent_id': 'owner', 'unit': 'paper-a',
                 'actual_model': 'observed-model', 'actual_effort': 'high', 'stage': 'R1'},
                {'event_type': 'internal_checkpoint', 'agent_id': 'unbound', 'actual_model': 'not-real'}]}))
            records = [record(actual_model=None, actual_effort=None), record(thread='unbound', response='r2', actual_model=None)]
            from lib.usage import normalize
            records = [normalize(value) for value in records]
            metadata = attach_run_metadata(root, 'real-run', records)
        self.assertEqual(records[0]['actual_model'], 'observed-model')
        self.assertEqual(records[0]['unit'], 'paper-a')
        self.assertIsNone(records[0]['stage'])
        self.assertIsNone(records[1]['actual_model'])
        self.assertEqual(metadata['registered_actual_actors'], ['owner'])

    def test_missing_actual_model_blocks_fourfold_comparison(self):
        baseline = aggregate([record()], expected_threads=['owner'], evaluation_scope=SCOPE, full_tree=True)
        current = aggregate([record(actual_model=None)], expected_threads=['owner'], evaluation_scope=SCOPE, full_tree=True)
        result = compare_reports(baseline, current)
        self.assertEqual(result['fourfold_cost_gate'], 'unavailable')
        self.assertIn('actual_model_unavailable', result['reasons'])
        current = dict(baseline, evaluation_scope='same string is insufficient scope evidence')
        result = compare_reports(current, current)
        self.assertEqual(result['fourfold_cost_gate'], 'unavailable')
        self.assertIn('comparison_scope_dimensions_unavailable', result['reasons'])

    def test_helper_receipts_are_used_without_inventing_actual_routing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / '.research/work/probe/workflow.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'runtime': {'capabilities': {'root_agent_id': 'root'},
                'helpers': {'h': {'agent_id': 'helper', 'actual_record': 'observed spawn',
                    'unit': 'paper-a', 'status': 'completed', 'requested_model': 'cheap',
                    'actual_model': None, 'actual_effort': None}}}}))
            from lib.usage import normalize
            rows = [normalize(record(thread='helper', actual_model=None, actual_effort=None, requested_model=None))]
            metadata = attach_run_metadata(root, 'probe', rows)
        self.assertEqual(metadata['registered_actual_actors'], ['helper'])
        self.assertEqual(metadata['root_agent_id'], 'root')
        self.assertEqual(rows[0]['requested_model'], 'cheap')
        self.assertIsNone(rows[0]['actual_model'])

    def test_full_tree_manifest_cannot_omit_registered_helper_or_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / '.research/work/probe/workflow.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'runtime': {'capabilities': {'root_agent_id': 'root'},
                'helpers': {'h': {'agent_id': 'helper', 'actual_record': 'observed spawn',
                    'unit': 'paper-a', 'status': 'completed'}}}}))
            (root / 'actual.json').write_text(json.dumps([record()]))
            (root / 'scope.json').write_text(json.dumps({'full_tree': True, 'expected_threads': ['owner'],
                'evaluation_scope': SCOPE}))
            from lib.usage import main
            result = main(['--root', str(root), '--run', 'probe', '--input', str(root / 'actual.json'),
                           '--scope-manifest', 'scope.json'])
        self.assertFalse(result['coverage_complete'])
        self.assertIn({'kind': 'registered_tree_actor_omitted', 'actors': ['helper', 'root']}, result['diagnostics'])

    def test_stage_profile_marks_transition_traffic_unattributed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / '.research/work/probe/workflow.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'units': {'paper-a': {'owner_history': [{'agent_id': 'owner'}]}},
                'events': [{'event': 'internal_checkpoint', 'operation': 'begin', 'unit': 'paper-a',
                            'stage': 'R1', 'at': '2026-10-10T01:00:00Z'},
                           {'event': 'scientific_checkpoint', 'unit': 'paper-a', 'stage': 'R1',
                            'at': '2026-10-10T02:00:00Z'}]}))
            inside_stage = record(timestamp='2026-10-10T01:30:00Z')
            transition = record(response='r2', timestamp='2026-10-10T02:30:00Z')
            rows = [normalize(row) for row in (inside_stage, transition)]
            result = stage_profile(root, 'probe', rows, {})
        self.assertEqual(result['buckets']['paper-a/R1']['responses'], 1)
        self.assertEqual(result['buckets']['paper-a/transition']['responses'], 1)

    def test_usage_main_returns_compact_dict_for_research_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'actual.json'
            path.write_text(json.dumps([record()]))
            from lib.usage import main
            result = main(['--root', str(root), '--input', str(path)])
        self.assertIsInstance(result, dict)
        self.assertEqual(result['total_tokens'], 120)
        self.assertNotIn('records', result)

    def test_conflicting_response_or_reset_prevents_cost_certification(self):
        a, b = record(), record(usage={'input_tokens': 1, 'output_tokens': 1})
        report = aggregate([a, b], expected_threads=['owner'], evaluation_scope=SCOPE, full_tree=True)
        self.assertFalse(report['coverage_complete'])
        self.assertTrue(any(item['kind'] == 'conflicting_duplicate' for item in report['diagnostics']))
        first = record(response=None, measurement_scope='cumulative', meter_id='host', timestamp='2026-10-10T01:00:00Z')
        second = record(response=None, measurement_scope='cumulative', meter_id='host',
                        usage={'input_tokens': 10, 'output_tokens': 1})
        report = aggregate([first, second], expected_threads=['owner'], evaluation_scope=SCOPE, full_tree=True)
        self.assertFalse(report['coverage_complete'])
        self.assertTrue(any(item['kind'] == 'cumulative_reset' for item in report['diagnostics']))


if __name__ == '__main__':
    unittest.main()
