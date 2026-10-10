"""Host records and concurrent reservation invariants, without spawning an LLM."""
from copy import deepcopy
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import digest, utc_now, write_json
from lib import runtime


def reserve_process(root, ident, queue):
    try:
        result = runtime.main(['--root', root, 'reserve', '--run', 'probe',
                               '--unit', ident, '--agent-id', '/root/' + ident,
                               '--event', ident + '.json'])
        queue.put(('accepted', result['helper']))
    except ValueError as exc:
        queue.put(('rejected', str(exc)))


class Runtime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source.md'
        source.write_text('# Fixed\nA statement.\n')
        self.state = {'schema_version': 2, 'run_id': 'probe', 'units': {}}
        for ident in ('a', 'b'):
            self.state['units'][ident] = {'id': ident, 'owner': {'mode': 'agent', 'agent_id': '/root/' + ident},
                'owner_active': True, 'inputs': [{'path': 'source.md', 'sha256': digest(source.read_bytes())}],
                'depends_on': [], 'rounds': [], 'output_dir': '.research/work/probe/units/' + ident + '/results'}
        route = runtime.routing()['profiles']['audit']
        self.route = route
        runtime.configure(self.state, {'observed': True, 'actual_record': 'fixture host capabilities',
            'root_agent_id': '/root', 'active_limit_including_root': 4, 'open_subagent_limit': None,
            'spawn_model': True, 'spawn_effort': True, 'fork_none': True, 'nested_delegation': True,
            'available_models': [route['model']], 'close_thread': False})

    def event(self, helper='check-a'):
        return {'observed': True, 'actual_record': 'fixture full host tree', 'observed_at': utc_now(),
            'active_agent_ids': ['/root', '/root/a', '/root/b'], 'open_agent_ids': ['/root/a', '/root/b'],
            'helper': helper, 'profile': 'audit', 'question': 'Check one statement',
            'consumer': 'R2c decision', 'acceptance': 'Located answer with limits',
            'benefit_reason': 'Large source window would otherwise repeat in owner turns',
            'source_refs': [{'path': 'source.md', 'locator': '# Fixed'}], 'token_budget': 5000}

    def reserve_bind(self):
        reply = runtime.reserve(self.root, self.state, 'a', '/root/a', self.event())
        event = {'helper': reply['helper'], 'observed': True, 'actual_record': 'fixture spawn receipt',
                 'mode': 'spawn', 'parent_agent_id': '/root/a', 'agent_id': '/root/a/check',
                 'fork_turns': 'none'}
        return runtime.transition(self.root, self.state, 'a', '/root/a', 'bind', event)

    def complete(self, thread_status='open', answer='Supported with condition'):
        helper = self.state['runtime']['helpers']['check-a']
        write_json(self.root / helper['result_path'], {'answer': answer, 'limitations': 'One source only',
            'evidence': [{'path': 'source.md', 'locator': '# Fixed'}]})
        return runtime.transition(self.root, self.state, 'a', '/root/a', 'complete',
            {'helper': 'check-a', 'observed': True, 'actual_record': 'fixture final message',
             'host_status': 'completed', 'agent_id': '/root/a/check', 'thread_status': thread_status})

    def test_two_owners_share_one_helper_reservation(self):
        result = runtime.reserve(self.root, self.state, 'a', '/root/a', self.event())
        self.assertEqual(result['mode'], 'spawn')
        self.assertEqual(result['fork_turns'], 'none')
        with self.assertRaisesRegex(ValueError, '全树'):
            runtime.reserve(self.root, self.state, 'b', '/root/b', self.event('check-b'))

    def test_failed_spawn_can_release_only_reservation(self):
        runtime.reserve(self.root, self.state, 'a', '/root/a', self.event())
        runtime.transition(self.root, self.state, 'a', '/root/a', 'cancel',
                           {'helper': 'check-a', 'reason': 'Host quota rejected spawn'})
        runtime.reserve(self.root, self.state, 'b', '/root/b', self.event('check-b'))

    def test_same_owner_and_actual_call_are_required(self):
        with self.assertRaisesRegex(ValueError, 'owner'):
            runtime.reserve(self.root, self.state, 'a', '/root/b', self.event())
        runtime.reserve(self.root, self.state, 'a', '/root/a', self.event())
        event = {'helper': 'check-a', 'observed': False}
        with self.assertRaisesRegex(ValueError, 'observed'):
            runtime.transition(self.root, self.state, 'a', '/root/a', 'bind', event)

    def test_missing_actual_model_is_not_claimed_verified(self):
        result = self.reserve_bind()
        self.assertFalse(result['route_verified'])
        with self.assertRaisesRegex(ValueError, 'cancel'):
            runtime.transition(self.root, self.state, 'a', '/root/a', 'cancel', {'helper': 'check-a', 'reason': 'stop'})

    def test_full_history_is_not_a_cross_model_spawn(self):
        runtime.reserve(self.root, self.state, 'a', '/root/a', self.event())
        with self.assertRaisesRegex(ValueError, 'fork_turns'):
            runtime.transition(self.root, self.state, 'a', '/root/a', 'bind', {
                'helper': 'check-a', 'observed': True, 'actual_record': 'fixture', 'mode': 'spawn',
                'parent_agent_id': '/root/a', 'agent_id': '/root/a/check', 'fork_turns': 'all'})

    def test_host_outside_project_and_open_threads_are_counted(self):
        event = self.event()
        event['active_agent_ids'].append('/root/unrelated')
        with self.assertRaisesRegex(ValueError, '全树'):
            runtime.reserve(self.root, self.state, 'a', '/root/a', event)
        self.state['runtime']['capabilities']['open_subagent_limit'] = 2
        with self.assertRaisesRegex(ValueError, '打开子线程'):
            runtime.reserve(self.root, self.state, 'a', '/root/a', self.event())

    def test_completed_is_not_closed_and_preserves_version_on_reuse(self):
        self.reserve_bind()
        first = self.complete()
        first_ref = deepcopy(first['result_ref'])
        saved = (self.root / first_ref['path']).read_bytes()
        self.assertEqual(first['thread_status'], 'open')
        reply = runtime.reserve(self.root, self.state, 'a', '/root/a', self.event())
        self.assertEqual(reply['mode'], 'followup')
        runtime.transition(self.root, self.state, 'a', '/root/a', 'bind', {
            'helper': 'check-a', 'observed': True, 'actual_record': 'fixture followup',
            'parent_agent_id': '/root/a', 'agent_id': '/root/a/check', 'mode': 'followup'})
        self.complete(answer='A later bounded result')
        self.assertEqual((self.root / first_ref['path']).read_bytes(), saved)
        helper = self.state['runtime']['helpers']['check-a']
        self.assertEqual(len(helper['history']), 1)
        self.assertNotIn('history', helper['history'][0])

    def test_changed_source_and_stale_capacity_are_rejected(self):
        event = self.event()
        event['observed_at'] = '2020-01-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            runtime.reserve(self.root, self.state, 'a', '/root/a', event)
        (self.root / 'source.md').write_text('changed')
        with self.assertRaises(ValueError):
            runtime.reserve(self.root, self.state, 'a', '/root/a', self.event())

    def test_source_change_during_helper_execution_blocks_acceptance(self):
        self.reserve_bind()
        (self.root / 'source.md').write_text('Changed during execution')
        with self.assertRaisesRegex(ValueError, '执行期间改变'):
            self.complete()
        self.assertEqual(self.state['runtime']['helpers']['check-a']['status'], 'bound')

    def test_accepted_science_can_still_check_before_final_delivery(self):
        unit = self.state['units']['a']
        unit['owner_active'] = False
        unit['rounds'] = [{'id': 'R4', 'status': 'accepted', 'accepted': None}]
        self.reserve_bind()
        self.complete()
        unit['final_report'] = {'path': 'final.json'}
        with self.assertRaisesRegex(ValueError, 'owner未真实'):
            runtime.reserve(self.root, self.state, 'a', '/root/a', self.event('later'))

    def test_parallel_processes_cannot_reserve_same_last_slot(self):
        self.state['run_id'] = 'probe'
        write_json(self.root / '.research/work/probe/workflow.json', self.state)
        for ident in ('a', 'b'):
            write_json(self.root / (ident + '.json'), self.event('check-' + ident))
        context = multiprocessing.get_context('fork')
        queue = context.Queue()
        processes = [context.Process(target=reserve_process, args=(str(self.root), ident, queue))
                     for ident in ('a', 'b')]
        try:
            for process in processes:
                process.start()
            results = [queue.get(timeout=10) for _ in processes]
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sorted(value[0] for value in results), ['accepted', 'rejected'])
            self.assertIn('全树', next(value[1] for value in results if value[0] == 'rejected'))
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            queue.close()


if __name__ == '__main__':
    unittest.main()
