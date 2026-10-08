"""Fixed source versions and bounded extraction; parsing is not reading."""
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor
import io
import json
import multiprocessing
import os
from pathlib import Path
import re
import argparse
from html.parser import HTMLParser
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from .core import atomic_text, digest, inside, locked, read_json, write_json, safe_id, utc_now

def prepare_source(root, ident, filename, version):
    """Read/validate fixed bytes before parsing; reuse only verified parser-matched cache."""
    root=Path(root).resolve()
    import pypdf
    if not re.fullmatch(r'paper-[a-z0-9-]+', ident):
        raise ValueError('id 必须是 paper- 开头的小写稳定标识')
    if not re.fullmatch(r'[A-Za-z0-9._-]+', version):
        raise ValueError('version 使用版本号或 imported，不确定的修订号不能猜测')
    src = Path(filename).resolve()
    raw = src.read_bytes()
    target = src if src.is_relative_to(root / '附件') else root / '附件' / ident / f'{version}.pdf'
    target = inside(root, str(target))
    if target.exists() and target.read_bytes() != raw:
        raise ValueError('同名版本的原文不同；请使用新的 version，旧原文未改动')
    parser = {'name': 'pypdf', 'version': pypdf.__version__}
    source_hash = digest(raw)
    cached = None
    for path in sorted((root / '.research/sources' / ident / source_hash).glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        if (data.get('paper_id') == ident and data.get('version') == version
                and data.get('source_path') == str(target.relative_to(root)) and data.get('parser') == parser):
            texts = [b['text'] for b in data['blocks']]
            if (data.get('source_sha256') != source_hash or data.get('page_count') != len(texts)
                    or any(b['page'] != i or b['text_sha256'] != digest(b['text'])
                           for i, b in enumerate(data['blocks'], 1))
                    or data['text_sha256'] != digest('\f'.join(texts))):
                raise ValueError('不可变来源缓存哈希/页序不匹配')
            cached = (path, data)
            break
    return {'ident': ident, 'version': version, 'raw': raw, 'target': target,
            'source_hash': source_hash, 'parser': parser, 'cached': cached}

def extract_pdf(raw):
    """Pure file-level worker. Fixed bytes in, page text out; no shared writes."""
    import pypdf
    try:
        reader = pypdf.PdfReader(io.BytesIO(raw))
        return [p.extract_text() or '' for p in reader.pages]
    except Exception as ex:
        raise ValueError(f'PDF解析失败: {ex}') from ex

def source_result(root, dest, data):
    return {'manifest': str(dest.relative_to(root)), 'source': data['source_path'],
            'pages': data['page_count'], 'extractable': any(b['text'].strip() for b in data['blocks']),
            'limitation': '图表/公式需看原图；扫描件无文本时需另行OCR，不据此宣称精读完成。'}


def manifest_key(data):
    """Bind parser output to both fixed bytes and its work/version identity.

    Identical bytes may legitimately describe different versions or attachment
    paths. New keys avoid collisions; old matching manifests remain reusable.
    """
    identity = {key: data[key] for key in ('paper_id', 'version', 'source_path',
                                          'source_sha256', 'parser', 'text_sha256')}
    return digest(json.dumps(identity, ensure_ascii=False, sort_keys=True))

def publish_source(root, prepared, pages=None):
    """Coordinator-only publication. Existing originals/manifests stay immutable."""
    root = Path(root).resolve()
    target, raw = prepared['target'], prepared['raw']
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write(raw)
    elif target.read_bytes() != raw:
        raise ValueError('发布前发现原文冲突；旧原文未改动')
    if prepared['cached']:
        return source_result(root, *prepared['cached'])
    blocks = [dict(id=f'page-{i:04d}', page=i, kind='text', text=t, text_sha256=digest(t))
              for i, t in enumerate(pages, 1)]
    data = dict(paper_id=prepared['ident'], version=prepared['version'], source_path=str(target.relative_to(root)),
                source_sha256=prepared['source_hash'], parser=prepared['parser'],
                page_count=len(pages), blocks=blocks, text_sha256=digest('\f'.join(pages)))
    key = manifest_key(data)
    dest = root / '.research/sources' / prepared['ident'] / prepared['source_hash'] / f'{key}.json'
    payload = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if dest.exists() and dest.read_text() != payload:
        raise ValueError('不可变来源记录冲突')
    if not dest.exists():
        atomic_text(dest, payload)
    return source_result(root, dest, data)

def _source(root, ident, filename, version):
    """Import bytes once, preserving revisions; never fabricate an arXiv version."""
    root = Path(root).resolve()
    if Path(filename).suffix.lower() in {'.html', '.htm', '.txt', '.md'}:
        return import_text(root, ident, filename, version)
    prepared = prepare_source(root, ident, filename, version)
    return publish_source(root, prepared, None if prepared['cached'] else extract_pdf(prepared['raw']))

def _source_batch(root, specs, workers=2, memory_mb=512):
    """Preflight a batch, parse uncached PDFs in processes, publish sequentially."""
    root = Path(root).resolve()
    if not isinstance(specs, list) or not specs or not 1 <= workers <= 8:
        raise ValueError('spec 须是非空列表；workers 须为1..8')
    if any(not isinstance(s, dict) or any(not isinstance(s.get(k), str) or not s[k].strip()
                                       for k in ('id', 'file', 'version')) for s in specs):
        raise ValueError('每个来源须给非空字符串 id、file、version')
    if memory_mb < 64 or any(Path(s['file']).suffix.lower() != '.pdf' for s in specs):
        raise ValueError('batch仅处理PDF；memory_mb至少64，其他固定文本使用import')
    sizes = [Path(root / s['file']).stat().st_size for s in specs]
    if sum(sizes) > memory_mb * 1024 * 1024:
        raise ValueError('该批原文字节超过内存预算；按文件分批导入')
    identities = [(s['id'], s['version']) for s in specs]
    if len(set(identities)) != len(identities):
        raise ValueError('批内身份/版本重复；先去重')
    prepared = [prepare_source(root, s['id'], root / s['file'], s['version']) for s in specs]
    destinations = [p['target'] for p in prepared]
    if len(set(destinations)) != len(destinations):
        raise ValueError('批内来源目标路径重复')
    needs = [i for i, p in enumerate(prepared) if not p['cached']]
    estimate = max((sizes[i] * 4 for i in needs), default=1)
    available = max(1, memory_mb * 1024 * 1024 - sum(sizes))
    count = min(workers, len(needs), os.cpu_count() or 1, max(1, available // estimate))
    pages, errors = {}, {}
    if count > 1:
        with ProcessPoolExecutor(max_workers=count, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = {i: pool.submit(extract_pdf, prepared[i]['raw']) for i in needs}
            for i, future in futures.items():
                try: pages[i] = future.result()
                except Exception as ex: errors[i] = str(ex)
    else:
        for i in needs:
            try: pages[i] = extract_pdf(prepared[i]['raw'])
            except Exception as ex: errors[i] = str(ex)
    results = []
    for i, p in enumerate(prepared):
        row = {'id': p['ident'], 'version': p['version'], 'cache_hit': bool(p['cached'])}
        if i not in errors:
            try: row['result'] = publish_source(root, p, pages.get(i))
            except (ValueError, OSError) as ex: errors[i] = str(ex)
        if i in errors: row['error'] = errors[i]
        results.append(row)
    return {'results': results, 'workers': count, 'cache_hits': len(prepared) - len(needs),
            'errors': len(errors), 'memory_mb': memory_mb,
            'limitation': '文件级进程并行；内存估计为调度启发式，非硬进程限额。图表仍须原图核验。'}

def source_pages(root, manifest, pages, max_chars=12000, offset=0):
    """Bounded, revision-specific extraction. Extraction is not reading coverage."""
    root=Path(root).resolve()
    path=inside(root,manifest)
    if not path.is_relative_to(root/'.research/sources') or path.suffix!='.json':
        raise ValueError('manifest必须是.research/sources中的来源记录')
    if not 1<=max_chars<=50000 or offset<0:raise ValueError('max_chars须为1..50000，offset须非负')
    requested=sorted(set(int(x) for x in pages.split(',')))
    m=json.loads(path.read_text());raw=inside(root,m['source_path'])
    if digest(raw.read_bytes())!=m['source_sha256']:raise ValueError('原文哈希变化')
    if m.get('source_kind', 'pdf') != 'pdf':
        raise ValueError('非PDF固定文本使用sections，不能把解析段当PDF页')
    count=m.get('page_count',max((b['page'] for b in m['blocks']),default=0))
    if not requested or any(p<1 or p>count for p in requested):raise ValueError('请求页码超出原文范围')
    blocks=[b for b in m['blocks'] if b['page'] in requested]
    if any(digest(b['text'])!=b['text_sha256'] for b in blocks):raise ValueError('解析块哈希不匹配')
    total=sum(len(b['text']) for b in blocks)
    if offset>total:raise ValueError('offset超出所选页面文本长度')
    result=[];cursor=0;end=min(total,offset+max_chars)
    for b in blocks:
        size=len(b['text']);lo=max(0,offset-cursor);hi=min(size,end-cursor)
        if hi>lo:
            result.append(dict(page=b['page'],block_id=b['id'],start=lo,end=hi,text=b['text'][lo:hi],
                               path=m['source_path']+'#page='+str(b['page'])))
        cursor+=size
    return dict(paper_id=m['paper_id'],source_sha256=m['source_sha256'],manifest=manifest,
                requested_pages=requested,results=result,total_chars=total,returned_chars=end-offset,
                truncated=end<total,next_offset=end if end<total else None,
                empty_or_unextracted_pages=[p for p in requested if not any(b['page']==p and b['text'].strip() for b in blocks)],
                limitation='文本片段；未自动核验图表、公式或阅读覆盖。')


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        elif tag in {'p', 'div', 'section', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {'p', 'div', 'section', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def import_text(root, ident, filename, version):
    safe_id(ident)
    if not ident.startswith('paper-') or not re.fullmatch(r'[A-Za-z0-9._-]+', version):
        raise ValueError('固定来源需要paper-身份与明确版本')
    root, src = Path(root).resolve(), Path(filename).resolve()
    raw = src.read_bytes()
    suffix = src.suffix.lower()
    target = src if src.is_relative_to(root / '附件') else root / '附件' / ident / (version + suffix)
    target = inside(root, target)
    if target.exists() and target.read_bytes() != raw:
        raise ValueError('同名版本原文不同，旧原文未覆盖')
    decoded = raw.decode('utf-8')
    if suffix in {'.html', '.htm'}:
        parser = _HTMLText()
        parser.feed(decoded)
        decoded = ''.join(parser.parts)
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n', decoded) if part.strip()]
    if not paragraphs:
        paragraphs = [decoded.strip()]
    blocks = [dict(id=f'section-{i:04d}', section=i, kind='text', text=part,
                   text_sha256=digest(part)) for i, part in enumerate(paragraphs, 1)]
    data = dict(paper_id=ident, version=version, source_kind='html' if suffix in {'.html', '.htm'} else 'text',
                source_path=str(target.relative_to(root)), source_sha256=digest(raw),
                parser={'name': 'stdlib-text', 'version': '1'}, page_count=0,
                section_count=len(blocks), blocks=blocks, text_sha256=digest('\f'.join(paragraphs)))
    directory = root / '.research/sources' / ident / digest(raw)
    dest = directory / (manifest_key(data) + '.json')
    # Reuse a byte-identical legacy text manifest rather than rewriting its
    # immutable identity. Other versions sharing the raw hash get their own key.
    for candidate in sorted(directory.glob('*.json')):
        if read_json(candidate) == data:
            dest = candidate
            break
    if dest.exists() and read_json(dest) != data:
        raise ValueError('固定来源解析记录冲突')
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
    if not dest.exists():
        write_json(dest, data)
    return {'manifest': str(dest.relative_to(root)), 'source': data['source_path'],
            'source_sha256': data['source_sha256'], 'source_kind': data['source_kind'],
            'sections': len(blocks), 'limitation': '固定文本的解析段不是PDF页；公式、图像及实际阅读需另核。'}


def source(root, ident, filename, version):
    with locked(root, 'sources'):
        return _source(root, ident, filename, version)


def source_batch(root, specs, workers=2, memory_mb=512):
    with locked(root, 'sources'):
        return _source_batch(root, specs, workers, memory_mb)


def fetch_source(root, ident, url, version, max_mb=128):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('来源URL须为公开HTTPS，不含凭据')
    request = Request(url, headers={'User-Agent': 'paper-research/1.0'})
    with urlopen(request, timeout=45) as response:
        resolved = response.geturl()
        if urlsplit(resolved).scheme != 'https':
            raise ValueError('重定向后的来源须为HTTPS')
        raw = response.read(max_mb * 1024 * 1024 + 1)
        if len(raw) > max_mb * 1024 * 1024:
            raise ValueError('来源超出单次下载预算')
        kind = response.headers.get_content_type()
    suffix = '.pdf' if raw.startswith(b'%PDF-') else '.html' if kind == 'text/html' else '.txt'
    with tempfile.TemporaryDirectory(prefix='paper-fetch-') as directory:
        path = Path(directory) / ('source' + suffix)
        path.write_bytes(raw)
        result = source(root, ident, path, version)
    provenance = dict(requested_url=url, resolved_url=resolved, acquired_at=utc_now(),
                      version=version, source_path=result['source'], source_sha256=digest(raw))
    location = inside(root, f'.research/provenance/{safe_id(ident)}/{digest(raw)}/{digest(url)}.json')
    if not location.exists():
        write_json(location, provenance)
    result['provenance'] = str(location.relative_to(Path(root).resolve()))
    return result


def sections(root, manifest, requested, max_chars=12000):
    root = Path(root).resolve()
    path = inside(root, manifest)
    if not path.is_relative_to(root / '.research/sources'):
        raise ValueError('manifest必须是来源记录')
    data = read_json(path)
    if data.get('source_kind', 'pdf') == 'pdf':
        raise ValueError('PDF使用pages，不能把段落编号当页码')
    if digest(inside(root, data['source_path']).read_bytes()) != data['source_sha256']:
        raise ValueError('原文哈希改变')
    positions = {int(value) for value in requested.split(',')}
    if not positions or min(positions) < 1 or max(positions) > data['section_count']:
        raise ValueError('段落编号超范围')
    if not 1 <= max_chars <= 50000:
        raise ValueError('max_chars须为1..50000')
    selected = [block for block in data['blocks'] if block['section'] in positions]
    if any(digest(block['text']) != block['text_sha256'] for block in selected):
        raise ValueError('解析段哈希改变')
    if sum(len(block['text']) for block in selected) > max_chars:
        raise ValueError('所选段超出当前窗口，请按段拆分；未返回截断段冒充完整段')
    return {'manifest': manifest, 'source_sha256': data['source_sha256'], 'results': selected,
            'limitation': '解析段定位，不等于阅读或图像核验。'}


def record_coverage(root, run, unit, record):
    safe_id(run)
    safe_id(unit)
    if not isinstance(record, dict) or not record.get('manifest') or not record.get('observations'):
        raise ValueError('实际覆盖需要manifest和非空observations列表')
    root = Path(root).resolve()
    manifest = inside(root, record['manifest'])
    if not manifest.is_relative_to(root / '.research/sources'):
        raise ValueError('coverage manifest须在来源目录')
    data = read_json(manifest)
    if digest(inside(root, data['source_path']).read_bytes()) != data['source_sha256']:
        raise ValueError('coverage原文哈希改变')
    observations = record['observations']
    if not isinstance(observations, list) or any(not isinstance(row, dict)
            or row.get('status') not in {'已读', '图像已核', '未覆盖', '不可读'}
            or not row.get('location') or not row.get('basis') for row in observations):
        raise ValueError('每项覆盖给location/status/basis；解析存在不是已读依据')
    path = inside(root, f'.research/coverage/{run}/{unit}.json')
    with locked(root, 'coverage-' + run):
        history = read_json(path) if path.exists() else {'run': run, 'unit': unit, 'events': []}
        history['events'].append(dict(record, source_sha256=data['source_sha256'], recorded_at=utc_now()))
        write_json(path, history)
    return {'coverage': str(path.relative_to(root)), 'events': len(history['events']),
            'limitation': '记录执行者提供的实际范围，脚本不证明其阅读语义。'}


def render_page(root, manifest, page, dpi=120):
    root = Path(root).resolve()
    path = inside(root, manifest)
    if not path.is_relative_to(root / '.research/sources'):
        raise ValueError('manifest须在来源目录')
    data = read_json(path)
    raw = inside(root, data['source_path'])
    if data.get('source_kind', 'pdf') != 'pdf' or not 1 <= page <= data['page_count']:
        raise ValueError('仅支持固定PDF内的实际页')
    if digest(raw.read_bytes()) != data['source_sha256'] or not 72 <= dpi <= 300:
        raise ValueError('原文改变或dpi超出72..300')
    program = shutil.which('pdftoppm')
    if program is None:
        raise ValueError('未找到pdftoppm；保留来源并使用宿主PDF查看工具，不声称图已核验')
    output = inside(root, f'.research/cache/figures/{data["source_sha256"]}/{page}-{dpi}')
    image = Path(str(output) + '.png')
    with locked(root, 'render-' + data['source_sha256'] + '-' + str(page)):
        if not image.exists():
            output.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([program, '-f', str(page), '-l', str(page), '-r', str(dpi),
                            '-png', '-singlefile', str(raw), str(output)],
                           check=True, capture_output=True, timeout=60)
    return {'image': str(image), 'page': page, 'limitation': '已渲染；阅读者须实际查看才记图像核验。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    actions = parser.add_subparsers(dest='action', required=True)
    for name in ('import', 'fetch'):
        action = actions.add_parser(name)
        action.add_argument('--id', required=True)
        action.add_argument('--version', required=True)
        action.add_argument('--file' if name == 'import' else '--url', required=True)
    action = actions.add_parser('batch')
    action.add_argument('--spec', required=True, type=Path)
    action.add_argument('--workers', default=2, type=int)
    action.add_argument('--memory-mb', default=512, type=int)
    for name in ('pages', 'sections', 'render'):
        action = actions.add_parser(name)
        action.add_argument('--manifest', required=True)
        action.add_argument('--pages' if name == 'pages' else '--sections' if name == 'sections' else '--page',
                            required=True, type=int if name == 'render' else str)
        if name != 'render':
            action.add_argument('--max-chars', default=12000, type=int)
        if name == 'pages':
            action.add_argument('--offset', default=0, type=int)
    action = actions.add_parser('coverage')
    action.add_argument('--run', required=True)
    action.add_argument('--unit', required=True)
    action.add_argument('--record', required=True, type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.action == 'import':
        supplied = Path(args.file)
        # Relative inputs belong to --root; an explicit absolute source may be
        # outside the vault because importing only reads it before fixing bytes.
        filename = supplied if supplied.is_absolute() else inside(root, supplied)
        return source(root, args.id, filename, args.version)
    if args.action == 'fetch':
        return fetch_source(root, args.id, args.url, args.version)
    if args.action == 'batch':
        return source_batch(root, read_json(inside(root, args.spec)), args.workers, args.memory_mb)
    if args.action == 'pages':
        return source_pages(root, args.manifest, args.pages, args.max_chars, args.offset)
    if args.action == 'sections':
        return sections(root, args.manifest, args.sections, args.max_chars)
    if args.action == 'render':
        return render_page(root, args.manifest, args.page)
    return record_coverage(root, args.run, args.unit, read_json(inside(root, args.record)))
