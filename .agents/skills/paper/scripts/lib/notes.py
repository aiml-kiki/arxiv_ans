"""Markdown and native Obsidian structure; no scientific judgments are inferred."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import quote, unquote, urlsplit
import yaml
from .core import atomic_text, digest, inside, locked, read_json, write_json

FOLDERS = ('论文', '知识', '专题')
BLOCK_ANCHOR = re.compile(r'(?<!\S)\^([A-Za-z0-9-]+)[ \t]*$')
LINK_DEST = r'(<[^>\n]*>|[^\s)]+)'
MD_INLINE = re.compile(r'!?\[[^\]\n]*\]\(\s*')
MD_REFERENCE = re.compile(r'^ {0,3}\[[^\]\n]+\]:\s*(' + LINK_DEST + r')', re.M)

def read_note(p):
    t = p.read_text(encoding='utf-8')
    m = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', t, re.S)
    meta = yaml.safe_load(m[1]) if m else {}
    if not isinstance(meta, dict):
        raise ValueError(f'属性必须是映射: {p.name}')
    return meta, t[m.end():] if m else t

def validate_note_frontmatter(text, label):
    """Reject malformed generated note properties before staging a review."""
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.S)
    if not match:
        raise ValueError(f'缺少有效 YAML 属性区: {label}')
    try:
        meta = yaml.safe_load(match[1])
    except yaml.YAMLError as exc:
        raise ValueError(f'YAML 属性格式错误: {label}: {exc}') from exc
    if not isinstance(meta, dict):
        raise ValueError(f'YAML 属性必须是顶层映射: {label}')
    missing = {'id', 'tags', 'analysis', 'updated'} - set(meta)
    if missing:
        raise ValueError(f'缺少必要属性 {sorted(missing)}: {label}')
    if not isinstance(meta['tags'], list):
        raise ValueError(f'tags 必须是列表: {label}')
    return meta

def note_paths(root):
    return sorted(p for d in FOLDERS for p in (root / d).rglob('*.md')
                  if p.resolve().is_relative_to(root) and not p.is_symlink())

def manifests(root):
    return sorted((root / '.research/sources').rglob('*.json'))

def visible_markdown(body):
    """Mask examples and comments while retaining offsets for link edits."""
    lines = body.splitlines(keepends=True)
    result = []
    fence = None
    for line in lines:
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence:
            result.append(re.sub(r'[^\n]', ' ', line))
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
        elif marker:
            fence = marker[1]
            result.append(re.sub(r'[^\n]', ' ', line))
        elif line.startswith(('    ', '\t')):
            result.append(re.sub(r'[^\n]', ' ', line))
        else:
            result.append(line)
    text = ''.join(result)
    for pattern in (r'<!--.*?-->', r'(`+)(?:(?!\1)[^\n])*?\1'):
        text = re.sub(pattern, lambda m: re.sub(r'[^\n]', ' ', m[0]), text, flags=re.S)
    return text

def table_render_issues(body):
    """Catch Wiki aliases that Markdown tables can split into extra cells."""
    issues = []
    for index, line in enumerate(visible_markdown(body).splitlines(), 1):
        if not re.match(r'^\s*\|', line):
            continue
        for match in re.finditer(r'\[\[([^\]\n]+)\]\]', line):
            for pos, char in enumerate(match[1]):
                prefix = match[1][:pos]
                escapes = len(prefix) - len(prefix.rstrip('\\'))
                if char == '|' and escapes % 2 == 0:
                    issues.append(f'第 {index} 行：表格中的Wiki竖线未转义，可能被拆列；请使用\\|或把链接移到表外')
                    break
    return issues

def block_anchors(body):
    """Return renderable Obsidian block IDs and misplaced-ID diagnostics."""
    visible = visible_markdown(body)
    lines = visible.splitlines()
    # Link fragments are references, not malformed declarations of block IDs.
    # Mask them only for diagnostics; actual anchors still use original text so
    # an ID preceding a trailing link cannot become a valid paragraph anchor.
    diagnostic = list(visible)
    spans = [(m.start(), m.end()) for m in re.finditer(r'\[\[[^\]]+\]\]', visible)]
    try:
        spans.extend((start, end) for start, end, _ in markdown_destinations(body))
    except ValueError:
        pass  # The link checker reports malformed Markdown independently.
    for start, end in spans:
        for pos in range(start, end):
            if diagnostic[pos] != '\n':
                diagnostic[pos] = ' '
    diagnostic_lines = ''.join(diagnostic).splitlines()
    anchors, issues = [], []
    for index, line in enumerate(lines):
        match = BLOCK_ANCHOR.search(line)
        if not match:
            candidate = re.search(r'\^(?:claim-|c-|r-|q-|d-|question-|relation-|delta-)[^\s]*\s*$', diagnostic_lines[index])
            if candidate:
                issues.append(f'第 {index + 1} 行：块标识格式无效；使用ASCII字母/数字/连字符，段尾标识前须有空格')
            continue
        prefix = line[:match.start()].strip()
        if prefix:
            if re.match(r'^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\|)', prefix):
                issues.append(f'第 {index + 1} 行：块标识不能直接附在结构块内部')
            else:
                anchors.append(match[1])
            continue
        # Obsidian allows a separate ID for a structured block only when it is
        # surrounded by blank lines. A detached ID after prose renders as text.
        before = index - 1
        while before >= 0 and not lines[before].strip():
            before -= 1
        structured = before >= 0 and bool(re.match(r'^\s*(?:[-*+]\s|\d+[.)]\s|>|\|)', lines[before]))
        separated = (index > 0 and not lines[index - 1].strip()
                     and index + 1 < len(lines) and not lines[index + 1].strip())
        if structured and separated:
            anchors.append(match[1])
        else:
            issues.append(f'第 {index + 1} 行：孤立块标识会显示为正文；普通段落请放在段尾')
    return anchors, issues

def markdown_destinations(body):
    visible = visible_markdown(body)
    def has_closing_link(end):
        tail = visible[end:].split('\n', 1)[0]
        return bool(re.match(r'''\s*(?:(?:"[^"\n]*"|'[^'\n]*')\s*)?\)''', tail))
    for match in MD_INLINE.finditer(visible):
        start = match.end()
        if start >= len(visible): raise ValueError('Markdown链接格式无效')
        if visible[start] == '<':
            end = visible.find('>', start + 1)
            if end < 0 or not has_closing_link(end + 1):
                raise ValueError('Markdown链接格式无效')
            end += 1
        else:
            depth = 0
            end = start
            while end < len(visible):
                char = visible[end]
                if char == '\\' and end + 1 < len(visible):
                    end += 2
                    continue
                if char == '(':
                    depth += 1
                elif char == ')':
                    if depth == 0: break
                    depth -= 1
                elif char.isspace() and depth == 0:
                    break
                end += 1
            if depth or end == start or not has_closing_link(end):
                raise ValueError('Markdown链接格式无效')
        yield start, end, body[start:end]
    for match in MD_REFERENCE.finditer(visible):
        yield match.start(1), match.end(1), body[match.start(1):match.end(1)]

def local_destination(root, page, destination):
    """Resolve a Markdown URL from its authoring page; external URLs return None."""
    angle = destination.startswith('<') and destination.endswith('>')
    url = destination[1:-1] if angle else destination
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return None
    name = unquote(parts.path)
    if not name:
        return page, unquote(parts.fragment)
    target = inside(root, name.lstrip('/') if name.startswith('/') else page.parent.relative_to(root) / name)
    return target, unquote(parts.fragment)

def wiki_parts(destination):
    """Split a Wiki target and retain the exact plain or table-escaped alias.

    Only the escape immediately before the alias separator is syntax. Do not
    unescape paths or fragments generally; vault path safety rules still apply.
    """
    target, divider, alias = destination.partition('|')
    if divider and target.endswith('\\'):
        target, divider = target[:-1], '\\|'
    return target, divider, alias


def wiki_target(root, page, destination, overrides=None):
    """Resolve an explicit attachment or a Markdown title using one shared rule.

    An existing HTML/text/image attachment keeps its actual extension. A dotted
    note title such as ``v1.0`` may still resolve to ``v1.0.md``; proposed changes
    count as existence so outgoing and incoming checks use the same overlay.
    """
    root = Path(root).resolve()
    name = wiki_parts(destination)[0].split('#', 1)[0]
    target = inside(root, name) if name else page
    if not name:
        return target
    overrides = overrides or {}
    if '/' not in name and '\\' not in name:
        # Obsidian permits a short filename only if its target is unambiguous.
        # Include visible attachments and proposed files, excluding hidden work
        # caches and proposed deletions. Do not guess by nearest-folder order.
        names = {name} if Path(name).suffix.lower() == '.md' else {name, name + '.md'}
        candidates = set()
        for directory, folders, files in os.walk(root, followlinks=False):
            folders[:] = [folder for folder in folders if not folder.startswith('.')]
            for filename in files:
                if filename not in names or filename.startswith('.'):
                    continue
                path = (Path(directory) / filename).resolve()
                if path.is_relative_to(root) and path.is_file():
                    candidates.add(path)
        candidates.update(path for path, content in overrides.items()
                          if content is not None and path.name in names)
        candidates.difference_update(path for path, content in overrides.items() if content is None)
        if len(candidates) > 1:
            raise ValueError('Wiki短文件名有歧义，请使用库根路径: ' + name + ' -> ' +
                             ', '.join(str(path.relative_to(root)) for path in sorted(candidates)))
        if candidates:
            return next(iter(candidates))
        # Keep a unique removed destination recognizable to incoming-link
        # checks instead of accidentally looking for a new root-level note.
        deleted = [path for path, content in overrides.items() if content is None and path.name in names]
        if len(deleted) == 1:
            return deleted[0]
    markdown = Path(str(target) + '.md')
    if not target.suffix or (not target.is_file() and target not in overrides
                             and (markdown.is_file() or markdown in overrides)):
        return markdown
    return target


def validate_link(root, page, destination, pages_by_path, overrides=None, wiki=False, allow_legacy_orphan=False):
    overrides = overrides or {}
    if wiki:
        _, _, anchor = wiki_parts(destination)[0].partition('#')
        target = wiki_target(root, page, destination, overrides)
    else:
        resolved = local_destination(root, page, destination)
        if resolved is None:
            return
        target, anchor = resolved
    target_text = overrides.get(target)
    if target_text is None and not target.is_file():
        raise ValueError('链接目标不存在: '+destination)
    if target.is_relative_to(root / '.research/drafts') or target.is_relative_to(root / '审阅'):
        raise ValueError('正式页引用待审草稿')
    if target.suffix.lower() == '.md' and anchor:
        body = target_text if target_text is not None else target.read_text(encoding='utf-8')
        visible = visible_markdown(body)
        if anchor.startswith('^'):
            valid = anchor[1:] in block_anchors(body)[0]
            if not valid and allow_legacy_orphan and target_text is None:
                # Existing notes await review. Keep search available while the
                # checker reports their misplaced IDs as warnings.
                valid = bool(re.search(r'^' + re.escape(anchor) + r'[ \t]*$', visible, re.M))
        else:
            valid = anchor in re.findall(r'^#{1,6}\s+(.+?)\s*$', visible, re.M)
        if not valid: raise ValueError('页内定位不存在: '+destination)
    elif target.suffix.lower() == '.pdf' and anchor:
        page_match = re.fullmatch(r'page=(\d+)', anchor)
        if not page_match or not 1 <= int(page_match[1]) <= pages_by_path.get(target, 0):
            raise ValueError('PDF页码无效: '+destination)
    elif target.suffix.lower() == '.base' and anchor:
        views = yaml.safe_load(target.read_text()).get('views', [])
        if anchor not in [v.get('name') for v in views]:
            raise ValueError('Base视图不存在: '+destination)

def normalize_draft(root, draft, target, draft_targets=None):
    """Prepare one draft for review at its final path; never edit the source draft."""
    root = Path(root).resolve()
    draft = inside(root, draft)
    target = inside(root, target)
    if not draft.is_relative_to(root / '.research/drafts') or draft.suffix != '.md':
        raise ValueError('草稿必须是.research/drafts中的Markdown文件')
    if target != root / '首页.md' and not any(target.is_relative_to(root / d) for d in FOLDERS):
        raise ValueError('目标不在知识目录')
    if target.suffix != '.md': raise ValueError('目标必须是Markdown文件')
    targets = {inside(root, source): inside(root, final) for source, final in (draft_targets or {}).items()}
    targets[draft] = target
    body = draft.read_text(encoding='utf-8')
    return rebase_markdown(root, body, draft, target, targets)

def rebase_markdown(root, body, source, target, linked_targets=None):
    """Keep local link destinations pointing to the same files after a page moves."""
    root = Path(root).resolve()
    source, target = inside(root, source), inside(root, target)
    linked_targets = linked_targets or {}
    edits = []
    for start, end, destination in markdown_destinations(body):
        resolved = local_destination(root, source, destination)
        if resolved is None: continue
        linked, fragment = resolved
        if linked.is_relative_to(root / '.research/drafts') and linked not in linked_targets and linked != source:
            raise ValueError('草稿链接没有对应正式目标: ' + destination)
        linked = linked_targets.get(linked, target if linked == source else linked)
        angle = destination.startswith('<')
        original_path = urlsplit(destination[1:-1] if angle else destination).path
        relative = '' if not original_path else os.path.relpath(linked, target.parent).replace(os.sep, '/')
        replacement = quote(relative, safe='/.-_~') + ('#' + quote(fragment, safe='-._~^/=') if fragment else '')
        if angle: replacement = '<' + replacement + '>'
        edits.append((start, end, replacement))
    for start, end, replacement in sorted(edits, reverse=True):
        body = body[:start] + replacement + body[end:]
    return body

def fingerprint(root):
    return digest('\n'.join(str(p.relative_to(root))+':'+digest(p.read_bytes()) for p in note_paths(root)+manifests(root)))

def _search(root, query, limit=8):
    root=Path(root).resolve()
    report=check(root)
    if report['errors']:raise ValueError(f'请先修复 {report["errors"]} 个完整性错误；原索引未替换')
    db=root/'.research/search.sqlite'; stamp=fingerprint(root); fresh=False
    if db.exists():
        try:
            with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
                fresh=c.execute('SELECT value FROM settings WHERE key="fingerprint"').fetchone()[0]==stamp
        except (sqlite3.Error,TypeError):pass
    if not fresh:
        db.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(dir=db.parent,suffix='.sqlite');os.close(fd)
        try:
            with sqlite3.connect(temp) as c:
                c.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT); CREATE VIRTUAL TABLE docs USING fts5(path UNINDEXED,title,body,kind UNINDEXED);')
                for p in note_paths(root):
                    m,b=read_note(p); title=p.stem+' '+str(m.get('aliases',[]))
                    c.execute('INSERT INTO docs VALUES (?,?,?,?)',(str(p.relative_to(root)),title,b,'note'))
                seen=set()
                for p in manifests(root):
                    m=json.loads(p.read_text())
                    for b in m['blocks']:
                        path=m['source_path'] + ('#page=' + str(b['page']) if 'page' in b
                                                else '#section=' + str(b.get('section', b['id'])))
                        if path in seen:continue
                        seen.add(path)
                        c.execute('INSERT INTO docs VALUES (?,?,?,?)',(path,m['paper_id'],b['text'],'source'))
                if fingerprint(root)!=stamp:raise ValueError('索引期间文档发生变化，请重试')
                c.execute('INSERT INTO settings VALUES (?,?)',('fingerprint',stamp))
            os.replace(temp,db)
        finally:Path(temp).unlink(missing_ok=True)
    words=re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9_]+',query)
    terms=[]
    for word in words:
        # FTS5 does not segment Chinese phrases into useful concept words.
        # Non-overlapping pairs let a four-character query match a page
        # containing its two components separately, with no domain dictionary.
        terms.extend(word[i:i+2] for i in range(0,len(word),2)) if re.fullmatch(r'[\u4e00-\u9fff]{3,}',word) else terms.append(word)
    terms=list(dict.fromkeys(t.lower() for t in terms if t))
    if not terms:return {'results':[],'rebuilt':not fresh}
    ranked=[]
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        # This personal vault is small; scanning the disposable SQLite cache
        # gives predictable Chinese substring matching and title priority.
        for row in c.execute('SELECT path,title,kind,body FROM docs'):
            title,body=row['title'].lower(),row['body'].lower()
            title_hits=[t in title for t in terms]
            body_hits=[t in body for t in terms]
            hits=sum(a or b for a,b in zip(title_hits,body_hits))
            if not hits:continue
            score=6*sum(title_hits)+sum(body_hits)+5*(hits==len(terms))
            if query.strip().lower() in title:score+=20
            offsets=[body.find(t) for t in terms if t in body]
            start=max(0,min(offsets,default=0)-40)
            ranked.append((score,row['kind']=='note',row['path'],dict(path=row['path'],title=row['title'],kind=row['kind'],excerpt=row['body'][start:start+300])))
    ranked.sort(key=lambda item:(-item[0],-item[1],item[2]))
    return {'rebuilt':not fresh,'stale':False,'results':[item[3] for item in ranked[:limit]],
            'retrieval':'标题与正文的中文子串重排；SQLite 可重建缓存，非向量检索'}


def source_target(root, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('论文必须给固定 source')
    if value.startswith('[[') and value.endswith(']]'):
        value = wiki_parts(value[2:-2])[0].split('#', 1)[0]
    return inside(root, value)


def property_wiki_links(meta):
    """Yield visible navigation links in property values, without semantics.

    Existing flat properties and nested legacy data stay readable; no new
    property is required and none of these links imply support or dependency.
    """
    seen = set()
    def walk(value, label):
        if isinstance(value, str):
            for match in re.finditer(r'\[\[([^\]]+)\]\]', visible_markdown(value)):
                yield label, match[1]
        elif isinstance(value, list):
            if id(value) in seen:
                return
            seen.add(id(value))
            for index, item in enumerate(value):
                yield from walk(item, f'{label}[{index}]')
        elif isinstance(value, dict):
            if id(value) in seen:
                return
            seen.add(id(value))
            for key, item in value.items():
                yield from walk(item, f'{label}.{key}' if label else str(key))
    yield from walk(meta, '')


def note_kind(path, meta):
    inferred = {'论文': 'paper', '知识': 'knowledge', '专题': 'topic'}
    return meta.get('kind') or next((inferred[p] for p in path.parts if p in inferred), None)


def source_records(root):
    records, issues = {}, []
    for path in manifests(root):
        try:
            data = read_json(path)
            raw = inside(root, data['source_path'])
            if not raw.is_relative_to(root / '附件') or not raw.is_file():
                raise ValueError('固定来源不存在或不在附件')
            if digest(raw.read_bytes()) != data['source_sha256']:
                raise ValueError('固定原文哈希改变')
            blocks = data['blocks']
            ids = [block['id'] for block in blocks]
            if len(ids) != len(set(ids)):
                raise ValueError('解析块ID重复')
            if any(digest(b['text']) != b['text_sha256'] for b in blocks):
                raise ValueError('解析块哈希改变')
            if digest('\f'.join(b['text'] for b in blocks)) != data['text_sha256']:
                raise ValueError('解析全文哈希改变')
            if data.get('source_kind', 'pdf') == 'pdf':
                if data['page_count'] != len(blocks) or any(
                        b.get('page') != i for i, b in enumerate(blocks, 1)):
                    raise ValueError('解析页数或页序错误')
            records[path] = data
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append({'path': str(path.relative_to(root)), 'message': str(exc)})
    return records, issues


def check(root, changes=None):
    """Check proposed outgoing AND existing incoming links, without imposing a heading quota."""
    root = Path(root).resolve()
    changes = changes or {}
    overrides = {inside(root, path): content for path, content in changes.items()}
    removed = {path for path, content in overrides.items() if content is None}
    records, errors = source_records(root)
    warnings, identities, docs = [], {}, []
    pdf_pages = {inside(root, data['source_path']): data.get('page_count', 0)
                 for data in records.values()}
    paths = set(note_paths(root)) | set(overrides)
    if (root / '首页.md').is_file():
        paths.add(root / '首页.md')
    for path in sorted(paths - removed):
        rel = str(path.relative_to(root))
        try:
            content = overrides[path] if path in overrides else path.read_text(encoding='utf-8')
            if not isinstance(content, str):
                raise ValueError('正文必须为字符串')
            if path.name == '首页.md':
                match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', content, re.S)
                meta = yaml.safe_load(match[1]) if match else {}
                if not isinstance(meta, dict):
                    raise ValueError('首页属性必须为映射')
                body = content[match.end():] if match else content
            else:
                meta = validate_note_frontmatter(content, rel)
                match = re.match(r'\A---\r?\n.*?\r?\n---(?:\r?\n|$)', content, re.S)
                body = content[match.end():]
                ident = meta.get('id')
                if not isinstance(ident, str) or not ident or ident in identities:
                    raise ValueError('缺少稳定id或正式id重复')
                identities[ident] = path
                kind = note_kind(path, meta)
                if kind not in {'paper', 'knowledge', 'comparison', 'topic'}:
                    raise ValueError('kind无效')
                expected = ('paper' if path.is_relative_to(root / '论文') else
                            'knowledge' if path.is_relative_to(root / '知识') else None)
                if expected and kind != expected:
                    raise ValueError('kind与正式目录不一致')
                if meta.get('review_status') or meta.get('analysis') in {'draft', 'pending', '待审', '草稿'}:
                    raise ValueError('正式页不能包含待审状态')
                if kind == 'paper':
                    src = source_target(root, meta.get('source'))
                    matched = [data for data in records.values()
                               if data['paper_id'] == ident and inside(root, data['source_path']) == src
                               and str(data['version']) == str(meta.get('version'))]
                    if not matched:
                        raise ValueError('论文id/source/version没有匹配的固定来源')
            docs.append((path, body, meta))
            anchors, anchor_issues = block_anchors(body)
            if len(anchors) != len(set(anchors)):
                raise ValueError('块ID重复')
            errors.extend({'path': rel, 'message': issue}
                          for issue in anchor_issues + table_render_issues(body))
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
            errors.append({'path': rel, 'message': str(exc)})
    for path, body, meta in docs:
        rel = str(path.relative_to(root))
        visible = visible_markdown(body)
        links = [(m[1], True, '正文') for m in re.finditer(r'\[\[([^\]]+)\]\]', visible)]
        links.extend((destination, True, '属性 ' + label)
                     for label, destination in property_wiki_links(meta))
        try:
            links.extend((destination, False, '正文') for _, _, destination in markdown_destinations(body))
        except ValueError as exc:
            errors.append({'path': rel, 'message': str(exc)})
        for destination, wiki, location in links:
            target = None
            try:
                if wiki:
                    target = wiki_target(root, path, destination, overrides)
                else:
                    resolved = local_destination(root, path, destination)
                    target = resolved[0] if resolved else None
                if target in removed:
                    raise ValueError('更新删除了仍被引用的目标: ' + destination)
                validate_link(root, path, destination, pdf_pages, overrides, wiki=wiki)
            except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
                errors.append({'path': rel, 'message': location + '：' + str(exc),
                               **({'target': str(target.relative_to(root))} if target is not None else {})})
    for path in sorted((root / '专题').glob('*.base')):
        try:
            data = yaml.safe_load(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('views'), list):
                raise ValueError('Base必须包含views列表')
            if any(not view.get('name') or not view.get('type') for view in data['views']):
                raise ValueError('Base视图缺少名称或类型')
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            errors.append({'path': str(path.relative_to(root)), 'message': str(exc)})
    from .canvas import audit_canvas
    for path in sorted((root / '专题').glob('*.canvas')):
        try:
            data = read_json(path)
            objects = data['nodes'] + data['edges']
            if len({item['id'] for item in objects}) != len(objects):
                raise ValueError('Canvas ID重复')
            node_ids = {node['id'] for node in data['nodes']}
            if any(edge['fromNode'] not in node_ids or edge['toNode'] not in node_ids
                   for edge in data['edges']):
                raise ValueError('Canvas关系端点缺失')
            if any(any(type(node.get(key)) not in {int, float}
                       for key in ('x', 'y', 'width', 'height'))
                   or node['width'] <= 0 or node['height'] <= 0 for node in data['nodes']):
                raise ValueError('Canvas坐标或尺寸无效')
            for issue in audit_canvas(root, data, changes=changes, pages_by_path=pdf_pages):
                errors.append({'path': str(path.relative_to(root)), 'message': issue})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({'path': str(path.relative_to(root)), 'message': str(exc)})
    # Local import keeps notes usable by the semantic compiler without a module
    # cycle. Compile the complete proposed formal set read-only: ordinary old
    # prose is unregistered, not an error; explicit broken contracts are errors.
    try:
        from .semantics import compile_index
        semantic_changes = {str(path.relative_to(root)): content
                            for path, content in overrides.items()
                            if any(path.is_relative_to(root / folder) for folder in FOLDERS)}
        semantic = compile_index(root, semantic_changes)
        errors.extend({**issue, 'message': '显式语义：' + issue['message']}
                      for issue in semantic['errors'])
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        errors.append({'path': '.research/index/semantic.json',
                       'message': '显式语义校验未完成：' + str(exc)})
    return {'notes': len(docs), 'errors': len(errors), 'warnings': len(warnings),
            'issues': errors + warnings,
            'limitation': '机械校验不证明科学支持、实际阅读或Obsidian界面效果。'}



def check_changes(root, changes, baseline=None, *, actual=False):
    """Block new/affected errors; report unchanged unrelated debt separately."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError('改动范围校验需要非空正式路径→正文/null映射')
    root = Path(root).resolve()
    baseline = check(root) if baseline is None else baseline
    proposed = check(root) if actual else check(root, changes)
    key = lambda issue: json.dumps(issue, ensure_ascii=False, sort_keys=True)
    known = {key(issue) for issue in baseline['issues']}
    affected = {str(inside(root, path).relative_to(root)) for path in changes}
    source_paths = set()
    for content in changes.values():
        if content is None:
            continue
        try:
            match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', content, re.S)
            meta = yaml.safe_load(match[1]) if match else {}
            if isinstance(meta, dict) and meta.get('source'):
                source_paths.add(source_target(root, meta['source']))
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            pass  # Full candidate validation supplies the blocking diagnostic.
    for path in manifests(root):
        try:
            data = read_json(path)
            if inside(root, data['source_path']) in source_paths:
                affected.add(str(path.relative_to(root)))
        except (OSError, ValueError, KeyError, TypeError):
            affected.add(str(path.relative_to(root)))  # Unknown source corruption is not waived.
    blockers, historical = [], []
    for issue in proposed['issues']:
        touches = issue.get('path') in affected or issue.get('target') in affected
        global_contract = issue.get('message', '').startswith('显式语义') or '正式id重复' in issue.get('message', '')
        (blockers if touches or global_contract or key(issue) not in known else historical).append(issue)
    return {**proposed, 'errors': len(blockers), 'issues': blockers,
            'validation_scope': 'changed_pages_and_affected_incoming_links',
            'full_errors': proposed['errors'], 'historical_issues': historical,
            'historical_errors': len(historical),
            'limitation': proposed['limitation'] + ' 未变化且与本次改动无关的历史错误仍单列；这不等于全库通过。'}


def search(root, query, limit=8):
    if not isinstance(query, str) or not query.strip() or not 1 <= limit <= 50:
        raise ValueError('给非空查询，limit须为1..50')
    with locked(root, 'search-index'):
        return _search(Path(root).resolve(), query, limit)
