# /// script
# requires-python = ">=3.11"
# ///
"""Compile a focused, portable Obsidian Canvas from a reviewed topic map.

Use the research.py canvas command. The input map is semantic data; this script
owns JSON Canvas syntax, link generation, and cross-vault path checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote


def visible_notes(vault: Path) -> list[Path]:
    return [p.resolve() for p in vault.rglob('*.md')
            if not any(part.startswith('.') for part in p.relative_to(vault).parts)]


def unique_link_target(vault: Path, target: Path) -> str:
    if not target.is_relative_to(vault):
        raise ValueError(f'目标不在 Obsidian 库内: {target} / {vault}')
    matches = {p for p in visible_notes(vault) if p.stem == target.stem}
    if matches != {target}:
        raise ValueError(f'短链接在库中缺失或重名: {target.stem}: {sorted(str(p) for p in matches)}')
    return target.stem


def compile_canvas(root: Path, spec: dict, other_vaults: list[Path]) -> dict:
    root = root.resolve()
    vaults = [root] + [p.resolve() for p in other_vaults]
    cards = spec.get('cards')
    edges = spec.get('edges')
    if not isinstance(cards, list) or not cards or not isinstance(edges, list):
        raise ValueError('关系图需要非空 cards 与 edges 列表')
    nodes, ids = [], set()
    for card in cards:
        ident = card['id']
        if not re.fullmatch(r'[a-z0-9-]+', ident) or ident in ids:
            raise ValueError(f'节点 ID 无效或重复: {ident}')
        ids.add(ident)
        relative = Path(card['target'])
        if relative.is_absolute() or '..' in relative.parts or relative.suffix != '.md':
            raise ValueError(f'目标必须是库根相对 Markdown 路径: {relative}')
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(f'节点目标不存在: {relative}')
        for vault in vaults:
            unique_link_target(vault, target)
        coords = [card[k] for k in ('x', 'y', 'width', 'height')]
        if any(type(v) is not int for v in coords) or coords[2] <= 0 or coords[3] <= 0:
            raise ValueError(f'节点坐标或尺寸无效: {ident}')
        title, summary = card['title'].strip(), card['summary'].strip()
        if not title or not summary or '\n' in title or '[[' in summary:
            raise ValueError(f'节点说明无效: {ident}')
        text = f'### {title}\n\n{summary}\n\n[[{target.stem}|打开对应页面]]'
        nodes.append(dict(id=ident, type='text', text=text,
                          x=coords[0], y=coords[1], width=coords[2], height=coords[3]))
    result_edges = []
    positions = {n['id']: (n['x'], n['y']) for n in nodes}
    for index, edge in enumerate(edges, 1):
        start, end, label = edge['from'], edge['to'], edge['label'].strip()
        if start not in ids or end not in ids or start == end or not label:
            raise ValueError(f'边目标或语义标签无效: {edge}')
        dx = positions[end][0] - positions[start][0]
        dy = positions[end][1] - positions[start][1]
        if abs(dx) >= abs(dy):
            from_side, to_side = ('right', 'left') if dx >= 0 else ('left', 'right')
        else:
            from_side, to_side = ('bottom', 'top') if dy >= 0 else ('top', 'bottom')
        result_edges.append(dict(id=f'edge-{index:03d}', fromNode=start, fromSide=from_side,
                                 toNode=end, toSide=to_side, label=label))
    return {'nodes': nodes, 'edges': result_edges}


def audit_canvas(vault: Path, data: dict, changes: dict | None = None,
                 pages_by_path: dict | None = None) -> list[str]:
    """Audit the proposed Markdown set, not only files on the current disk.

    Canvas owns no scientific facts. Its file/text destinations use the same
    native Wiki and heading/block checks as Markdown pages, with read-only
    candidate overlays and explicit proposed-deletion diagnostics.
    """
    from .core import inside
    from .notes import source_records, validate_link, visible_markdown, wiki_parts, wiki_target
    vault = vault.resolve()
    overrides = {inside(vault, value): text for value, text in (changes or {}).items()}
    removed = {path for path, text in overrides.items() if text is None}
    if pages_by_path is None:
        records, _ = source_records(vault)
        pages_by_path = {inside(vault, record['source_path']): record.get('page_count', 0)
                         for record in records.values()}
    problems = []
    # Root-level placeholder supplies a base for a malformed self-only Wiki;
    # a Canvas text node has no Markdown page of its own to anchor into.
    origin = vault / '首页.md'
    for node in data.get('nodes', []):
        try:
            if node.get('type') == 'file':
                relative = Path(node.get('file', ''))
                if not str(node.get('file', '')).strip() or relative.is_absolute() or '..' in relative.parts:
                    raise ValueError(f'文件卡目标须为库根相对路径: {relative}')
                target = inside(vault, relative)
                if target in removed:
                    raise ValueError(f'候选删除了仍被文件卡引用的目标: {relative}')
                if target not in overrides and not target.is_file():
                    raise ValueError(f'文件卡目标不存在: {relative}')
                subpath = node.get('subpath', '')
                if not isinstance(subpath, str) or (subpath and not subpath.startswith('#')):
                    raise ValueError('文件卡subpath须为真实#标题/#^块/#page=页')
                # A file card stores an explicit vault-root path, not a short
                # Wiki title. Keep that exact destination even if another folder
                # contains a same-named file, then validate its candidate anchor.
                destination = '/' + quote(relative.as_posix(), safe='/.-_~') + subpath
                validate_link(vault, origin, destination, pages_by_path, overrides, wiki=False)
            elif node.get('type') == 'text':
                for link in re.findall(r'\[\[([^\]]+)\]\]', visible_markdown(node.get('text', ''))):
                    if not wiki_parts(link)[0].split('#', 1)[0]:
                        raise ValueError('文字卡的Wiki须给真实页面，不能用无所属页面的自链接')
                    target = wiki_target(vault, origin, link, overrides)
                    if target in removed:
                        raise ValueError('候选删除了仍被文字卡引用的目标: ' + link)
                    validate_link(vault, origin, link, pages_by_path, overrides, wiki=True)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            problems.append(f'{node.get("id")}: {exc}')
    return problems


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['render', 'verify', 'audit'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--also-vault', action='append', default=[], type=Path)
    args = parser.parse_args(argv)
    if args.action == 'audit':
        if args.output is None:
            parser.error('audit 需要 --output 指向 Canvas')
        data = json.loads(args.output.read_text(encoding='utf-8'))
        results = {str(vault.resolve()): audit_canvas(vault, data)
                   for vault in [args.root] + args.also_vault}
        print(json.dumps({'canvas': str(args.output), 'problems_by_vault': results}, ensure_ascii=False, indent=2))
        sys.exit(1 if any(results.values()) else 0)
    if args.spec is None:
        parser.error('render/verify 需要 --spec')
    spec = json.loads(args.spec.read_text(encoding='utf-8'))
    data = compile_canvas(args.root, spec, args.also_vault)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if args.action == 'render':
        if args.output is None:
            parser.error('render 需要 --output')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding='utf-8')
    # Obsidian rewrites JSON whitespace when a Canvas is opened; compare the graph.
    elif args.output is None or json.loads(args.output.read_text(encoding='utf-8')) != data:
        raise ValueError('Canvas 与当前来源图不一致，需要重新生成')
    print(json.dumps({'cards': len(data['nodes']), 'edges': len(data['edges']),
                      'vaults_checked': [str(args.root.resolve())] + [str(p.resolve()) for p in args.also_vault],
                      'sha256': hashlib.sha256(serialized.encode()).hexdigest()}, ensure_ascii=False))
