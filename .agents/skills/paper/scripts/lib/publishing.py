"""Content-bound review, visible Obsidian projections and conflict-aware publication."""
from __future__ import annotations
import argparse
from copy import deepcopy
import difflib
import json
from pathlib import Path
import re
import shutil

from .core import atomic_text, digest, inside, locked, read_json, safe_id, utc_now, write_json
from .notes import FOLDERS, check, check_changes, rebase_markdown, validate_note_frontmatter, visible_markdown, wiki_parts, wiki_target


def target_path(root, value):
    path = inside(root, value)
    if path.suffix != '.md' or (path != root / '首页.md' and not any(
            path.is_relative_to(root / folder) for folder in FOLDERS)):
        raise ValueError('正式目标仅为论文/知识/专题的Markdown及首页')
    return path


def proposal_path(root, proposal):
    return inside(root, f'.research/drafts/{safe_id(proposal)}/proposal.json')


def payload_digest(entries):
    return digest(json.dumps(entries, ensure_ascii=False, sort_keys=True))


def workflow_gate(root, run, entries, origins=None):
    """Bind a research proposal to current reviewed candidates, never an integration string."""
    from .workflow import candidates, accepted_binding, state_path, archived_state_path
    path = state_path(root, run)
    if not path.is_file():
        if path.parent.exists() or archived_state_path(root, run).exists():
            raise ValueError('研究运行缺有效工作状态或已归档，不能绕过科学交付门')
        return {'mode': 'direct_edit', 'scope': '实际用户审阅当前改动；无研究工作流科学验收声明'}
    state = read_json(path)
    if state.get('run_id') != run or state.get('schema_version') != 2:
        raise ValueError('关联工作流身份或版本不符')
    group = candidates(root, state)
    mapping = group['mapping']
    selected = [entry for entry in entries if entry['path'] != '首页.md']
    for entry in selected:
        candidate = mapping.get(entry['path'])
        if not candidate or entry['content'] is None:
            raise ValueError('关联研究的正式改动没有唯一已核验候选: ' + entry['path'])
        draft = inside(root, candidate['draft'])
        raw = draft.read_bytes()
        if digest(raw) != candidate['sha256']:
            raise ValueError('科学验收候选已改变: ' + entry['path'])
        if origins and origins.get(entry['path']) != {'draft': candidate['draft'], 'sha256': candidate['sha256']}:
            raise ValueError('提案来源绑定与当前科学候选不同，请重新整合/暂存')
        paths = {inside(root, value['draft']): target_path(root, target) for target, value in mapping.items()}
        content = rebase_markdown(root, raw.decode('utf-8'), draft, target_path(root, entry['path']), paths)
        content = rewrite_wiki(root, content, draft, paths)
        if content != entry['content']:
            raise ValueError('待发布正文不同于科学核验候选；更新受影响候选并重新核验: ' + entry['path'])
    return {'mode': 'workflow', 'run': run,
            'unit_bindings': {ident: accepted_binding(unit) for ident, unit in state['units'].items()},
            'candidate_hashes': {entry['path']: mapping[entry['path']]['sha256'] for entry in selected},
            'origins': {entry['path']: {'draft': mapping[entry['path']]['draft'],
                                      'sha256': mapping[entry['path']]['sha256']} for entry in selected}}


def load_proposal(root, proposal):
    record = read_json(proposal_path(root, proposal))
    if record.get('schema_version') != 1 or payload_digest(record['entries']) != record['digest']:
        raise ValueError('候选记录损坏或不属于新版审阅协议；原稿保留')
    return record


def rewrite_wiki(root, content, origin, mapping):
    """Rewrite only visible Wiki destinations, preserving aliases and block fragments."""
    edits = []
    for match in re.finditer(r'\[\[([^\]]+)\]\]', visible_markdown(content)):
        destination, divider, alias = wiki_parts(match[1])
        name, hashmark, anchor = destination.partition('#')
        path = wiki_target(root, origin, name, {value: '' for value in mapping}) if name else origin
        if path not in mapping:
            continue
        mapped = mapping[path]
        text = str(mapped.relative_to(root))
        if mapped.suffix == '.md':
            text = text[:-3]
        text += (hashmark + anchor if hashmark else '') + (divider + alias if divider else '')
        edits.append((match.start(1), match.end(1), text))
    for start, end, text in reversed(edits):
        content = content[:start] + text + content[end:]
    return content


def add_review_properties(content, target):
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', content, re.S)
    if not match:
        return content
    if re.search(r'^(?:review_status|formal_target):', match[1], re.M):
        raise ValueError('候选正式正文不能含待审投影属性')
    front = match[1] + '\nreview_status: 待审\nformal_target: ' + json.dumps(target, ensure_ascii=False)
    return '---\n' + front + '\n---\n' + content[match.end():]


def remove_review_properties(content):
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', content, re.S)
    if not match:
        return content
    lines = [line for line in match[1].splitlines()
             if not re.match(r'^(?:review_status|formal_target):', line)]
    return '---\n' + '\n'.join(lines) + '\n---\n' + content[match.end():]


def projection(root, entry, preview, mapping):
    origin = target_path(root, entry['path'])
    text = rebase_markdown(root, entry['content'], origin, preview, mapping)
    text = rewrite_wiki(root, text, origin, mapping)
    return add_review_properties(text, entry['path'])


def view_directory(root, record):
    return inside(root, f'审阅/{record["proposal"]}/r{record["revision"]:04d}')


def configure_views(record):
    directory = f'审阅/{record["proposal"]}/r{record["revision"]:04d}'
    record['overview'] = directory + '/总览.md'
    record['previews'] = {entry['path']: f'{directory}/{index:02d}-{Path(entry["path"]).name}'
                          for index, entry in enumerate(record['entries'], 1)
                          if entry['content'] is not None}
    record['canonical_directory'] = (f'.research/drafts/{record["proposal"]}/'
                                     f'canonical/r{record["revision"]:04d}')


def write_views(root, record):
    """Generate a new revision; never replace an existing editable projection."""
    proposal = record['proposal']
    directory = view_directory(root, record)
    canonical = inside(root, record['canonical_directory'])
    if directory.exists() or canonical.exists():
        raise ValueError('审阅修订目录已存在；保留原稿，请使用新修订')
    mapping = {target_path(root, entry['path']): inside(root, record['previews'][entry['path']])
               for entry in record['entries'] if entry['content'] is not None}
    prepared = {}
    overview = ['# 待审改动', '', '状态：待审。正文类型、原始证据和讨论仍按正常笔记阅读。', '',
                '可编辑下列候选页；编辑后先合并回候选并重新查看差异，再按实际授权提交。', '']
    for index, entry in enumerate(record['entries'], 1):
        target = target_path(root, entry['path'])
        old = target.read_text(encoding='utf-8') if target.exists() else ''
        content = entry['content'] or ''
        diff = ''.join(difflib.unified_diff(old.splitlines(True), content.splitlines(True),
                                           fromfile=entry['path'], tofile=entry['path']))
        prepared[directory / f'{index:02d}.diff'] = diff
        if entry['content'] is None:
            overview.append('- 删除：' + entry['path'])
            continue
        prepared[canonical / f'{index:02d}.md'] = entry['content']
        preview = mapping[target]
        prepared[preview] = projection(root, entry, preview, mapping)
        destination = str(preview.relative_to(root))[:-3]
        overview.append(f'- [[{destination}|{target.stem}]]：正式目标 `{entry["path"]}`')
    overview.extend(['', '## 改动说明与尚未确定的部分', '',
                     record.get('summary') or '由协调者结合具体差异说明机制、证据与判断的实质变化。', '',
                     '差异文件与候选放在同一审阅目录；批准对象是当前候选内容，内容摘要本身不构成授权。', ''])
    prepared[directory / '总览.md'] = '\n'.join(overview)
    record['preview_hashes'] = {path: digest(prepared[inside(root, path)])
                                for path in record['previews'].values()}
    record['canonical_hashes'] = {f'{index:02d}.md': digest(entry['content'])
                                  for index, entry in enumerate(record['entries'], 1)
                                  if entry['content'] is not None}
    # The baseline binds generated bytes, never edits that happen during IO.
    previous = record.get('status', 'pending')
    record['status'] = 'preparing'
    write_json(proposal_path(root, proposal), record)
    try:
        for path, content in prepared.items():
            atomic_text(path, content)
    except Exception as exc:
        record.update(status='staging_failed', staging_failure=str(exc))
        write_json(proposal_path(root, proposal), record)
        raise ValueError('生成审阅视图失败，候选及局部视图已保留；可show后discard并使用新提案: ' + str(exc)) from exc
    record['status'] = previous
    write_json(proposal_path(root, proposal), record)


def stage(root, changes, proposal, run=None, summary=None, origins=None):
    root = Path(root).resolve()
    safe_id(proposal)
    safe_id(run or proposal)
    if not isinstance(changes, dict) or not changes:
        raise ValueError('changes须为非空 正式路径→正文/null 映射')
    with locked(root, f'workflow:{run or proposal}'), locked(root, 'review-publish'):
        path = proposal_path(root, proposal)
        if path.parent.exists() or inside(root, f'审阅/{proposal}').exists():
            raise ValueError('提案标识已存在；原待审视图保留，请使用新标识或import当前编辑')
        entries = []
        for rel, content in changes.items():
            target = target_path(root, rel)
            if content is not None and not isinstance(content, str):
                raise ValueError('候选正文须为字符串，删除用null')
            if content is not None and target.name != '首页.md':
                meta = validate_note_frontmatter(content, rel)
                if meta.get('kind') not in {'paper', 'knowledge', 'comparison', 'topic'}:
                    raise ValueError('新版候选给出明确kind；旧正式页无需批量改写')
                from .semantics import body_presentation_issues
                style = body_presentation_issues(content)
                if style:
                    raise ValueError('候选正文展示需调整: ' + '; '.join(style))
            entries.append({'path': str(target.relative_to(root)),
                            'before': digest(target.read_bytes()) if target.exists() else None,
                            'content': content})
        scientific_binding = workflow_gate(root, run or proposal, entries, origins)
        report = check_changes(root, {entry['path']: entry['content'] for entry in entries})
        if report['errors']:
            raise ValueError('正式/入链预检失败: ' + json.dumps(report['issues'], ensure_ascii=False))
        record = {'schema_version': 1, 'proposal': proposal, 'run': run or proposal,
                  'entries': entries, 'digest': payload_digest(entries), 'revision': 1,
                  'status': 'pending', 'created_at': utc_now(), 'summary': summary,
                  'origins': origins or scientific_binding.get('origins', {}),
                  'retired_preview_hashes': {}, 'validation': report,
                  'scientific_binding': scientific_binding}
        configure_views(record)
        write_views(root, record)
        return {'proposal': proposal, 'digest': record['digest'], 'revision': 1,
                'overview': record['overview'], 'previews': record['previews'],
                'historical_errors': report['historical_errors'], 'validation_scope': report['validation_scope'],
                'limitation': '机械预检已完成；科学核验和实际授权由协调者依据真实结果处理。'}


def stage_drafts(root, drafts, proposal, run=None, summary=None):
    root = Path(root).resolve()
    if not isinstance(drafts, dict) or not drafts:
        raise ValueError('drafts须为非空 正式路径→内部草稿路径 映射')
    paths = {target: inside(root, draft) for target, draft in drafts.items()}
    if len(set(paths.values())) != len(paths):
        raise ValueError('一份草稿不能同时对应多个正式目标；请形成各自可审阅正文')
    for path in paths.values():
        if path.suffix != '.md' or not path.is_relative_to(root / '.research/drafts'):
            raise ValueError('草稿必须是.research/drafts中的Markdown文件')
    snapshots = {target: path.read_bytes() for target, path in paths.items()}
    mapping = {path: target_path(root, target) for target, path in paths.items()}
    changes = {target: rewrite_wiki(root, rebase_markdown(root, snapshots[target].decode('utf-8'),
                                path, target_path(root, target), mapping), path, mapping)
               for target, path in paths.items()}
    origins = {target: {'draft': str(path.relative_to(root)), 'sha256': digest(snapshots[target])}
               for target, path in paths.items()}
    if any(path.read_bytes() != snapshots[target] for target, path in paths.items()):
        raise ValueError('暂存期间草稿发生变化；原文件保留，请重新核对具体候选')
    return stage(root, changes, proposal, run, summary, origins)


def show(root, proposal):
    root = Path(root).resolve()
    record = load_proposal(root, proposal)
    edited = [path for path, value in record.get('preview_hashes', {}).items()
              if not inside(root, path).is_file() or digest(inside(root, path).read_bytes()) != value]
    conflicts = [entry['path'] for entry in record['entries']
                 if (digest(target_path(root, entry['path']).read_bytes())
                     if target_path(root, entry['path']).exists() else None) != entry['before']]
    retired = [path for path, value in record.get('retired_preview_hashes', {}).items()
               if not inside(root, path).is_file() or digest(inside(root, path).read_bytes()) != value]
    return {'proposal': proposal, 'status': record['status'], 'digest': record['digest'],
            'revision': record['revision'], 'overview': record.get('overview', f'审阅/{proposal}/总览.md'),
            'edited_previews': edited, 'edited_retired_previews': retired,
            'formal_conflicts': conflicts, 'previews': record['previews']}


def assert_canonical(root, record):
    directory = inside(root, record.get('canonical_directory', f'.research/drafts/{record["proposal"]}/canonical'))
    for name, expected in record['canonical_hashes'].items():
        path = directory / name
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError('内部候选被修改，请明确重新合并并暂存；不能忽略它发布旧正文')


def import_edits(root, proposal, acknowledge_retired=None):
    root = Path(root).resolve()
    initial_run = safe_id(load_proposal(root, proposal)['run'])
    with locked(root, f'workflow:{initial_run}'), locked(root, 'review-publish'):
        record = load_proposal(root, proposal)
        if record['run'] != initial_run:
            raise ValueError('提案关联运行在加锁前改变，保留原稿重新核对')
        if record['status'] != 'pending':
            raise ValueError('仅合并待审提案')
        assert_canonical(root, record)
        report = show(root, proposal)
        if report['edited_retired_previews'] and not (acknowledge_retired and acknowledge_retired.strip()):
            raise ValueError('旧修订在合并后又被编辑；先将这些修改与当前候选人工合并，原视图保留: ' +
                             json.dumps(report['edited_retired_previews'], ensure_ascii=False))
        if report['formal_conflicts']:
            raise ValueError('正式页已改变，需先保留用户修改并重新合并: ' +
                             json.dumps(report['formal_conflicts'], ensure_ascii=False))
        if not report['edited_previews'] and not report['edited_retired_previews']:
            return dict(report, imported=False)
        mapping = {inside(root, preview): target_path(root, target)
                   for target, preview in record['previews'].items()}
        snapshots = {preview: inside(root, preview).read_bytes() for preview in record['previews'].values()}
        retired_snapshots = {}
        for path in report['edited_retired_previews']:
            if not inside(root, path).is_file():
                raise ValueError('旧修订已删除，先恢复并核对内容: ' + path)
            retired_snapshots[path] = inside(root, path).read_bytes()
        entries = []
        for entry in record['entries']:
            updated = dict(entry)
            if entry['content'] is not None:
                preview = inside(root, record['previews'][entry['path']])
                if not preview.is_file():
                    raise ValueError('候选投影被删除；删除正式页须作为明确提案，不能由视图文件消失推断')
                if str(preview.relative_to(root)) in report['edited_previews']:
                    content = remove_review_properties(snapshots[str(preview.relative_to(root))].decode('utf-8'))
                    content = rebase_markdown(root, content, preview, target_path(root, entry['path']), mapping)
                    updated['content'] = rewrite_wiki(root, content, preview, mapping)
            entries.append(updated)
        check_report = check_changes(root, {entry['path']: entry['content'] for entry in entries})
        if check_report['errors']:
            raise ValueError('编辑后的候选存在完整性问题，保留原视图: ' +
                             json.dumps(check_report['issues'], ensure_ascii=False))
        for path, raw in (snapshots | retired_snapshots).items():
            if not inside(root, path).is_file() or inside(root, path).read_bytes() != raw:
                raise ValueError('合并期间发现新编辑，原视图保留，请重新import: ' + path)
        archive = inside(root, f'.research/records/proposals/{proposal}/revision-{record["revision"]:04d}.json')
        write_json(archive, record)
        record = deepcopy(record)
        record.setdefault('retired_preview_hashes', {}).update({path: digest(raw) for path, raw in snapshots.items()})
        if retired_snapshots:
            record['retired_preview_hashes'].update({path: digest(raw) for path, raw in retired_snapshots.items()})
            record.setdefault('merge_history', []).append({'at': utc_now(),
                'reason': acknowledge_retired.strip(), 'retired_snapshots': {
                    path: {'sha256': digest(raw), 'content': raw.decode('utf-8')}
                    for path, raw in retired_snapshots.items()}})
        record['entries'] = entries
        # Import preserves user edits; publication must rebind their scientific changes.
        record['scientific_binding'] = None
        record['validation'] = check_report
        record['digest'] = payload_digest(entries)
        record['revision'] += 1
        record['edited_at'] = utc_now()
        configure_views(record)
        write_views(root, record)
        return dict(show(root, proposal), imported=True, authorization_required_for_new_content=True)


def publish(root, proposal, approved_digest, authorization):
    root = Path(root).resolve()
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('需记录实际用户授权或既有授权范围；摘要不是授权')
    initial_run = safe_id(load_proposal(root, proposal)['run'])
    with locked(root, f'workflow:{initial_run}'), locked(root, 'review-publish'):
        record = load_proposal(root, proposal)
        if record['run'] != initial_run:
            raise ValueError('提案关联运行在加锁前改变，保留原稿重新核对')
        if record['status'] != 'pending' or record['digest'] != approved_digest:
            raise ValueError('批准的候选已改变或已处理，请核对最新具体内容')
        assert_canonical(root, record)
        status = show(root, proposal)
        if status['edited_previews'] or status['edited_retired_previews']:
            raise ValueError('Obsidian候选已编辑，先import并重新查看具体差异')
        if status['formal_conflicts']:
            raise ValueError('正式页在审阅期间改变，保留修改并重新合并: ' +
                             json.dumps(status['formal_conflicts'], ensure_ascii=False))
        current_binding = workflow_gate(root, record['run'], record['entries'], record.get('origins'))
        if current_binding['mode'] == 'workflow' and current_binding != record.get('scientific_binding'):
            raise ValueError('科学候选/复核已改变或候选编辑后未重新绑定，重新暂存具体候选')
        baseline_report = check(root)
        report = check_changes(root, {entry['path']: entry['content'] for entry in record['entries']}, baseline=baseline_report)
        if report['errors']:
            raise ValueError('正式/入链预检失败: ' + json.dumps(report['issues'], ensure_ascii=False))
        history = inside(root, f'.research/records/commits/{proposal}')
        if (history / 'commit.json').exists():
            raise ValueError('提交记录已存在')
        attempts = history / 'attempts'
        number = max([int(path.name) for path in attempts.glob('*') if path.is_dir() and path.name.isdigit()], default=0) + 1
        attempt = attempts / f'{number:04d}'
        originals = {target_path(root, entry['path']):
                     target_path(root, entry['path']).read_bytes()
                     if target_path(root, entry['path']).exists() else None for entry in record['entries']}
        # Bind exact preimages immediately before writes; Obsidian does not participate in our lock.
        for entry in record['entries']:
            raw = originals[target_path(root, entry['path'])]
            if (digest(raw) if raw is not None else None) != entry['before']:
                raise ValueError('写入前发现正式正文冲突: ' + entry['path'])
        for path, raw in originals.items():
            if raw is not None:
                backup = attempt / 'before' / path.relative_to(root)
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(raw)
        changed = []
        try:
            for entry in record['entries']:
                path = target_path(root, entry['path'])
                latest = path.read_bytes() if path.exists() else None
                if latest != originals[path]:
                    raise ValueError('提交过程中发现外部修改: ' + entry['path'])
                if entry['content'] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_text(path, entry['content'])
                changed.append(path)
            report = check_changes(root, {entry['path']: entry['content'] for entry in record['entries']}, baseline=baseline_report, actual=True)
            if report['errors']:
                raise ValueError('提交后完整性检查失败: ' + json.dumps(report['issues'], ensure_ascii=False))
        except Exception as exc:
            unresolved = []
            for path in reversed(changed):
                entry = next(entry for entry in record['entries'] if target_path(root, entry['path']) == path)
                expected = entry['content'].encode('utf-8') if entry['content'] is not None else None
                current = path.read_bytes() if path.exists() else None
                if current != expected:
                    unresolved.append(str(path.relative_to(root)))
                    continue  # Preserve edits made outside this transaction.
                if originals[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_text(path, originals[path].decode('utf-8'))
            write_json(attempt / 'failure.json', {'at': utc_now(), 'reason': str(exc),
                                                  'external_edits_preserved': unresolved})
            raise
        record['status'] = 'applied'
        record['applied_at'] = utc_now()
        record['authorization'] = authorization.strip()
        record['validation'] = report
        record['scientific_binding'] = current_binding
        commit = {key: record[key] for key in ('proposal', 'run', 'digest', 'revision', 'applied_at', 'authorization')}
        commit['entries'] = [{'path': entry['path'], 'before': entry['before'],
                             'after': digest(entry['content']) if entry['content'] is not None else None}
                            for entry in record['entries']]
        commit['attempt'] = str(attempt.relative_to(root))
        write_json(history / 'commit.json', commit)
        write_json(proposal_path(root, proposal), record)
        return {'applied': [str(path.relative_to(root)) for path in changed],
                'commit': str((history / 'commit.json').relative_to(root)),
                'limitation': '结构检查通过；Obsidian不提供共同事务锁，界面与科学结论另需实际核验。'}


def discard(root, proposal, reason):
    root = Path(root).resolve()
    if not reason.strip():
        raise ValueError('明确废弃原因')
    with locked(root, 'review-publish'):
        record = load_proposal(root, proposal)
        if record['status'] not in {'pending', 'preparing', 'staging_failed'}:
            raise ValueError('仅废弃待审提案')
        record.update(status='discarded', discarded_at=utc_now(), discard_reason=reason)
        write_json(proposal_path(root, proposal), record)
    return {'proposal': proposal, 'status': 'discarded', 'views_preserved_until_finish': True}


def managed_directory(root, relative):
    """Check lexical ownership before resolution so a link cannot redirect cleanup."""
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('管理目录须为库内明确相对路径')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('管理目录路径包含符号链接，保留文件: ' + str(relative))
    return inside(root, relative)


def directory_snapshot(path):
    result = {}
    if not path.exists():
        return result
    for child in path.rglob('*'):
        if child.is_symlink():
            raise ValueError('管理目录含符号链接，保留目录: ' + str(path))
        if child.is_file():
            result[str(child.relative_to(path))] = digest(child.read_bytes())
    return result


def cleanup_archived(root, durable, journal):
    """Resume an interrupted removal only while every remaining byte matches its archive boundary."""
    paths = [managed_directory(root, path) for path in journal['cleanup']]
    for path in paths:
        relative = str(path.relative_to(root))
        expected = journal['cleanup_snapshots'][relative]
        current = directory_snapshot(path)
        if any(name not in expected or expected[name] != value for name, value in current.items()):
            raise ValueError('归档后目录有新编辑，保留它并核对档案: ' + relative)
    for copy in journal['copies']:
        destination = managed_directory(root, copy['destination'])
        valid = (destination.is_file() and digest(destination.read_bytes()) == copy['sha256']) if copy.get('kind') == 'file' else directory_snapshot(destination) == copy['files']
        if not valid:
            raise ValueError('持久档案不完整，不清理工作文件: ' + copy['destination'])
    removed = list(journal.get('removed', []))
    for path in paths:
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path.relative_to(root)))
            journal['removed'] = removed
            write_json(durable / 'archive.json', journal)
    journal.update(status='complete', completed_at=utc_now(), removed=removed)
    write_json(durable / 'archive.json', journal)
    return {'run': journal['run'], 'removed': removed, 'durable_record': str(durable.relative_to(root)),
            'preserved': ['固定来源与版本', '来源定位及覆盖', '查询与筛选集合', '接受产物与判断更新', '完整审阅修订与提交记录']}


def finish(root, run, discard_unpublished=False, abandon_reason=None):
    """Keep provenance, queries, evidence and cognitive updates; remove only approved temporary material."""
    root = Path(root).resolve()
    safe_id(run)
    if abandon_reason is not None and not str(abandon_reason).strip():
        raise ValueError("废弃中断运行需要具体的用户授权及原因")
    with locked(root, f'workflow:{run}'), locked(root, 'review-publish'):
        durable = managed_directory(root, f'.research/records/runs/{run}')
        journal_path = durable / 'archive.json'
        if journal_path.is_file():
            journal = read_json(journal_path)
            if journal.get('run') != run or journal.get('archive_version') != 1:
                raise ValueError('清理档案身份不符，保留目录')
            if journal.get('status') == 'complete':
                return {'run': run, 'status': 'already_finished', 'durable_record': str(durable.relative_to(root))}
            if journal.get('status') == 'archived_before_cleanup':
                return cleanup_archived(root, durable, journal)
            raise ValueError('归档未完成，保留工作与候选，请核对档案')
        records = []
        for path in sorted((root / '.research/drafts').glob('*/proposal.json')):
            data = read_json(path)
            if data.get('schema_version') == 1 and data.get('run') == run:
                records.append(load_proposal(root, data['proposal']))
        work = managed_directory(root, f'.research/work/{run}')
        state = work / 'workflow.json'
        data = read_json(state) if state.is_file() else None
        if work.exists() and data is None:
            raise ValueError('工作目录没有可核验的新版状态，保留它而不推测归属')
        if abandon_reason is not None and data is None:
            raise ValueError('没有当前工作流状态，不能废弃推测归属的文件')
        if not records and data is None:
            raise ValueError('没有新版研究状态或已处理提案；旧待审稿和来源草稿保留')
        if data is not None:
            if data.get('schema_version') != 2 or data.get('run_id') != run:
                raise ValueError('不是当前运行的新版状态，不清理')
            if abandon_reason is not None:
                if any(unit.get('owner_active') or any(stage.get('status') in {'executing', 'awaiting_review'} for stage in unit['rounds']) for unit in data['units'].values()):
                    raise ValueError('仍有执行中的单元；先真实中断代理并登记workflow interrupt，再废弃运行')
                data['abandonment'] = {'at': utc_now(), 'reason': str(abandon_reason).strip(),
                                       'scientific_status_preserved': True}
            elif (any(unit.get('owner_active') for unit in data['units'].values()) or
                  any(helper.get('status') in {'reserved', 'bound'}
                      for helper in data.get('runtime', {}).get('helpers', {}).values())):
                raise ValueError('仍有科学owner交付或助手运行/预留未结束，保留工作文件')
            from .workflow import candidates as workflow_candidates
            # Published bytes replace the pre-publication target baseline. Below,
            # exact proposal/commit/current-byte checks supply the cleanup gate.
            group = workflow_candidates(root, data, check_target_baselines=False) if abandon_reason is None else {'mapping': {}}
            if abandon_reason is None and not data.get('integration'):
                raise ValueError('先用workflow candidates --finding记录全组语义与知识增量核验')
            for target, candidate in group['mapping'].items():
                bound = [record for record in records if record.get('origins', {}).get(target) == {
                    'draft': candidate['draft'], 'sha256': candidate['sha256']}]
                if not bound:
                    raise ValueError('仍有最终候选未作为具体提案处理，保留全部任务文件: ' + target)
        latest = {}
        for record in records:
            if record['status'] == 'pending' or (record['status'] == 'discarded' and not discard_unpublished):
                raise ValueError('待审未处理或废弃未明确允许清理；不删除')
            if record['status'] not in {'applied', 'discarded'}:
                raise ValueError('未知状态，不清理')
            if record['status'] == 'applied':
                commit = read_json(inside(root, f'.research/records/commits/{record["proposal"]}/commit.json'))
                for key in ('proposal', 'run', 'digest', 'revision', 'authorization'):
                    if commit.get(key) != record.get(key):
                        raise ValueError('提交记录与提案不一致，保留恢复文件: ' + record['proposal'])
                expected_entries = [{'path': entry['path'], 'before': entry['before'],
                    'after': digest(entry['content']) if entry['content'] is not None else None}
                    for entry in record['entries']]
                if commit.get('entries') != expected_entries:
                    raise ValueError('提交记录正文摘要不一致，保留恢复文件: ' + record['proposal'])
                for entry in record['entries']:
                    previous = latest.get(entry['path'])
                    if previous is None or record['applied_at'] > previous[0]:
                        latest[entry['path']] = (record['applied_at'], entry['content'])
        for relative, (_, content) in latest.items():
            path = target_path(root, relative)
            current = path.read_text(encoding='utf-8') if path.exists() else None
            if current != content:
                raise ValueError('正式内容已偏离最新批准稿，保留恢复文件: ' + relative)
        cleanup = []
        archives = {}
        if data is not None:
            cleanup.extend([work, managed_directory(root, f'.research/drafts/{run}'),
                            managed_directory(root, f'.research/cache/runs/{run}')])
            # Explicit abandonment retains the complete audit without inventing acceptance.
            if abandon_reason is not None:
                archives[work] = durable / 'work'
            # Accepted answers, evidence links and content-addressed histories are research records.
            for unit in (() if abandon_reason is not None else data['units'].values()):
                for area in ('results', 'versions'):
                    folder = work / 'units' / unit['id'] / area
                    if folder.exists():
                        archives[folder] = durable / 'work' / folder.relative_to(work)
            for area in (() if abandon_reason is not None else ('updates', 'learning', 'recovery')):
                folder = work / area
                if folder.exists():
                    archives[folder] = durable / 'work' / area
            draft_folder = managed_directory(root, f'.research/drafts/{run}')
            if draft_folder.exists():
                archives[draft_folder] = durable / 'drafts'
        for record in records:
            for folder, destination in (
                    (managed_directory(root, f'.research/drafts/{record["proposal"]}'), durable / 'proposals' / record['proposal']),
                    (managed_directory(root, f'审阅/{record["proposal"]}'), durable / 'reviews' / record['proposal'])):
                cleanup.append(folder)
                if folder.exists():
                    archives[folder] = destination
        # Preflight every path before writing archives or removing any directory.
        cleanup = list(dict.fromkeys(cleanup))
        for path in cleanup + list(archives):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise ValueError('清理/归档目录身份异常，保留所有文件: ' + str(path.relative_to(root)))
            if path.exists() and any(child.is_symlink() for child in path.rglob('*')):
                raise ValueError('目录含符号链接，需明确处理后清理: ' + str(path.relative_to(root)))
        persistent = directory_snapshot(durable)
        if durable.exists():
            # Report/helper versions are written before finish and already
            # belong to this run. Accept only hash-bound versions; a partial
            # archive or unrelated file must still block cleanup.
            known = {}
            def persistent_refs(value):
                if isinstance(value, list):
                    for item in value:
                        persistent_refs(item)
                elif isinstance(value, dict):
                    location, expected = value.get('path'), value.get('sha256')
                    if isinstance(location, str) and isinstance(expected, str):
                        path = inside(root, location)
                        if path.is_relative_to(durable):
                            known[str(path.relative_to(durable))] = expected
                    for item in value.values():
                        persistent_refs(item)
            persistent_refs([data, *records])
            if (any(child.name not in {'reports', 'helpers'} or not child.is_dir() for child in durable.iterdir())
                    or any(known.get(name) != value for name, value in persistent.items())):
                raise ValueError('归档目标含未绑定记录或部分档案；保留原档案与工作文件')
        path_map = {str(folder.relative_to(root)): str(destination.relative_to(root))
                    for folder, destination in archives.items()}

        cleanup_snapshots = {str(path.relative_to(root)): directory_snapshot(path) for path in cleanup}
        available = {}
        for folder, files in cleanup_snapshots.items():
            for name, value in files.items():
                available.setdefault(value, []).append(inside(root, Path(folder) / name))
        file_archives, reference_map = {}, {}

        def register_references(value):
            if isinstance(value, list):
                for item in value:
                    register_references(item)
            elif isinstance(value, dict):
                for path_key, hash_key in (('path', 'sha256'), ('draft', 'sha256'), ('source_path', 'source_sha256')):
                    location, expected = value.get(path_key), value.get(hash_key)
                    if not isinstance(location, str) or not isinstance(expected, str):
                        continue
                    source = inside(root, location)
                    if not any(source.is_relative_to(folder) for folder in cleanup):
                        continue
                    choices = available.get(expected, [])
                    if not choices:
                        raise ValueError('历史引用没有对应的完整哈希版本，保留任务文件: ' + location)
                    chosen = source if source in choices else choices[0]
                    destination = durable / 'files' / expected / source.name
                    file_archives[chosen, destination] = expected
                    reference_map[location, expected] = str(destination.relative_to(root))
                for item in value.values():
                    register_references(item)

        register_references([data, *records])

        def remap(value):
            if isinstance(value, dict):
                mapped = {key: remap(item) for key, item in value.items()}
                for path_key, hash_key in (('path', 'sha256'), ('draft', 'sha256'), ('source_path', 'source_sha256')):
                    location, expected = value.get(path_key), value.get(hash_key)
                    if isinstance(location, str) and isinstance(expected, str) and (location, expected) in reference_map:
                        mapped[path_key] = reference_map[location, expected]
                return mapped
            if isinstance(value, list):
                return [remap(item) for item in value]
            if isinstance(value, str):
                for old in sorted(path_map, key=len, reverse=True):
                    if value == old or value.startswith(old + '/'):
                        return path_map[old] + value[len(old):]
            return value

        copies = [{'kind': 'file', 'destination': str((durable / name).relative_to(root)), 'sha256': value}
                  for name, value in persistent.items()]
        for folder, destination in archives.items():
            shutil.copytree(folder, destination)
            copied = directory_snapshot(destination)
            if copied != directory_snapshot(folder):
                raise ValueError('归档期间发现新编辑，原文件保留: ' + str(folder.relative_to(root)))
            copies.append({'source': str(folder.relative_to(root)),
                           'destination': str(destination.relative_to(root)), 'files': copied})
        for (source, destination), expected in file_archives.items():
            raw = source.read_bytes()
            if digest(raw) != expected:
                raise ValueError('归档期间引用版本被改变，原文件保留: ' + str(source.relative_to(root)))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            copies.append({'kind': 'file', 'source': str(source.relative_to(root)),
                           'destination': str(destination.relative_to(root)), 'sha256': expected})
        for record in records:
            path = durable / 'decisions' / (record['proposal'] + '.json')
            write_json(path, remap(record))
            copies.append({'kind': 'file', 'destination': str(path.relative_to(root)), 'sha256': digest(path.read_bytes())})
        if data is not None:
            path = durable / 'workflow.json'
            write_json(path, remap(data))
            copies.append({'kind': 'file', 'destination': str(path.relative_to(root)), 'sha256': digest(path.read_bytes())})
        journal = {'archive_version': 1, 'run': run, 'at': utc_now(), 'path_map': path_map,
            'cleanup': [str(path.relative_to(root)) for path in cleanup], 'cleanup_snapshots': cleanup_snapshots,
            'copies': copies, 'references': [{'original': path, 'sha256': value, 'archive': destination}
                for (path, value), destination in reference_map.items()], 'status': 'archived_before_cleanup'}
        write_json(durable / 'archive.json', journal)
        return cleanup_archived(root, durable, journal)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    actions = parser.add_subparsers(dest='action', required=True)
    action = actions.add_parser('stage')
    action.add_argument('--proposal', required=True)
    group = action.add_mutually_exclusive_group(required=True)
    group.add_argument('--drafts', type=Path)
    group.add_argument('--changes', type=Path)
    action.add_argument('--run')
    action.add_argument('--summary')
    for name in ('show', 'import', 'discard'):
        action = actions.add_parser(name)
        action.add_argument('--proposal', required=True)
        if name == 'discard':
            action.add_argument('--reason', required=True)
        if name == 'import':
            action.add_argument('--acknowledge-retired', help='协调者已将旧修订新编辑人工合并/明确处理的具体说明；不自动丢弃修改')
    args = parser.parse_args(argv)
    if args.action == 'stage':
        root = args.root.resolve()
        return (stage_drafts(root, read_json(root / args.drafts), args.proposal, args.run, args.summary)
                if args.drafts else stage(root, read_json(root / args.changes), args.proposal, args.run, args.summary))
    if args.action == 'show':
        return show(args.root, args.proposal)
    if args.action == 'import':
        return import_edits(args.root, args.proposal, args.acknowledge_retired)
    return discard(args.root, args.proposal, args.reason)
