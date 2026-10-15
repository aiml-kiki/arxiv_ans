"""Offline usage aggregation from actual response metadata or local Codex JSONL.

Only metering metadata is emitted. Conversation bodies, paths to private logs,
pricing guesses and model calls are never copied into the research repository.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
import sys
from pathlib import Path

from .core import default_root, digest, inside, read_json, write_json

METRICS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens')
ALIASES = {'input_tokens': ('input_tokens', 'prompt_tokens'),
           'cached_input_tokens': ('cached_input_tokens', 'cached_tokens'),
           'output_tokens': ('output_tokens', 'completion_tokens'),
           'reasoning_tokens': ('reasoning_tokens', 'reasoning_output_tokens')}
SCOPES = ('response', 'cumulative', 'tree_total')
ACTION_TAGS = {'wait', 'status', 'delegation', 'user_interaction', 'tool', 'multi_tool'}


def action_for_tool(name):
    """Classify a tool name without inspecting or exporting its arguments."""
    if name in {'wait_agent', 'wait_threads', 'wait', 'sleep'}:
        return 'wait'
    if name in {'list_agents', 'get_worktree_creation_status'}:
        return 'status'
    if name in {'spawn_agent', 'followup_task', 'send_message', 'interrupt_agent'}:
        return 'delegation'
    if name in {'request_user_input', 'request_user_input_async'}:
        return 'user_interaction'
    return 'tool'


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError('actual usage 须为实际非负数或 null；缺测不能填零')
    return value


def normalize(record):
    """Explicit measurement schema; a response ID never doubles as a scope guess."""
    if not isinstance(record, dict):
        raise ValueError('actual usage 记录须为对象')
    usage = record.get('usage') or {}
    if not isinstance(usage, dict):
        raise ValueError('usage 须为对象；缺测使用 null')
    scope = record.get('measurement_scope', 'response')
    if scope not in SCOPES:
        raise ValueError('measurement_scope 须为 response/cumulative/tree_total')
    row = {key: record.get(key) for key in ('run', 'unit', 'stage', 'thread_id', 'parent_thread_id',
        'turn_id', 'root_turn_id',
        'response_id', 'call_id', 'requested_model', 'actual_model', 'requested_effort', 'actual_effort',
        'timestamp', 'elapsed_ms', 'meter_id', 'coverage_start', 'coverage_end',
        'action_tag', 'action_basis', 'tool_name')}
    if row['action_tag'] is not None and row['action_tag'] not in ACTION_TAGS:
        raise ValueError('action_tag 须为受控动作类别')
    if row['tool_name'] is not None and (not isinstance(row['tool_name'], str) or len(row['tool_name']) > 128):
        raise ValueError('tool_name 须为短工具名，不保存参数')
    row.update(measurement_scope=scope, cumulative=bool(record.get('cumulative', scope == 'cumulative')),
               covered_thread_ids=list(record.get('covered_thread_ids') or []),
               covered_response_ids=list(record.get('covered_response_ids') or []))
    row['usage'] = {}
    for metric, aliases in ALIASES.items():
        value = next((usage[key] for key in aliases if key in usage), None)
        if metric == 'cached_input_tokens' and value is None:
            value = (usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}).get('cached_tokens')
        if metric == 'reasoning_tokens' and value is None:
            value = (usage.get('output_tokens_details') or usage.get('completion_tokens_details') or {}).get('reasoning_tokens')
        row['usage'][metric] = _number(value)
    if (row['usage']['cached_input_tokens'] is not None and row['usage']['input_tokens'] is not None
            and row['usage']['cached_input_tokens'] > row['usage']['input_tokens']):
        raise ValueError('cached input 超过 input，计量口径不一致')
    if (row['usage']['reasoning_tokens'] is not None and row['usage']['output_tokens'] is not None
            and row['usage']['reasoning_tokens'] > row['usage']['output_tokens']):
        raise ValueError('reasoning 超过 output，不能假定为 output 子项')
    # Unidentified observations remain visible but cannot certify an exact response ledger.
    row['measurement_id'] = record.get('measurement_id') or row['response_id'] or row['call_id']
    row['identity_available'] = bool(row['response_id'] or row['call_id'])
    row['unavailable'] = [key for key in ('actual_model', 'actual_effort', 'response_id') if row[key] is None]
    row['unavailable'].extend(metric for metric in METRICS if row['usage'][metric] is None)
    return row


def read_records(paths, selected_turn_ids=None):
    """Read only selected local files; Codex context denotes requested routing."""
    rows, diagnostics = [], []
    for filename in paths:
        path = Path(filename).expanduser().resolve()
        if path.suffix != '.jsonl':
            data = read_json(path)
            if isinstance(data, dict) and data.get('format') == 'paper-usage-v1':
                raise ValueError('聚合报告只能用作 baseline，不能重新当作实际响应输入计量')
            records = data.get('records', []) if isinstance(data, dict) else data
            if not isinstance(records, list):
                raise ValueError('usage JSON 须为记录数组或含 records 数组的对象')
            rows.extend(normalize(record) for record in records)
            continue
        context = {}
        session = {}
        legacy_rows, receipt_rows = [], []
        turn_receipts = defaultdict(dict)
        turn_totals = {}
        pending_calls = []
        with path.open(encoding='utf-8') as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics.append({'kind': 'malformed_jsonl', 'line': line_number})
                    continue
                if not isinstance(event, dict):
                    diagnostics.append({'kind': 'nonobject_jsonl', 'line': line_number})
                    continue
                kind, payload = event.get('type'), event.get('payload') or {}
                if not isinstance(payload, dict):
                    continue
                if kind == 'session_meta':
                    session = {'thread_id': payload.get('id'), 'parent_thread_id': payload.get('parent_thread_id')}
                elif kind == 'turn_context':
                    context = {'turn_id': payload.get('turn_id'),
                               'root_turn_id': payload.get('root_turn_id'),
                               'requested_model': payload.get('model'),
                               'requested_effort': payload.get('effort', payload.get('reasoning_effort'))}
                    pending_calls = []
                elif kind == 'response_item' and payload.get('type') in {'function_call', 'custom_tool_call'}:
                    # Only tool identity enters the ledger. Arguments and message bodies stay local.
                    pending_calls.append({'name': payload.get('name'), 'call_id': payload.get('call_id')})
                elif kind == 'token_usage_record':
                    usage = payload.get('usage')
                    if not isinstance(usage, dict):
                        diagnostics.append({'kind': 'receipt_usage_unavailable', 'line': line_number})
                        continue
                    if not payload.get('thread_id') or not payload.get('turn_id') or not payload.get('response_id'):
                        diagnostics.append({'kind': 'receipt_identity_unavailable', 'line': line_number})
                    if (usage.get('total_tokens') is not None and
                            usage['total_tokens'] != usage.get('input_tokens', 0) + usage.get('output_tokens', 0)):
                        diagnostics.append({'kind': 'receipt_total_mismatch', 'line': line_number})
                    action = {}
                    if len(pending_calls) == 1 and isinstance(pending_calls[0]['name'], str):
                        action = {'action_tag': action_for_tool(pending_calls[0]['name']),
                                  'action_basis': 'preceding_tool_call_same_turn',
                                  'tool_name': pending_calls[0]['name'],
                                  'call_id': pending_calls[0]['call_id']}
                    elif len(pending_calls) > 1:
                        action = {'action_tag': 'multi_tool', 'action_basis': 'multiple_preceding_calls'}
                    pending_calls = []
                    row = normalize({**session, **context, **action, 'timestamp': event.get('timestamp'),
                        'thread_id': payload.get('thread_id') or session.get('thread_id'),
                        'turn_id': payload.get('turn_id'), 'root_turn_id': payload.get('root_turn_id'),
                        'response_id': payload.get('response_id'), 'usage': usage})
                    receipt_rows.append(row)
                    turn_id = row['turn_id']
                    if turn_id and row['response_id']:
                        turn_receipts[turn_id][row['response_id']] = row
                    if turn_id and isinstance(payload.get('turn_token_usage'), dict):
                        turn_totals[turn_id] = payload['turn_token_usage']
                elif kind == 'event_msg' and payload.get('type') == 'token_count':
                    info = payload.get('info') or {}
                    total = info.get('total_token_usage')
                    last = info.get('last_token_usage')
                    if not total and not last:
                        continue
                    # Codex snapshots are per thread; token_count is not a provider response receipt.
                    legacy_rows.append(normalize({**session, **context, 'timestamp': event.get('timestamp'),
                        'measurement_id': 'codex-observation-' + digest(json.dumps(event, sort_keys=True)),
                        'meter_id': 'codex-thread-tokens',
                        'measurement_scope': 'cumulative' if total else 'response',
                        'response_id': payload.get('response_id', info.get('response_id')),
                        'actual_model': payload.get('actual_model', info.get('actual_model')),
                        'actual_effort': payload.get('actual_effort', info.get('actual_effort')),
                        'usage': total if total else last}))
                elif kind == 'response' or (kind == 'response_item' and payload.get('usage')):
                    response = payload.get('response', payload)
                    if isinstance(response, dict) and isinstance(response.get('usage'), dict):
                        legacy_rows.append(normalize({**session, **context, 'timestamp': event.get('timestamp'),
                            'response_id': response.get('id'), 'actual_model': response.get('model'),
                            'actual_effort': (response.get('reasoning') or {}).get('effort'),
                            'usage': response['usage']}))
        # Recent Codex files contain exact response receipts alongside cumulative
        # token_count snapshots. Counting both would double bill the same calls.
        if receipt_rows:
            for turn_id, receipts in turn_receipts.items():
                cumulative = turn_totals.get(turn_id)
                if cumulative is None:
                    diagnostics.append({'kind': 'receipt_turn_total_unavailable', 'turn_id': turn_id})
                    continue
                for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens'):
                    if key not in cumulative and key not in next(iter(receipts.values()))['usage']:
                        continue
                    normalized_key = 'reasoning_tokens' if key == 'reasoning_output_tokens' else key
                    if cumulative.get(key) != sum(row['usage'][normalized_key] or 0 for row in receipts.values()):
                        diagnostics.append({'kind': 'receipt_turn_cumulative_mismatch',
                                            'turn_id': turn_id, 'metric': key})
            chosen = receipt_rows
        else:
            chosen = legacy_rows
        if selected_turn_ids is not None:
            chosen = [row for row in chosen if row['turn_id'] in selected_turn_ids.get(row['thread_id'], [])]
        rows.extend(chosen)
    return rows, diagnostics


def _key(row):
    if row['cumulative']:
        # Separate provider meters and tree/agent scopes; timestamps identify observations.
        return ('snapshot', row['thread_id'], row['meter_id'], row['measurement_scope'],
                tuple(sorted(row['covered_thread_ids'])), row['timestamp'])
    if row['response_id'] or row['call_id']:
        return ('response', row['response_id'] or row['call_id'], row['measurement_scope'])
    return ('observation', row['measurement_id'], row['thread_id'], row['timestamp'])


def _covers(parent, child):
    if child['response_id'] and child['response_id'] in parent['covered_response_ids']:
        return True
    if child['thread_id'] not in parent['covered_thread_ids']:
        return False
    # A named thread alone is not proof that the aggregate spans this response.
    start, end = parent.get('coverage_start'), parent.get('coverage_end')
    child_start = child.get('coverage_start') or child.get('timestamp')
    child_end = child.get('coverage_end') or child.get('timestamp')
    if end is None or child_end is None:
        return False
    return (start is None or (child_start is not None and start <= child_start)) and child_end <= end


def context_growth_profile(rows, window=5):
    """Observed input growth only; never an automatic handover decision."""
    by_thread = defaultdict(list)
    for row in rows:
        if (row['measurement_scope'] == 'response' and row.get('response_id') and row.get('thread_id')
                and row.get('timestamp') and row['usage']['input_tokens'] is not None):
            by_thread[row['thread_id']].append(row)
    result = {}
    for thread, values in by_thread.items():
        ordered = sorted(values, key=lambda item: (item['timestamp'], item['response_id']))
        n = min(window, len(ordered) // 2)
        if n < 2:
            result[thread] = {'available': False, 'responses': len(ordered),
                              'reason': 'need_two_nonoverlapping_windows_of_at_least_two_responses'}
            continue
        previous = ordered[-2*n:-n]
        recent = ordered[-n:]
        mean = lambda group: round(sum(item['usage']['input_tokens'] for item in group) / len(group))
        old, new = mean(previous), mean(recent)
        result[thread] = {'available': True, 'responses': len(ordered), 'window_responses': n,
                          'previous_mean_input_tokens': old, 'recent_mean_input_tokens': new,
                          'observed_mean_change': new - old,
                          'basis': 'two adjacent response windows; cached input included; stage mix may differ'}
    return dict(sorted(result.items()))


def aggregate(records, *, expected_threads=None, evaluation_scope=None, full_tree=False, diagnostics=None):
    """Combine disjoint actual observations and disclose every unresolved scope."""
    unique, duplicates, issues = {}, 0, list(diagnostics or [])
    for record in records:
        row = normalize(record) if 'identity_available' not in record else dict(record)
        row['usage'] = dict(row['usage'])
        key = _key(row)
        if key in unique:
            if unique[key]['usage'] != row['usage']:
                issues.append({'kind': 'conflicting_duplicate', 'measurement': row['measurement_id']})
            duplicates += 1
        else:
            unique[key] = row
    rows = list(unique.values())
    snapshots = defaultdict(list)
    discrete = []
    for row in rows:
        if row['cumulative']:
            if not row['thread_id'] or not row['meter_id'] or not row['timestamp']:
                issues.append({'kind': 'unattributed_cumulative', 'measurement': row['measurement_id']})
                continue
            snapshots[(row['thread_id'], row['meter_id'], row['measurement_scope'],
                       tuple(sorted(row['covered_thread_ids'])))].append(row)
        else:
            if not row['identity_available'] and not row['measurement_id']:
                issues.append({'kind': 'response_identity_unavailable', 'thread_id': row['thread_id']})
            discrete.append(row)
    for group in snapshots.values():
        previous = None
        for row in sorted(group, key=lambda item: item['timestamp']):
            current = dict(row['usage'])
            row['snapshot_usage'] = current
            if previous:
                for metric in METRICS:
                    value, old = current[metric], previous['usage'][metric]
                    row['usage'][metric] = value - old if value is not None and old is not None and value >= old else None
                    if value is not None and old is not None and value < old:
                        issues.append({'kind': 'cumulative_reset', 'metric': metric, 'thread_id': row['thread_id']})
                row['coverage_start'] = previous['timestamp']
            row['coverage_end'] = row['timestamp']
            row['covered_thread_ids'] = row['covered_thread_ids'] or [row['thread_id']]
            # Keep each unmodified snapshot to compute the next delta.
            previous = {**row, 'usage': current}
            discrete.append(row)
    # Aggregates take precedence only where their explicit coverage proves containment.
    parents = [row for row in discrete if row['measurement_scope'] == 'tree_total' or row['cumulative']]
    excluded = set()
    for index, row in enumerate(discrete):
        covering = [parent for parent in parents if parent is not row and _covers(parent, row)]
        if covering:
            # A tree aggregate supersedes per-thread increments. A cumulative
            # thread snapshot supersedes actual receipts within that interval.
            if row['measurement_scope'] == 'tree_total':
                covering = [parent for parent in covering if parent['measurement_scope'] == 'tree_total'
                            and set(parent['covered_thread_ids']) > set(row['covered_thread_ids'])]
            elif row['cumulative']:
                covering = [parent for parent in covering if parent['measurement_scope'] == 'tree_total']
            if covering:
                excluded.add(index)
    included = [row for index, row in enumerate(discrete) if index not in excluded]
    # Unproven tree coverage cannot safely be added to its known members.
    ambiguous_trees = set()
    for tree in [row for row in included if row['measurement_scope'] == 'tree_total']:
        if not tree['covered_thread_ids'] and not tree['covered_response_ids']:
            issues.append({'kind': 'tree_coverage_unavailable', 'measurement': tree['measurement_id']})
            if len(included) > 1:
                ambiguous_trees.add(id(tree))
        for row in included:
            if row is not tree and row['thread_id'] in tree['covered_thread_ids'] and not _covers(tree, row):
                # Disjoint intervals may safely coexist; unknown intervals may not.
                start, end, timestamp = tree.get('coverage_start'), tree.get('coverage_end'), row.get('timestamp')
                if timestamp is None or end is None or not (timestamp > end or (start is not None and timestamp < start)):
                    ambiguous_trees.add(id(tree))
                    issues.append({'kind': 'ambiguous_tree_overlap', 'measurement': tree['measurement_id'],
                                   'thread_id': row['thread_id']})
    unresolved_aggregates = [row for row in included if id(row) in ambiguous_trees]
    excluded_records = [row for index, row in enumerate(discrete) if index in excluded]
    included = [row for row in included if id(row) not in ambiguous_trees]
    totals, missing = {}, {}
    for metric in METRICS:
        present = [row['usage'][metric] for row in included if row['usage'][metric] is not None]
        missing[metric] = sum(row['usage'][metric] is None for row in included)
        totals[metric] = {'observed': sum(present) if present else None,
                         'complete': bool(included) and missing[metric] == 0,
                         'unavailable_measurements': missing[metric]}
    metered = {thread for row in included for thread in ([row['thread_id']] + row['covered_thread_ids']) if thread}
    expected = set(expected_threads or [])
    missing_threads = sorted(expected - metered)
    scope_complete = bool(full_tree and expected and not missing_threads and not issues
                          and totals['input_tokens']['complete'] and totals['output_tokens']['complete'])
    total_tokens = (totals['input_tokens']['observed'] + totals['output_tokens']['observed']
                    if totals['input_tokens']['observed'] is not None and totals['output_tokens']['observed'] is not None else None)
    scopes = sorted({row['measurement_scope'] for row in rows})
    action_profile = defaultdict(lambda: {'responses': 0, 'input_tokens': 0, 'cached_input_tokens': 0,
                                         'output_tokens': 0, 'missing_usage': 0})
    for row in included:
        if row['measurement_scope'] != 'response' or not row['response_id']:
            continue
        bucket = action_profile[row.get('action_tag') or 'unattributed']
        bucket['responses'] += 1
        for metric in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
            value = row['usage'][metric]
            if value is None:
                bucket['missing_usage'] += 1
            else:
                bucket[metric] += value
    return {'format': 'paper-usage-v1', 'evaluation_scope': evaluation_scope,
            'full_tree_requested': full_tree, 'coverage_complete': scope_complete,
            'expected_threads': sorted(expected), 'metered_threads': sorted(metered), 'unavailable_threads': missing_threads,
            'totals': totals, 'total_tokens': total_tokens,
            'total_tokens_complete': scope_complete,
            'measurement_scopes': scopes, 'observations': len(rows), 'included_measurements': len(included),
            'duplicate_observations': duplicates, 'covered_measurements_excluded': len(excluded),
            'response_count': len({row['response_id'] for row in rows if row['response_id']}) or None,
            'response_count_scope': 'observed identified receipts only',
            'actual_model_unavailable': sum(row['actual_model'] is None for row in included),
            'actual_effort_unavailable': sum(row['actual_effort'] is None for row in included),
            'action_profile': dict(sorted(action_profile.items())),
            'action_attribution': 'preceding tool name associated with response; whole response context is not causal tool cost',
            'context_growth': context_growth_profile(included),
            'records': included, 'unresolved_aggregates': unresolved_aggregates, 'excluded_records': excluded_records, 'diagnostics': issues,
            'accounting': 'input includes cached input; reasoning is an output subset, neither is added again',
            'unavailable': ['price/cost']}


def compare_reports(baseline, current):
    reasons = []
    if not baseline.get('coverage_complete') or not current.get('coverage_complete'):
        reasons.append('full_tree_usage_incomplete')
    scope = baseline.get('evaluation_scope')
    required = {'source_versions', 'question', 'research_scope', 'model_capabilities', 'prompt_background'}
    if not isinstance(scope, dict) or not required.issubset(scope) or any(not scope[key] for key in required):
        reasons.append('comparison_scope_dimensions_unavailable')
    if not scope or scope != current.get('evaluation_scope'):
        reasons.append('complete_research_scope_not_matched')
    if baseline.get('actual_model_unavailable', 0) or current.get('actual_model_unavailable', 0):
        reasons.append('actual_model_unavailable')
    if not baseline.get('total_tokens'):
        reasons.append('baseline_total_unavailable_or_zero')
    ratio = current['total_tokens'] / baseline['total_tokens'] if not reasons else None
    return {'token_ratio': ratio, 'fourfold_cost_gate': ratio <= .25 if ratio is not None else 'unavailable',
            'quality_gate': 'unavailable', 'fourfold_overall': 'unavailable', 'reasons': reasons,
            'limitation': 'cost gate uses full tree including cached input; scientific quality needs independent review'}


def attach_run_metadata(root, run_id, records):
    """Use actual dispatch receipts for exact actor matches; never guess a stage."""
    state = read_json(inside(root, f'.research/work/{run_id}/workflow.json'))
    calls = [event for event in state.get('events', [])
             if event.get('event_type') == 'actual_host_call' or event.get('event') == 'actual_dispatch']
    for helper in state.get('runtime', {}).get('helpers', {}).values():
        for receipt in [*helper.get('history', []), helper]:
            if receipt.get('agent_id') and receipt.get('actual_record') and receipt.get('status') in {
                    'bound', 'completed', 'interrupted', 'failed'}:
                calls.append(receipt)
    matches = defaultdict(list)
    for event in calls:
        actor = event.get('thread_id') or event.get('agent_id')
        if actor:
            matches[actor].append(event)
    for row in records:
        row['run'] = row.get('run') or run_id
        actual = matches.get(row.get('thread_id'), [])
        for key in ('unit', 'requested_model', 'requested_effort', 'actual_model', 'actual_effort'):
            values = {event[key] for event in actual if event.get(key) is not None}
            if row.get(key) is None and len(values) == 1:
                row[key] = next(iter(values))
        row['unavailable'] = [key for key in row['unavailable'] if row.get(key) is None]
    return {'run': run_id, 'registered_actual_actors': sorted(matches),
            'root_agent_id': state.get('runtime', {}).get('capabilities', {}).get('root_agent_id'),
            'metadata_ref': f'.research/work/{run_id}/workflow.json',
            'stage_attribution': 'unavailable unless response metadata supplies a stage',
            'full_tree_membership': 'unavailable unless scope manifest includes root and all actual actors'}


def stage_profile(root, run_id, records, actor_thread_ids):
    """Attribute identified responses only inside observed begin..checkpoint windows.

    Transition/recovery traffic stays explicitly unattributed instead of being
    silently charged to the previous science stage.
    """
    state = read_json(inside(root, f'.research/work/{run_id}/workflow.json'))
    owner_units = {}
    for unit_id, unit in state.get('units', {}).items():
        for owner in unit.get('owner_history', []):
            actor = owner.get('agent_id')
            if actor:
                owner_units.setdefault(actor_thread_ids.get(actor, actor), set()).add(unit_id)
    runtime = state.get('runtime', {})
    root_id = (runtime.get('capabilities') or {}).get('root_agent_id')
    root_thread = actor_thread_ids.get(root_id, root_id)
    helper_threads = {actor_thread_ids.get(item['agent_id'], item['agent_id']): key
                      for key, item in runtime.get('helpers', {}).items() if item.get('agent_id')}
    windows = defaultdict(list)
    begins = {}
    for event in state.get('events', []):
        key = (event.get('unit'), event.get('stage'))
        moment = event.get('at')
        if not key[0] or not key[1] or not moment:
            continue
        when = datetime.fromisoformat(moment.replace('Z', '+00:00'))
        if event.get('event') == 'internal_checkpoint' and event.get('operation') == 'begin':
            begins[key] = when
        elif event.get('event') == 'scientific_checkpoint' and key in begins:
            start = begins.pop(key)
            if start <= when:
                windows[key[0]].append((start, when, key[1]))
    buckets = defaultdict(lambda: {'responses': 0, 'input_tokens': 0, 'cached_input_tokens': 0,
                                   'output_tokens': 0, 'missing_usage': 0})
    for row in records:
        if row.get('measurement_scope') != 'response' or not row.get('response_id'):
            continue
        thread = row.get('thread_id')
        units = owner_units.get(thread, set())
        bucket = ('root' if thread and thread == root_thread else
                  f'helper/{helper_threads[thread]}' if thread in helper_threads else
                  f'{next(iter(units))}/transition' if len(units) == 1 else 'unattributed')
        if len(units) == 1 and row.get('timestamp'):
            instant = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
            found = [f'{unit}/{stage}' for unit in units for start, end, stage in windows[unit]
                     if start <= instant <= end]
            if len(found) == 1:
                bucket = found[0]
        target = buckets[bucket]
        target['responses'] += 1
        for metric in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
            value = row['usage'][metric]
            if value is None:
                target['missing_usage'] += 1
            else:
                target[metric] += value
    return {'basis': 'identified owner response timestamp within actual stage begin..checkpoint; root/helper/transition separated',
            'buckets': dict(sorted(buckets.items()))}


def _add_options(parser):
    parser.add_argument('--root', type=Path, default=default_root())
    parser.add_argument('--input', action='append', required=True, help='选定本地 usage JSON 或 Codex JSONL；可重复')
    parser.add_argument('--run', help='已有 workflow run：按真实 dispatch 回执核对精确线程归属')
    parser.add_argument('--scope-manifest', help='库根相对 JSON：evaluation_scope、expected_threads、full_tree')
    parser.add_argument('--output', help='可选库根相对聚合结果；仅保存计量元数据')
    parser.add_argument('--records', action='store_true', help='显式回显完整计量元数据；无会话正文')
    parser.add_argument('--baseline', help='同研究范围完整基线 usage 聚合 JSON')
    return parser


def add_parser(subparsers):
    return _add_options(subparsers.add_parser('usage', help='离线汇总实际 usage；不调用模型、不复制私人会话'))


def run(root, args):
    root = Path(root).resolve()
    manifest = read_json(inside(root, args.scope_manifest)) if args.scope_manifest else {}
    selected = manifest.get('selected_turn_ids')
    if selected is not None and (not isinstance(selected, dict) or
            any(not isinstance(thread, str) or not isinstance(turns, list) or
                not turns or any(not isinstance(turn, str) for turn in turns)
                for thread, turns in selected.items())):
        raise ValueError('selected_turn_ids 须为线程ID到非空 turn ID 数组的映射')
    records, diagnostics = read_records(args.input, selected_turn_ids=selected)
    run_metadata = attach_run_metadata(root, args.run, records) if getattr(args, 'run', None) else None
    if run_metadata and manifest.get('full_tree'):
        known = set(run_metadata['registered_actual_actors'])
        if run_metadata['root_agent_id']:
            known.add(run_metadata['root_agent_id'])
        else:
            diagnostics.append({'kind': 'root_actor_unavailable_for_full_tree'})
        aliases = manifest.get('actor_thread_ids') or {}
        if not isinstance(aliases, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                for key, value in aliases.items()):
            raise ValueError('actor_thread_ids 须为代理ID到真实线程ID的映射')
        omitted = sorted(actor for actor in known if aliases.get(actor, actor)
                         not in set(manifest.get('expected_threads') or []))
        if omitted:
            diagnostics.append({'kind': 'registered_tree_actor_omitted', 'actors': omitted})
    report = aggregate(records, expected_threads=manifest.get('expected_threads'),
        evaluation_scope=manifest.get('evaluation_scope'), full_tree=bool(manifest.get('full_tree')),
        diagnostics=diagnostics)
    if run_metadata:
        report['run_metadata'] = run_metadata
        report['stage_profile'] = stage_profile(root, args.run, records, manifest.get('actor_thread_ids') or {})
    if args.baseline:
        report['comparison'] = compare_reports(read_json(inside(root, args.baseline)), report)
    if args.output:
        output = inside(root, args.output)
        if not output.is_relative_to(root / '.research'):
            raise ValueError('usage 聚合只能保存到 .research；不写正式知识页')
        write_json(output, report)
        return {'path': str(output.relative_to(root)), **{key: report[key] for key in
            ('totals', 'total_tokens', 'coverage_complete', 'unavailable_threads', 'diagnostics')},
            'comparison': report.get('comparison')}
    if getattr(args, 'records', False):
        return report
    return {key: value for key, value in report.items() if key not in {'records', 'unresolved_aggregates', 'excluded_records'}}


def main(argv=None):
    if isinstance(argv, argparse.Namespace):
        return run(argv.root, argv)
    parser = argparse.ArgumentParser(prog='research.py usage', description=__doc__)
    _add_options(parser)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return run(args.root, args)
