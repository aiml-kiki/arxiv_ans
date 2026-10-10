"""Record bounded helper dispatches; only the host can create or stop an agent."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .core import atomic_text, digest, inside, locked, read_json, safe_id, utc_now, write_json


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + '须为非空文本')
    return value.strip()


def positive(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(label + '须为正整数')
    return value


def observed(event):
    if not isinstance(event, dict) or event.get('observed') is not True:
        raise ValueError('仅登记真实宿主观察；须observed:true')
    nonempty(event.get('actual_record'), '实际工具回执或运行元数据定位')


def routing():
    return yaml.safe_load((Path(__file__).resolve().parents[2] / 'agents/routing.yaml').read_text())


def configure(state, event):
    observed(event)
    positive(event.get('active_limit_including_root'), '宿主全树活跃上限（含根）')
    if event.get('open_subagent_limit') is not None:
        positive(event['open_subagent_limit'], '宿主打开子线程上限（不含根）')
    nonempty(event.get('root_agent_id'), '根代理ID')
    for name in ('spawn_model', 'spawn_effort', 'fork_none', 'nested_delegation'):
        if not isinstance(event.get(name), bool):
            raise ValueError('能力' + name + '须为实际布尔值')
    models = event.get('available_models')
    if not isinstance(models, list) or not models or any(not isinstance(x, str) or not x for x in models):
        raise ValueError('available_models须为实际可用模型列表')
    runtime = state.setdefault('runtime', {'helpers': {}})
    runtime['capabilities'] = dict(event, at=utc_now())
    return {'status': 'capabilities_recorded', 'reference': '/runtime/capabilities',
            'script_dispatches_agents': False}


def owner(state, unit_id, agent_id):
    unit = state['units'].get(unit_id)
    if not unit or (unit.get('owner') or {}).get('agent_id') != agent_id:
        raise ValueError('局部助手仅由本单元真实owner管理')
    delivery_window = (bool(unit.get('rounds'))
        and all(stage.get('status') in {'accepted', 'skipped'} for stage in unit['rounds'])
        and not unit.get('final_report') and not unit.get('owner_interrupted'))
    if not unit.get('owner_active') and not delivery_window:
        raise ValueError('owner未真实运行/恢复；不能用助手记录冒充恢复')
    return unit


def live_ids(event, capabilities):
    observed(event)
    timestamp = event.get('observed_at')
    try:
        value = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if value.tzinfo is None or not -5 <= (datetime.now(timezone.utc) - value).total_seconds() <= 90:
            raise ValueError('宿主容量观察过期；定向获取live tree后重试')
    except (AttributeError, TypeError) as exc:
        raise ValueError('observed_at须为本次容量观察的带时区时间') from exc
    active = event.get('active_agent_ids')
    opened = event.get('open_agent_ids')
    for value, label in ((active, 'active_agent_ids'), (opened, 'open_agent_ids')):
        if value is None and label == 'open_agent_ids' and capabilities.get('open_subagent_limit') is None:
            continue
        if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
            raise ValueError(label + '须为真实宿主全树ID列表；不可用空值代替未知')
    return set(active), set(opened or [])


def capacity(state, event, reuse_id=None):
    runtime = state.get('runtime', {})
    caps = runtime.get('capabilities')
    if not caps:
        raise ValueError('先configure实际宿主能力')
    active, opened = live_ids(event, caps)
    root = caps['root_agent_id']
    active.add(root)
    for unit in state['units'].values():
        if unit.get('owner_active') and (unit.get('owner') or {}).get('mode') == 'agent':
            active.add(unit['owner']['agent_id'])
            opened.add(unit['owner']['agent_id'])
    reserved = 0
    reserved_open = 0
    for helper in runtime.get('helpers', {}).values():
        if helper['status'] == 'reserved':
            reserved += 1
            reserved_open += not helper.get('reuse_agent_id')
        elif helper['status'] == 'bound':
            active.add(helper['agent_id'])
            opened.add(helper['agent_id'])
        elif helper.get('thread_status') == 'open':
            opened.add(helper['agent_id'])
    if reuse_id and reuse_id in active:
        raise ValueError('待复用助手仍活跃，不能同时续派')
    # YAML is a project bound, not a claim to change host limits.
    local = routing()
    limit = min(caps['active_limit_including_root'], local['max_active_subagents'] + 1)
    if len(active) + reserved >= limit:
        raise ValueError('全树没有助手名额；owner本人执行或事件等待，不能超额创建')
    opened.discard(root)
    open_limit = caps.get('open_subagent_limit')
    if open_limit is not None and len(opened) + reserved_open + (not reuse_id) > open_limit:
        raise ValueError('打开子线程已达上限；完成不等于关闭，请复用或本人执行')
    return {'active_count_including_root': len(active), 'reserved': reserved,
            'open_subagent_count': len(opened), 'host_observed_at': event['observed_at']}


def reserve(root, state, unit_id, agent_id, event):
    root = Path(root).resolve()
    unit = owner(state, unit_id, agent_id)
    runtime = state.setdefault('runtime', {'helpers': {}})
    caps = runtime.get('capabilities', {})
    if not all(caps.get(x) for x in ('spawn_model', 'spawn_effort', 'fork_none', 'nested_delegation')):
        raise ValueError('当前能力不足以执行独立跨模型委派；记录降级并由owner执行')
    helper_id = safe_id(event.get('helper'))
    profile = event.get('profile')
    route = routing()['profiles'].get(profile)
    if not route or route['model'] not in caps['available_models']:
        raise ValueError('profile或模型当前不可用；明确选择受支持档位及降级理由')
    for field in ('question', 'consumer', 'acceptance', 'benefit_reason'):
        nonempty(event.get(field), field)
    source_refs = event.get('source_refs')
    if not isinstance(source_refs, list) or not source_refs:
        raise ValueError('source_refs须有当前问题的固定来源窗口/接口引用')
    bound_refs = []
    for ref in source_refs:
        if not isinstance(ref, dict):
            raise ValueError('source_refs项须为path和locator')
        nonempty(ref.get('locator'), '来源窗口locator')
        source = inside(root, nonempty(ref.get('path'), '来源path'))
        if not source.is_file():
            raise ValueError('来源文件不存在: ' + str(source))
        from .workflow import allowed_evidence
        fixed = {path: item['sha256'] for path, item in allowed_evidence(root, state, unit).items()}
        for stage in unit.get('rounds', []):
            if stage.get('accepted'):
                fixed.update({item['path']: item['sha256'] for item in stage['accepted'].get('files', [])})
        if ref['path'] not in fixed:
            raise ValueError('助手来源先登记固定inputs或绑定已接受科学版本: ' + ref['path'])
        if digest(source.read_bytes()) != fixed[ref['path']]:
            raise ValueError('助手来源已改变，先更新受影响输入')
        bound_refs.append(dict(ref, sha256=fixed[ref['path']]))
    budget = positive(event.get('token_budget'), '助手全流程token预算（含整合复核先验）')
    old = runtime['helpers'].get(helper_id)
    reuse_id = None
    if old:
        if old['status'] != 'completed' or old.get('thread_status') != 'open':
            raise ValueError('已有helper标识不可覆盖；只有实际完成且未关闭助手可续派')
        if old['unit'] != unit_id or old['requested_model'] != route['model'] or old['requested_effort'] != route['reasoning_effort']:
            raise ValueError('续派须同owner和同档模型；不能将followup伪装切模型')
        reuse_id = old['agent_id']
    counts = capacity(state, event, reuse_id)
    result_path = str(Path(unit['output_dir']) / 'helpers' / (helper_id + '.json'))
    record = {key: event[key] for key in ('question', 'consumer', 'acceptance', 'benefit_reason', 'source_refs')}
    record.update(helper=helper_id, unit=unit_id, parent_agent_id=agent_id, profile=profile,
                  requested_model=route['model'], requested_effort=route['reasoning_effort'],
                  token_budget=budget, fork_turns='none', depth=2, status='reserved',
                  result_path=result_path, reuse_agent_id=reuse_id, reserved_at=utc_now(),
                  capacity=counts, history=(old.get('history', []) + [{k: v for k, v in old.items() if k != 'history'}] if old else []))
    record['source_refs'] = bound_refs
    runtime['helpers'][helper_id] = record
    return {'helper': helper_id, 'status': 'reserved', 'mode': 'followup' if reuse_id else 'spawn',
            'agent_id': reuse_id, 'model': route['model'], 'reasoning_effort': route['reasoning_effort'],
            'fork_turns': 'none', 'result_path': result_path, 'script_dispatches_agents': False}


def transition(root, state, unit_id, agent_id, action, event):
    root = Path(root).resolve()
    owner(state, unit_id, agent_id)
    helpers = state.get('runtime', {}).get('helpers', {})
    helper = helpers.get(safe_id(event.get('helper')))
    if not helper or helper['unit'] != unit_id or helper['parent_agent_id'] != agent_id:
        raise ValueError('助手不属于当前owner')
    if action == 'cancel':
        if helper['status'] != 'reserved':
            raise ValueError('cancel只取消未绑定预留；运行代理须真实停止再complete')
        helper.update(status='cancelled', reason=nonempty(event.get('reason'), '取消预留原因'), ended_at=utc_now())
    elif action == 'bind':
        observed(event)
        if helper['status'] != 'reserved':
            raise ValueError('仅绑定一次真实预留')
        actual_id = nonempty(event.get('agent_id'), '宿主真实助手ID')
        if event.get('parent_agent_id') != agent_id:
            raise ValueError('实际调用parent必须为论文owner')
        expected_mode = 'followup' if helper.get('reuse_agent_id') else 'spawn'
        if event.get('mode') != expected_mode or (expected_mode == 'followup' and actual_id != helper['reuse_agent_id']):
            raise ValueError('实际创建/续派方式或实例与预留不符')
        if expected_mode == 'spawn' and event.get('fork_turns') != 'none':
            raise ValueError('新助手须使用fork_turns:none独立上下文')
        identities = {u.get('owner', {}).get('agent_id') for u in state['units'].values() if u.get('owner')}
        identities.update(h.get('agent_id') for h in helpers.values() if h is not helper)
        if actual_id in identities:
            raise ValueError('助手实例已有其他责任，不得重复绑定')
        mismatches = [key for key, expected in (('actual_model', helper['requested_model']),
                                                ('actual_effort', helper['requested_effort']))
                      if event.get(key) is not None and event[key] != expected]
        helper.update(status='bound', agent_id=actual_id, actual_model=event.get('actual_model'),
                      actual_effort=event.get('actual_effort'), actual_record=event['actual_record'],
                      call_id=event.get('call_id'), bound_at=utc_now(), thread_status='open',
                      route_verified=not mismatches and event.get('actual_model') is not None and event.get('actual_effort') is not None,
                      route_mismatch=mismatches)
    else:
        observed(event)
        if helper['status'] != 'bound':
            raise ValueError('先绑定实际助手；不将预留当成执行')
        if event.get('agent_id') != helper['agent_id']:
            raise ValueError('完成记录实例不符')
        if event.get('host_status') not in {'completed', 'interrupted', 'failed'}:
            raise ValueError('host_status须是已观察的completed/interrupted/failed')
        thread_status = event.get('thread_status', 'unknown')
        if thread_status not in {'open', 'closed', 'unknown'}:
            raise ValueError('thread_status不属于open/closed/unknown')
        if event.get('close_requested') and not state['runtime']['capabilities'].get('close_thread'):
            raise ValueError('当前宿主未声明关闭能力，不能伪造close')
        if event['host_status'] == 'completed':
            for source in helper['source_refs']:
                if digest(inside(root, source['path']).read_bytes()) != source['sha256']:
                    raise ValueError('助手固定来源在执行期间改变，先核对受影响结果')
            path = inside(root, helper['result_path'])
            raw = path.read_bytes()
            result = read_json(path)
            for field in ('answer', 'limitations'):
                nonempty(result.get(field), field)
            if not isinstance(result.get('evidence'), list) or not result['evidence']:
                raise ValueError('助手最终结果须有证据定位')
            fixed_refs = {(item['path'], item['locator']) for item in helper['source_refs']}
            for item in result['evidence']:
                if not isinstance(item, dict) or not item.get('locator') or item.get('path') not in {r[0] for r in fixed_refs}:
                    raise ValueError('助手证据必须引用任务给定固定来源与明确定位')
            expected = digest(raw)
            preserved = inside(root, f'.research/records/runs/{state["run_id"]}/helpers/{expected}.json')
            if preserved.exists() and digest(preserved.read_bytes()) != expected:
                raise ValueError('已保存助手版本内容不符')
            if not preserved.exists():
                atomic_text(preserved, raw.decode('utf-8'))
            if path.read_bytes() != raw:
                raise ValueError('助手结果在接受期间发生变化；保留原版本重新核对')
            helper['result_ref'] = {'path': str(preserved.relative_to(root)), 'sha256': expected}
        helper.update(status=event['host_status'], thread_status=thread_status,
                      completion_record=event['actual_record'], ended_at=utc_now())
    return {key: helper.get(key) for key in ('helper', 'status', 'route_verified', 'route_mismatch', 'result_ref', 'thread_status')}


def status(state):
    helpers = state.get('runtime', {}).get('helpers', {})
    return {'script_dispatches_agents': False, 'capabilities_recorded': bool(state.get('runtime', {}).get('capabilities')),
            'helpers': [{key: h.get(key) for key in ('helper', 'unit', 'status', 'route_verified', 'thread_status')}
                        for h in helpers.values() if h['status'] in {'reserved', 'bound'}],
            'completed_count': sum(h['status'] == 'completed' for h in helpers.values()),
            'record_ref': '/runtime/helpers'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    actions = parser.add_subparsers(dest='action', required=True)
    for name in ('configure', 'reserve', 'bind', 'complete', 'cancel', 'status'):
        action = actions.add_parser(name)
        action.add_argument('--run', required=True)
        if name != 'status':
            action.add_argument('--event', required=True, type=Path)
        if name not in {'configure', 'status'}:
            action.add_argument('--unit', required=True)
            action.add_argument('--agent-id', required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    path = inside(root, f'.research/work/{safe_id(args.run)}/workflow.json')
    with locked(root, f'workflow:{args.run}'):
        state = read_json(path)
        if state.get('run_id') != args.run or state.get('schema_version') != 2:
            raise ValueError('workflow身份或版本不符')
        if args.action == 'status':
            return status(state)
        event = read_json(inside(root, args.event))
        if args.action == 'configure':
            value = configure(state, event)
        elif args.action == 'reserve':
            value = reserve(root, state, args.unit, args.agent_id, event)
        else:
            value = transition(root, state, args.unit, args.agent_id, args.action, event)
        state['updated'] = utc_now()
        write_json(path, state)
        return value
