"""State and scientific-version invariants for the authorized optimization plan."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import workflow as w
from lib.core import digest, write_json


def note(kind, body="scientific content"):
    return f"---\nid: example\nkind: {kind}\ntags: []\nanalysis: complete\nupdated: 2026-10-10\n---\n\n# Example\n\n{body}\n"


class WorkflowOptimization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for folder in ("论文", "知识", "专题"):
            (self.root / folder).mkdir()
        self.put("source.md", "fixed scientific source")
        source = {"source_kind": "text", "source_path": "source.md", "source_sha256": digest("fixed scientific source"),
                  "page_count": 0, "section_count": 1, "version": "v1", "paper_id": "example", "parser": {},
                  "blocks": [{"id": "b1", "section": 1, "text": "fixed scientific source", "text_sha256": digest("fixed scientific source")}]}
        self.manifest = ".research/sources/example/source.json"
        self.put(self.manifest, json.dumps(source))

    def put(self, path, content):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        return dest

    def plan(self, kind="read", units=None):
        return {"goal": "understand fixed mechanism", "units": units or [
            {"id": "u", "kind": kind, "inputs": [self.manifest] if kind == "read" else ["source.md"],
             "target": {"read": "论文/A.md", "knowledge": "知识/A.md", "trace": "专题/A.md", "compare": "专题/A.md"}.get(kind)}]}

    def init(self, kind="read"):
        state = w.initialize(self.root, "probe", self.plan(kind))
        event = {"observed": True, "mode": "spawn", "agent_id": "actual-owner", "tool": "collaboration.spawn_agent", "call_id": "actual-call"}
        w.start_owner(self.root, state, "u", event)
        return state

    def finding(self, final=False):
        value = {"reviewer": "actual-reviewer" if final else "actual-owner", "review_kind": "final_review" if final else "self_check",
                 "summary": "checked decisive source and scope", "checks": [
                     {"question": "Does the source support this boundary?", "finding": "source matches under named assumptions", "locator": "source.md and scientific draft"}]}
        if final:
            value.update(decision="accept", actual_review_record="actual main session source/candidate review")
        return value

    def prepare(self, state):
        unit = state["units"]["u"]
        stage = w.current_round(unit)
        pending = {key: {"status": "selected", "reason": "source.md decisive contribution requires this criterion"}
                   for key in stage["tasks"] if unit["tasks"][key]["status"] == "pending"}
        if pending:
            w.decide_tasks(state, "u", pending)
        w.owner_step(self.root, state, "u", "actual-owner", "begin", None)

    def payload(self, state, transaction=None):
        unit = state["units"]["u"]
        stage = w.current_round(unit)
        draft = self.root / unit["science_draft"]
        original = draft.read_text() if draft.exists() else "# Scientific draft\n"
        self.put(unit["science_draft"], original + f"\n## {stage['id']}\nReal source-grounded explanation of {stage['id']}.\n")
        value = {"transaction_id": transaction or stage["id"].lower(), "stage": stage["id"], "draft_sha256": digest(draft.read_bytes()),
                 "delta": [{"locator": f"## {stage['id']}", "summary": "Added condition and interface backed by source", "task_ids": w.selected_tasks(unit, stage)}],
                 "evidence": [{"path": "source.md", "locator": "section 1"}],
                 "coverage": [{"path": "source.md", "locator": "section 1", "mode": "read", "section": 1}],
                 "judgment_changes": [], "gaps": [], "finding": self.finding(),
                 "science_state": {"question": "What operation changes behavior?", "accepted_boundary": stage["id"], "interfaces": ["## R2a"],
                                   "critical_details": ["source condition"], "unresolved": [], "next_action": "prepare next question"}}
        if stage["base"] == w.FINAL[unit["kind"]]:
            self.put(unit["draft"], note(unit["candidate_kind"]))
            value["candidates"] = [{"draft": unit["draft"], "target": unit["target"]}]
        return value

    def finish_unit(self, state):
        while w.current_round(state["units"]["u"]):
            self.prepare(state)
            w.checkpoint(self.root, state, "u", "actual-owner", self.payload(state))
        w.final_report(self.root, state, "u", {"agent_id": "actual-owner", "conclusions": ["Mechanism applies under fixed conditions"], "unresolved": [], "checks": []})

    def test_start_prepares_and_inputs_pause_without_host_restart(self):
        state = self.init()
        unit = state["units"]["u"]
        self.assertEqual(w.current_round(unit)["status"], "preparing")
        self.put("old-note.md", "real prior note")
        w.refresh_inputs(self.root, state, "u", ["old-note.md"], "composition prior snapshot")
        self.assertIn(self.manifest, unit["declared_input_paths"])
        self.prepare(state)
        w.owner_step(self.root, state, "u", "actual-owner", "awaiting-input", {"reason": "missing ablation limits claim"})
        self.assertTrue(unit["owner_active"])
        self.put("ablation.md", "extra decisive source")
        w.refresh_inputs(self.root, state, "u", ["ablation.md"], "new actual ablation")
        self.assertEqual(w.current_round(unit)["status"], "preparing")
        self.assertEqual(len([event for event in state["events"] if event.get("event_type") == "actual_host_call"]), 1)
        self.prepare(state)
        w.checkpoint(self.root, state, "u", "actual-owner", self.payload(state))

    def test_checkpoint_failure_atomic_replay_idempotent_and_ordered(self):
        state = self.init()
        self.prepare(state)
        payload = self.payload(state)
        before = deepcopy(state)
        bad = deepcopy(payload); bad["finding"]["reviewer"] = "wrong-owner"
        with self.assertRaises(ValueError):
            w.checkpoint(self.root, state, "u", "actual-owner", bad)
        self.assertEqual(state, before)
        bad = deepcopy(payload); bad["stage"] = "R2a"
        with self.assertRaises(ValueError):
            w.checkpoint(self.root, state, "u", "actual-owner", bad)
        self.assertEqual(state, before)
        bad = deepcopy(payload); bad["coverage"][0]["mode"] = "pretend-read"
        with self.assertRaises(ValueError):
            w.checkpoint(self.root, state, "u", "actual-owner", bad)
        self.assertEqual(state, before)
        result = w.checkpoint(self.root, state, "u", "actual-owner", payload)
        accepted = deepcopy(state)
        replay = w.checkpoint(self.root, state, "u", "actual-owner", payload)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(state, accepted)
        self.assertEqual(result["next_stage"], "R2a")
        bad = deepcopy(payload); bad["gaps"] = ["changed"]
        with self.assertRaises(ValueError):
            w.checkpoint(self.root, state, "u", "actual-owner", bad)

    def test_single_draft_all_read_boundaries_keep_old_versions(self):
        state = self.init()
        self.finish_unit(state)
        unit = state["units"]["u"]
        self.assertEqual([stage["id"] for stage in unit["rounds"] if stage["accepted"]], ["R1", "R2a", "R2b", "R2c", "R3a", "R3b", "R4"])
        first = unit["rounds"][0]["accepted"]
        for stage in unit["rounds"]:
            self.assertNotIn(unit["science_draft"], [item["path"] for item in stage["accepted"]["files"]])
            self.assertEqual(stage["accepted"]["answers"], [])
        self.assertNotIn("## R2a", (self.root / first["files"][0]["path"]).read_text())
        self.assertFalse((self.root / unit["output_dir"]).exists())
        w.prerequisite_records(self.root, state, unit)
        w.final_report(self.root, state, "u", finding=self.finding(final=True))
        self.assertIn("论文/A.md", w.candidates(self.root, state)["mapping"])

    def test_opt_in_pass_checkpoint_preserves_substep_order_and_sources(self):
        plan = self.plan()
        plan['units'][0]['checkpoint_mode'] = 'pass_trial'
        state = w.initialize(self.root, 'probe', plan)
        w.start_owner(self.root, state, 'u', {'observed': True, 'mode': 'spawn',
            'agent_id': 'actual-owner', 'tool': 'collaboration.spawn_agent', 'call_id': 'actual-call'})
        unit = state['units']['u']
        self.assertEqual([item['id'] for item in unit['rounds']], ['R1', 'R2', 'R3', 'R4'])
        self.prepare(state)
        w.checkpoint(self.root, state, 'u', 'actual-owner', self.payload(state))
        unit = state['units']['u']
        self.assertEqual(w.current_round(unit)['id'], 'R2')
        self.assertEqual(w.next_packet(self.root, state, 'u')['substep'], 'R2a')
        self.prepare(state)
        with self.assertRaisesRegex(ValueError, '内部问题尚未'):
            w.checkpoint(self.root, state, 'u', 'actual-owner', self.payload(state))
        for substep_id in ['R2a', 'R2b', 'R2c']:
            unit = state['units']['u']
            stage = w.current_round(unit)
            current = w.active_substep(stage)
            self.assertEqual(current['id'], substep_id)
            decisions = {key: {'status': 'selected', 'reason': 'fixed source requires this mechanism check'}
                         for key in current['tasks'] if unit['tasks'][key]['status'] == 'pending'}
            if decisions:
                w.decide_tasks(state, 'u', decisions)
            draft = self.root / unit['science_draft']
            self.put(unit['science_draft'], (draft.read_text() if draft.exists() else '# Science\n') +
                     f'\n## {substep_id}\nSource-bound analysis.\n')
            payload = {'substep': substep_id, 'draft_sha256': digest(draft.read_bytes()),
                'delta': [{'locator': f'## {substep_id}', 'summary': 'specific mechanism and source',
                           'task_ids': w.selected_tasks(unit, stage)}],
                'evidence': [{'path': 'source.md', 'locator': 'section 1'}], 'finding': self.finding()}
            result = w.substep_complete(self.root, state, 'u', 'actual-owner', payload)
            self.assertEqual(result['accepted_substep'], substep_id)
            with self.assertRaisesRegex(ValueError, '只能记录当前内部问题|只用于已begin'):
                w.substep_complete(self.root, state, 'u', 'actual-owner', payload)
        self.assertEqual(w.next_packet(self.root, state, 'u')['action'], 'checkpoint_current_pass')
        w.checkpoint(self.root, state, 'u', 'actual-owner', self.payload(state))
        unit = state['units']['u']
        self.assertEqual(w.current_round(unit)['id'], 'R3')
        self.assertEqual(w.next_packet(self.root, state, 'u')['substep'], 'R3a')
        self.assertEqual(len([event for event in state['events'] if event.get('event') == 'scientific_checkpoint']), 2)
        repeated = w.repeat_round(state, 'u', 'R2', 'check a second fixed mechanism case')
        self.assertEqual(repeated['added_round'], 'R2-2')
        self.assertEqual([step['status'] for step in w.find_round(unit, 'R2-2')['substeps']], ['ready'] * 3)
        w.reopen(state, 'u', 'R2', 'source interpretation changed')
        self.assertEqual([step['status'] for step in w.find_round(unit, 'R2')['substeps']], ['ready'] * 3)
        self.assertEqual(w.next_packet(self.root, state, 'u')['substep'], 'R2a')

    def test_pass_trial_explicitly_skips_inapplicable_substep(self):
        plan = self.plan()
        plan['units'][0]['checkpoint_mode'] = 'pass_trial'
        state = w.initialize(self.root, 'probe', plan)
        w.start_owner(self.root, state, 'u', {'observed': True, 'mode': 'spawn',
            'agent_id': 'actual-owner', 'tool': 'collaboration.spawn_agent', 'call_id': 'actual-call'})
        self.prepare(state)
        w.checkpoint(self.root, state, 'u', 'actual-owner', self.payload(state))
        w.decide_tasks(state, 'u', {key: {'status': 'not_applicable',
            'reason': 'fixed source contains no such operation'} for key in ['B2', 'B3']})
        w.owner_step(self.root, state, 'u', 'actual-owner', 'begin', None)
        with self.assertRaisesRegex(ValueError, '显式记录not_applicable'):
            w.substep_complete(self.root, state, 'u', 'actual-owner', {'substep': 'R2a'})
        result = w.substep_complete(self.root, state, 'u', 'actual-owner',
                                    {'substep': 'R2a', 'decision': 'not_applicable'})
        self.assertEqual(result['next_substep'], 'R2b')
        self.assertEqual(w.next_packet(self.root, state, 'u')['substep'], 'R2b')

    def test_pass_trial_reaches_final_candidate_without_skipping_third_pass(self):
        plan = self.plan()
        plan['units'][0]['checkpoint_mode'] = 'pass_trial'
        state = w.initialize(self.root, 'probe', plan)
        w.start_owner(self.root, state, 'u', {'observed': True, 'mode': 'spawn',
            'agent_id': 'actual-owner', 'tool': 'collaboration.spawn_agent', 'call_id': 'actual-call'})
        while w.current_round(state['units']['u']):
            self.prepare(state)
            while True:
                unit = state['units']['u']
                stage = w.current_round(unit)
                substep = w.active_substep(stage)
                if substep is None:
                    break
                draft = self.root / unit['science_draft']
                self.put(unit['science_draft'], (draft.read_text() if draft.exists() else '# Science\n') +
                         f'\n## {substep["id"]}\nSource-bound finding.\n')
                w.substep_complete(self.root, state, 'u', 'actual-owner', {
                    'substep': substep['id'], 'draft_sha256': digest(draft.read_bytes()),
                    'delta': [{'locator': f'## {substep["id"]}', 'summary': 'source-bound inference',
                               'task_ids': w.selected_tasks(unit, stage)}],
                    'evidence': [{'path': 'source.md', 'locator': 'section 1'}],
                    'finding': self.finding()})
            w.checkpoint(self.root, state, 'u', 'actual-owner', self.payload(state))
        unit = state['units']['u']
        self.assertEqual([stage['id'] for stage in unit['rounds'] if stage['accepted']],
                         ['R1', 'R2', 'R3', 'R4'])
        self.assertEqual([step['status'] for step in w.find_round(unit, 'R3')['substeps']],
                         ['accepted', 'accepted'])
        w.final_report(self.root, state, 'u',
                       {'agent_id': 'actual-owner', 'conclusions': ['Mechanism under fixed conditions'],
                        'unresolved': [], 'checks': []}, finding=self.finding(final=True))
        self.assertIn('论文/A.md', w.candidates(self.root, state)['mapping'])

    def test_final_checkpoint_keeps_delivery_window_until_final_report(self):
        state = self.init()
        while w.current_round(state['units']['u']):
            self.prepare(state)
            w.checkpoint(self.root, state, 'u', 'actual-owner', self.payload(state))
        unit = state['units']['u']
        self.assertTrue(unit['owner_active'])
        self.assertTrue(w.owner_running(unit))
        state['runtime'] = {'helpers': {'probe': {'unit': 'u', 'status': 'reserved'}}}
        report = {'agent_id': 'actual-owner', 'conclusions': ['Under fixed conditions'], 'unresolved': [], 'checks': []}
        with self.assertRaisesRegex(ValueError, '助手仍有'):
            w.final_report(self.root, state, 'u', report)
        state['runtime']['helpers']['probe']['status'] = 'cancelled'
        w.final_report(self.root, state, 'u', report)
        self.assertFalse(unit['owner_active'])

    def publication_fixture(self):
        # This full publication path uses a knowledge note, not a fake paper
        # without its mandatory fixed source metadata.
        (self.root / self.manifest).unlink()
        state = self.init('knowledge')
        self.finish_unit(state)
        w.final_report(self.root, state, 'u', finding=self.finding(final=True))
        group = w.candidates(self.root, state, 'Fixture final group and knowledge impact check')
        write_json(self.root / '.research/work/probe/workflow.json', state)
        from lib.publishing import stage_drafts, publish
        proposal = stage_drafts(self.root, {target: value['draft'] for target, value in group['mapping'].items()},
                                'proposal', 'probe')
        publish(self.root, 'proposal', proposal['digest'], 'Explicit fixture authorization')
        return state

    def test_review_publish_finish_accepts_published_bytes_and_archives_versions(self):
        state = self.publication_fixture()
        # Pre-publication candidates cannot be reused after their target changed;
        # finish instead verifies the exact approved proposal and commit bytes.
        with self.assertRaises(ValueError):
            w.candidates(self.root, state)
        from lib.publishing import finish
        result = finish(self.root, 'probe')
        self.assertTrue(result['removed'])
        self.assertTrue((self.root / '知识/A.md').is_file())
        self.assertFalse((self.root / '.research/work/probe').exists())

    def test_postpublication_user_edit_blocks_cleanup_and_keeps_work(self):
        self.publication_fixture()
        self.put('知识/A.md', note('knowledge', 'User changed this after publication'))
        from lib.publishing import finish
        with self.assertRaisesRegex(ValueError, '偏离最新批准稿'):
            finish(self.root, 'probe')
        self.assertTrue((self.root / '.research/work/probe/workflow.json').is_file())

    def test_unknown_persistent_record_blocks_cleanup_without_overwrite(self):
        self.publication_fixture()
        self.put('.research/records/runs/probe/reports/unbound.json', '{"unknown":true}')
        from lib.publishing import finish
        with self.assertRaisesRegex(ValueError, '未绑定记录'):
            finish(self.root, 'probe')
        self.assertTrue((self.root / '.research/work/probe/workflow.json').is_file())
        self.assertTrue((self.root / '.research/records/runs/probe/reports/unbound.json').is_file())



    def test_candidate_final_review_cannot_be_bypassed_or_reused(self):
        state = self.init()
        self.finish_unit(state)
        for finding in (None, "integration looks good"):
            with self.assertRaises(ValueError):
                w.candidates(self.root, state, finding)
        bad = self.finding(final=True); bad.pop("actual_review_record")
        with self.assertRaises(ValueError):
            w.final_report(self.root, state, "u", finding=bad)
        bad = self.finding(final=True); bad["decision"] = "revise"; bad["required_changes"] = ["tighten mechanism"]
        w.final_report(self.root, state, "u", finding=bad)
        with self.assertRaises(ValueError):
            w.candidates(self.root, state)
        w.final_report(self.root, state, "u", finding=self.finding(final=True))
        w.candidates(self.root, state)
        unit = state["units"]["u"]
        self.put(unit["draft"], note("paper", "external user edit"))
        with self.assertRaises(ValueError):
            w.candidates(self.root, state)

    def test_replacement_reopens_only_affected_science_and_preserves_evidence(self):
        state = self.init()
        self.prepare(state)
        w.checkpoint(self.root, state, "u", "actual-owner", self.payload(state))
        first = deepcopy(state["units"]["u"]["rounds"][0]["accepted"])
        self.prepare(state)
        self.put("new.md", "new premise source")
        w.refresh_inputs(self.root, state, "u", [self.manifest, "new.md"], "premise changed", "R2a", "changed-premise")
        self.assertEqual(state["units"]["u"]["rounds"][0]["accepted"], first)
        self.assertEqual(w.current_round(state["units"]["u"])["status"], "preparing")
        self.put("source.md", "replaced physical source")
        replacement = json.loads((self.root / self.manifest).read_text())
        replacement["source_sha256"] = digest("replaced physical source")
        replacement["blocks"][0].update(text="replaced physical source", text_sha256=digest("replaced physical source"))
        self.put(self.manifest, json.dumps(replacement))
        with self.assertRaises(ValueError):
            w.refresh_inputs(self.root, state, "u", [self.manifest], "silently replace", mode="append")
        w.refresh_inputs(self.root, state, "u", [self.manifest], "actual replacement affects R2a", "R2a", "replace")
        w.prerequisite_records(self.root, state, state["units"]["u"])
        self.assertEqual((self.root / first["evidence_files"][0]["path"]).read_text(), "fixed scientific source")

    def test_checkpoint_continuation_prepares_only_next_question_and_replay_is_quiet(self):
        state = self.init()
        first = w.owner_step(self.root, state, 'u', 'actual-owner', 'prepare')
        self.assertEqual(first['status'], 'preparing')
        self.assertIn('resources', first)
        self.assertFalse(w.current_round(state['units']['u']).get('executions'))
        self.prepare(state)
        payload = self.payload(state)
        result = w.checkpoint(self.root, state, 'u', 'actual-owner', payload)
        continuation = result['continuation']
        self.assertEqual(continuation['stage'], 'R2a')
        self.assertEqual(continuation['status'], 'preparing')
        self.assertFalse(w.current_round(state['units']['u']).get('executions'))
        self.assertFalse(any(item.get('kind') == 'template' for item in continuation['resources']))
        source = next(item for item in continuation['resources'] if item.get('ref') == 'protocol-0')
        self.assertEqual(source['status'], 'unchanged')  # R1 already delivered this source section.
        accepted = deepcopy(state)
        replay = w.checkpoint(self.root, state, 'u', 'actual-owner', payload)
        self.assertTrue(replay['idempotent_replay'])
        self.assertNotIn('continuation', replay)
        self.assertEqual(accepted, state)

    def test_continuation_resource_failure_does_not_partially_accept(self):
        state = self.init()
        self.prepare(state)
        payload = self.payload(state)
        before = deepcopy(state)
        with patch.object(w, 'resource_bundle', side_effect=ValueError('broken current guide')):
            with self.assertRaisesRegex(ValueError, 'broken current guide'):
                w.checkpoint(self.root, state, 'u', 'actual-owner', payload)
        self.assertEqual(state, before)

    def test_continuation_preserves_all_capability_boundaries(self):
        for kind in ['read', 'compare', 'trace', 'knowledge']:
            with self.subTest(kind=kind):
                if kind == 'compare':
                    self.put('other.md', 'second fixed paper input')
                    plan = self.plan(kind)
                    plan['units'][0]['inputs'].append('other.md')
                    state = w.initialize(self.root, 'probe', plan)
                    w.start_owner(self.root, state, 'u', {'observed': True, 'mode': 'spawn',
                        'agent_id': 'actual-owner', 'tool': 'collaboration.spawn_agent', 'call_id': 'actual-call'})
                else:
                    state = self.init(kind)
                expected = [stage['id'] for stage in state['units']['u']['rounds'] if stage['status'] != 'skipped']
                seen = []
                while w.current_round(state['units']['u']):
                    self.prepare(state)
                    stage = w.current_round(state['units']['u'])
                    seen.append(stage['id'])
                    reply = w.checkpoint(self.root, state, 'u', 'actual-owner', self.payload(state))
                    current = w.current_round(state['units']['u'])
                    if current:
                        self.assertEqual(reply['continuation']['stage'], current['id'])
                        self.assertEqual(current['status'], 'preparing')
                        self.assertFalse(current.get('executions'))
                    else:
                        self.assertEqual(reply['continuation']['action'], 'report_final_result')
                self.assertEqual(seen, expected)

    def test_small_packages_resource_dedup_and_explicit_refresh(self):
        state = self.init()
        one = w.bootstrap(self.root, state, "u")
        self.assertEqual(w.bootstrap(self.root, state, "u")["status"], "unchanged")
        self.assertNotIn("sources", one)
        packet = w.next_packet(self.root, state, "u")
        for field in ("accepted", "accepted_other_refs", "result_contract", "sources", "fixed_inputs", "capacity"):
            self.assertNotIn(field, packet)
        resource = w.resource_packet(state, "u", "guide", 0, [])
        self.assertIn("content", resource)
        self.assertEqual(resource["content_bytes"], len(resource["content"].encode("utf-8")))
        self.assertEqual(state["units"]["u"]["resource_reads"][next(iter(state["units"]["u"]["resource_reads"]))]["stage"], "R1")
        unchanged = w.resource_packet(state, "u", "guide", 0, [])
        self.assertEqual(unchanged["status"], "unchanged")
        self.assertEqual(unchanged["content_bytes"], resource["content_bytes"])
        self.assertIn("content", w.resource_packet(state, "u", "guide", 0, [], refresh=True))
        delivered = next(row for row in state['units']['u']['resource_reads'].values()
                         if row['path'] == resource['path'])
        self.assertEqual(delivered['deliveries'], 2)
        self.assertEqual(delivered['delivered_bytes_total'], 2 * resource['content_bytes'])
        composition_refs = w.protocol_refs(state["units"]["u"], {"base": "R4"}, True)
        writing = next(ref for ref in composition_refs if ref["path"].endswith("/writing.md"))
        obsidian = next(ref for ref in composition_refs if ref["path"].endswith("/obsidian.md"))
        self.assertNotIn("栏目职责与科学承载", writing["sections"])
        self.assertNotIn("原生视图和讨论", obsidian["sections"])
        self.put("source.md", "unregistered source change")
        self.assertIn("已固定文件变动", w.recovery(self.root, state, "u")["units"]["u"]["integrity"])

    def test_every_declared_protocol_section_resolves(self):
        for kind, stages in w.STAGES.items():
            for stage_id, _, _ in stages:
                refs = w.protocol_refs({"kind": kind}, {"base": stage_id}, stage_id == w.FINAL[kind])
                for ref in refs:
                    with self.subTest(kind=kind, stage=stage_id, path=ref["path"]):
                        raw = Path(ref["path"]).read_text(encoding="utf-8")
                        self.assertTrue(w.markdown_sections(raw, ref.get("sections", [])))

    def test_stage_resource_bundle_is_atomic_and_deduplicates(self):
        state = self.init()
        bundle = w.resource_bundle(state, "u")
        self.assertEqual(bundle["stage"], "R1")
        self.assertEqual(bundle["delivered_bytes"], sum(item["content_bytes"] for item in bundle["resources"]))
        self.assertTrue(all("content" in item for item in bundle["resources"]))
        self.assertEqual(len(state["units"]["u"]["resource_reads"]), len(bundle["resources"]))
        repeated = w.resource_bundle(state, "u")
        self.assertEqual(repeated["delivered_bytes"], 0)
        self.assertTrue(all(item["status"] == "unchanged" for item in repeated["resources"]))
        before = deepcopy(state["units"]["u"]["resource_reads"])
        with patch.object(w, "protocol_refs", return_value=[{"path": str(w.PACKAGE / "references/protocol-source.md"),
                                                               "sections": ["missing heading"]}]):
            with self.assertRaises(ValueError):
                w.resource_bundle(state, "u", refresh=True)
        self.assertEqual(state["units"]["u"]["resource_reads"], before)

    def test_knowledge_no_change_short_exit_requires_impact_checks(self):
        state = self.init("knowledge")
        self.prepare(state)
        payload = self.payload(state)
        payload.update(no_change_reason="existing definitions and affected judgments remain unchanged", relation_checks=[])
        original = deepcopy(state)
        with self.assertRaises(ValueError):
            w.checkpoint(self.root, state, "u", "actual-owner", payload)
        self.assertEqual(state, original)
        payload["relation_checks"] = [{"target": "source.md", "reason": "this fixed research source adds no independent knowledge or affected mechanism judgment"}]
        result = w.checkpoint(self.root, state, "u", "actual-owner", payload)
        self.assertTrue(result["no_change"])
        self.assertTrue(w.complete(state["units"]["u"]))
        self.assertEqual(w.candidates(self.root, state)["mapping"], {})
        self.assertEqual(state["units"]["u"]["rounds"][1]["status"], "skipped")

    def test_conditional_criteria_and_add_unit_are_validated(self):
        state = self.init("trace")
        self.assertEqual(state["units"]["u"]["tasks"]["D4"]["status"], "pending")
        state = self.init("read")
        payload = {"reason": "new cross-paper question requires independent comparison", "budget": {"token_limit": 9000, "basis": "explicit task allowance"},
                   "unit": {"id": "comparison", "kind": "compare", "depends_on": ["u"], "inputs": ["source.md"], "target": "专题/Compare.md"}}
        w.add_unit(self.root, state, payload)
        self.assertIn("comparison", state["units"])
        bad = deepcopy(payload); bad["unit"]["id"] = "another"; bad["unit"]["target"] = "论文/A.md"
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            w.add_unit(self.root, state, bad)
        self.assertEqual(state, before)
        bad = deepcopy(payload); bad["unit"]["id"] = "cycle"; bad["unit"]["depends_on"] = ["cycle"]
        with self.assertRaises(ValueError):
            w.add_unit(self.root, state, bad)

    def test_catalog_count_is_not_a_protocol_requirement(self):
        catalog = json.loads(w.CATALOG_PATH.read_text())
        catalog["tasks"]["A6"] = {"name": "source responsibility", "owner": "coordinator", "rounds": [], "gate": "actual scoped source record"}
        path = self.put("catalog.json", json.dumps(catalog))
        with patch.object(w, "CATALOG_PATH", path):
            self.assertEqual(len(w.catalog()), 49)
            catalog["tasks"]["A6"]["rounds"] = ["invalid-round"]
            path.write_text(json.dumps(catalog))
            with self.assertRaisesRegex(ValueError, "范围引用无效"):
                w.catalog()

    def test_integration_updates_only_writer_and_invalidates_review(self):
        state = self.init()
        self.finish_unit(state)
        w.final_report(self.root, state, "u", finding=self.finding(final=True))
        candidate = w.candidates(self.root, state)["mapping"]["论文/A.md"]
        payload = {"transaction_id": "integrate-1", "target": "论文/A.md", "base_sha256": candidate["sha256"], "owner_observed": True,
                   "actual_owner_record": "actual owner receiving knowledge suggestion", "fragments": [
                       {"old": "scientific content", "new": "science clarified under source condition", "locator": "Example paragraph", "owner_confirmation": "condition remains backed by fixed source",
                        "evidence": [{"path": "source.md", "locator": "section 1"}]}]}
        result = w.integrate(self.root, state, "u", "actual-owner", payload)
        self.assertEqual(result["final_review"], "invalidated")
        self.assertNotIn("final_review", state["units"]["u"])
        self.assertEqual(w.current_round(state["units"]["u"])["status"], "preparing")
        self.assertTrue(w.integrate(self.root, state, "u", "actual-owner", payload)["idempotent_replay"])
        with self.assertRaises(ValueError):
            w.candidates(self.root, state)
        original_final = state["units"]["u"]["rounds"][-1]["history"][-1]["previous_result"]
        immutable = [item for item in original_final["files"] if "candidate-" in item["path"]][0]
        self.assertIn("scientific content", (self.root / immutable["path"]).read_text())
        self.assertNotIn(state["units"]["u"]["draft"], [item["path"] for item in original_final["files"]])
        integrated = (self.root / state["units"]["u"]["draft"]).read_text()
        self.prepare(state)
        revised = self.payload(state, "r4-integrated")
        self.put(state["units"]["u"]["draft"], integrated)
        w.checkpoint(self.root, state, "u", "actual-owner", revised)
        w.final_report(self.root, state, "u", {"agent_id": "actual-owner", "conclusions": ["Integrated condition"], "unresolved": [], "checks": []})
        with self.assertRaises(ValueError):
            w.candidates(self.root, state)
        w.final_report(self.root, state, "u", finding=self.finding(final=True))
        final = w.candidates(self.root, state)["mapping"]
        self.assertEqual(len(final), 1)
        self.assertEqual(final["论文/A.md"]["sha256"], result["sha256"])
        self.assertEqual((self.root / final["论文/A.md"]["version_ref"]["path"]).read_text(), integrated)

    def test_unchanged_draft_and_silent_input_edit_are_rejected(self):
        state = self.init()
        self.prepare(state)
        first = self.payload(state)
        w.checkpoint(self.root, state, "u", "actual-owner", first)
        self.prepare(state)
        unchanged = {**first, "transaction_id": "r2a", "stage": "R2a",
                     "delta": [{"locator": "## R1", "summary": "unsupported same-body reuse", "task_ids": ["B2", "B3"]}]}
        before = deepcopy(state)
        with self.assertRaisesRegex(ValueError, "草稿没有新增"):
            w.checkpoint(self.root, state, "u", "actual-owner", unchanged)
        self.assertEqual(state, before)
        second = self.payload(state)
        self.put("source.md", "unregistered execution edit")
        before = deepcopy(state)
        with self.assertRaisesRegex(ValueError, "已固定文件变动"):
            w.checkpoint(self.root, state, "u", "actual-owner", second)
        self.assertEqual(state, before)

    def test_reopen_does_not_present_old_transaction_as_current_acceptance(self):
        state = self.init()
        self.prepare(state)
        payload = self.payload(state)
        w.checkpoint(self.root, state, "u", "actual-owner", payload)
        w.reopen(state, "u", "R1", "new falsifying observation")
        replay = w.checkpoint(self.root, state, "u", "actual-owner", payload)
        self.assertFalse(replay["accepted_current"])
        self.assertEqual(replay["current_status"], "revision_required")

    def test_report_and_source_manifest_versions_are_bound(self):
        state = self.init()
        self.finish_unit(state)
        unit = state["units"]["u"]
        first = unit["rounds"][0]["accepted"]
        manifest_versions = [item for item in first["input_versions"] if item["path"].endswith(".json")]
        self.assertEqual(json.loads((self.root / manifest_versions[0]["path"]).read_text())["version"], "v1")
        unit.pop("final_report")
        w.final_report(self.root, state, "u", finding=self.finding(final=True))
        with self.assertRaisesRegex(ValueError, "最终report"):
            w.candidates(self.root, state)

    def test_binary_evidence_version_survives_and_is_shared(self):
        state = self.init()
        self.put("image.bin", "binary-image")
        blob = b"\x00\xff\x80actual-image"
        (self.root / "image.bin").write_bytes(blob)
        w.refresh_inputs(self.root, state, "u", ["image.bin"], "actual figure evidence")
        self.prepare(state)
        first = self.payload(state)
        first["evidence"] = [{"path": "image.bin", "locator": "Figure 1"}]
        w.checkpoint(self.root, state, "u", "actual-owner", first)
        version = state["units"]["u"]["rounds"][0]["accepted"]["evidence_files"][0]
        self.assertEqual((self.root / version["path"]).read_bytes(), blob)
        self.prepare(state)
        second = self.payload(state)
        second["evidence"] = [{"path": "image.bin", "locator": "Figure 1"}]
        w.checkpoint(self.root, state, "u", "actual-owner", second)
        other = state["units"]["u"]["rounds"][1]["accepted"]["evidence_files"][0]
        self.assertEqual(version, other)

    def test_integration_protects_external_formal_target(self):
        state = self.init()
        self.finish_unit(state)
        candidate = state["units"]["u"]["rounds"][-1]["accepted"]["candidates"][0]
        self.put("论文/A.md", note("paper", "user edit"))
        payload = {"transaction_id": "integrate-1", "target": "论文/A.md", "base_sha256": candidate["sha256"], "owner_observed": True,
                   "actual_owner_record": "actual writer", "fragments": [{"old": "scientific content", "new": "change", "locator": "paragraph", "owner_confirmation": "accepted",
                   "evidence": [{"path": "source.md", "locator": "section 1"}]}]}
        w.final_report(self.root, state, "u", finding=self.finding(final=True))
        with self.assertRaisesRegex(ValueError, "正式目标并发编辑"):
            w.candidates(self.root, state)
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            w.integrate(self.root, state, "u", "actual-owner", payload)
        self.assertEqual(state, before)
        self.assertIn("scientific content", (self.root / candidate["draft"]).read_text())


if __name__ == "__main__":
    unittest.main()
