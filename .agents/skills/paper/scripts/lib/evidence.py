"""Small, source-bound evidence locators for selective cross-context retrieval.

The index stores no source text or scientific judgment. It never substitutes for
viewing consequential figures, formulas, or tables in the original PDF.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import default_root, digest, inside, read_json, safe_id, write_json


def _index_path(root: Path, run: str, unit: str) -> Path:
    return inside(root, f'.research/work/{safe_id(run)}/units/{safe_id(unit)}/evidence-index.json')


def _workflow_unit(root: Path, run: str, unit: str) -> dict:
    state = read_json(inside(root, f'.research/work/{safe_id(run)}/workflow.json'))
    try:
        return state['units'][safe_id(unit)]
    except KeyError as exc:
        raise ValueError('索引所属 unit 不在现行 workflow 中') from exc


def _manifest(root: Path, path: str, bound: dict | None = None) -> dict:
    location = inside(root, path)
    if not location.is_relative_to(root / '.research/sources') or location.suffix != '.json':
        raise ValueError('证据 manifest 必须是 .research/sources 中的固定记录')
    raw = location.read_bytes()
    if bound and bound.get(path) != digest(raw):
        raise ValueError('manifest 未绑定当前 unit 或字节哈希改变')
    data = json.loads(raw)
    source = inside(root, data['source_path'])
    if digest(source.read_bytes()) != data['source_sha256']:
        raise ValueError('固定原文哈希改变')
    if data.get('source_kind', 'pdf') != 'pdf':
        raise ValueError('当前证据索引仅支持物理 PDF 页')
    return data


def _slice(data: dict, page: int, start: int, end: int) -> str:
    blocks = [block for block in data['blocks'] if block.get('page') == page]
    if len(blocks) != 1:
        raise ValueError(f'物理页 {page} 无唯一解析块')
    block = blocks[0]
    content = block['text']
    if digest(content) != block['text_sha256']:
        raise ValueError(f'物理页 {page} 解析块哈希改变')
    if not (isinstance(start, int) and not isinstance(start, bool)
            and isinstance(end, int) and not isinstance(end, bool)
            and 0 <= start < end <= len(content) and end - start <= 5000):
        raise ValueError('证据窗口须为有效页内字符区间，单项最多 5000 字符')
    return content[start:end]


def build(root: Path, run: str, unit: str, spec: str) -> dict:
    root = Path(root).resolve()
    current = _workflow_unit(root, run, unit)
    bound = {item['path']: item['sha256'] for item in current['inputs']}
    windows = read_json(inside(root, spec))
    if not isinstance(windows, list) or not 1 <= len(windows) <= 100:
        raise ValueError('spec 须为 1..100 项窗口列表')
    entries, manifests = {}, {}
    for window in windows:
        if not isinstance(window, dict):
            raise ValueError('窗口须为对象')
        ident = safe_id(window.get('id'))
        if ident in entries:
            raise ValueError('证据 ID 重复')
        manifest = window.get('manifest')
        if not isinstance(manifest, str) or manifest not in bound:
            raise ValueError('manifest 必须绑定当前 unit 固定输入')
        if manifest not in manifests:
            manifests[manifest] = _manifest(root, manifest, bound)
        data = manifests[manifest]
        page, start, end = window.get('page'), window.get('start'), window.get('end')
        excerpt = _slice(data, page, start, end)
        purpose = window.get('purpose')
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 160:
            raise ValueError('purpose 须为 1..160 字的具体用途')
        entries[ident] = {'manifest': manifest, 'manifest_sha256': bound[manifest],
                          'source_sha256': data['source_sha256'], 'page': page,
                          'start': start, 'end': end, 'text_sha256': digest(excerpt),
                          'purpose': purpose.strip()}
    target = _index_path(root, run, unit)
    write_json(target, {'format': 'paper-evidence-index-v1', 'run': run, 'unit': unit,
                        'entries': entries})
    return {'index': str(target.relative_to(root)), 'entries': len(entries),
            'stored_source_text': False, 'limitation': '定位索引不是阅读或原页核证'}


def get(root: Path, run: str, unit: str, ids: str, max_chars: int = 12000) -> dict:
    root = Path(root).resolve()
    current = _workflow_unit(root, run, unit)
    bound = {item['path']: item['sha256'] for item in current['inputs']}
    index = read_json(_index_path(root, run, unit))
    if index.get('format') != 'paper-evidence-index-v1' or index.get('run') != run or index.get('unit') != unit:
        raise ValueError('证据索引归属或格式不匹配')
    wanted = [safe_id(value.strip()) for value in ids.split(',')]
    if not wanted or len(wanted) > 20 or len(set(wanted)) != len(wanted):
        raise ValueError('一次只取 1..20 个不重复证据 ID')
    if not 1 <= max_chars <= 30000:
        raise ValueError('max_chars 须为 1..30000')
    results, manifests, total = [], {}, 0
    for ident in wanted:
        try:
            entry = index['entries'][ident]
        except KeyError as exc:
            raise ValueError(f'未知证据 ID: {ident}') from exc
        manifest = entry['manifest']
        if bound.get(manifest) != entry['manifest_sha256']:
            raise ValueError(f'{ident} 的 manifest 绑定已失效')
        if manifest not in manifests:
            manifests[manifest] = _manifest(root, manifest, bound)
        data = manifests[manifest]
        if data['source_sha256'] != entry['source_sha256']:
            raise ValueError(f'{ident} 的固定原文已改变')
        excerpt = _slice(data, entry['page'], entry['start'], entry['end'])
        if digest(excerpt) != entry['text_sha256']:
            raise ValueError(f'{ident} 的文本片段已改变')
        total += len(excerpt)
        if total > max_chars:
            raise ValueError('所选片段超过 max_chars；缩小 ID 集合')
        results.append({'id': ident, 'purpose': entry['purpose'], 'page': entry['page'],
                        'path': data['source_path'] + '#page=' + str(entry['page']),
                        'text': excerpt})
    return {'run': run, 'unit': unit, 'results': results, 'returned_chars': total,
            'limitation': '仅返回解析文本；关键图表/公式及影响结论的来源须回原页核证'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='research.py evidence', description=__doc__)
    parser.add_argument('--root', type=Path, default=default_root())
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('build', 'get'):
        command = actions.add_parser(action)
        command.add_argument('--run', required=True)
        command.add_argument('--unit', required=True)
        if action == 'build':
            command.add_argument('--spec', required=True)
        else:
            command.add_argument('--ids', required=True)
            command.add_argument('--max-chars', type=int, default=12000)
    args = parser.parse_args(argv)
    return build(args.root, args.run, args.unit, args.spec) if args.action == 'build' else get(
        args.root, args.run, args.unit, args.ids, args.max_chars)
