"""One entry for fixed sources, staged research, derived indexes and reviewed notes.

Run from the vault with uv run python .agents/skills/paper/scripts/research.py.
The CLI never starts an LLM or interprets a source's scientific meaning.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import yaml

from lib.core import default_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=default_root())
    parser.add_argument('command', choices=('source', 'search', 'query', 'workflow', 'derive',
                                           'check', 'review', 'publish', 'finish', 'canvas'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        parser.error('root不存在')
    remaining = args.arguments
    try:
        if args.command == 'workflow':
            from lib import workflow
            return workflow.main(['--root', str(root), *remaining])
        if args.command in {'derive', 'query'}:
            from lib import semantics, queries
            module = semantics if args.command == 'derive' else queries
            delegated = [remaining[0], '--root', str(root), *remaining[1:]] if remaining and not remaining[0].startswith('-') else remaining
            return module.main(delegated)
        if args.command == 'canvas':
            from lib import canvas
            canvas.main([*remaining, '--root', str(root)])
            return 0
        if args.command == 'source':
            from lib import sources
            value = sources.main(['--root', str(root), *remaining])
        elif args.command == 'review':
            from lib import publishing
            value = publishing.main(['--root', str(root), *remaining])
        elif args.command == 'search':
            from lib.notes import search
            command = argparse.ArgumentParser(prog='research.py search')
            command.add_argument('query')
            command.add_argument('--limit', type=int, default=8)
            options = command.parse_args(remaining)
            value = search(root, options.query, options.limit)
        elif args.command == 'check':
            from lib.notes import check, check_changes
            from lib.core import read_json
            command = argparse.ArgumentParser(prog='research.py check')
            command.add_argument('--changes', type=Path)
            options = command.parse_args(remaining)
            value = check_changes(root, read_json(root / options.changes)) if options.changes else check(root)
        elif args.command == 'publish':
            from lib.publishing import publish
            command = argparse.ArgumentParser(prog='research.py publish')
            command.add_argument('--proposal', required=True)
            command.add_argument('--digest', required=True)
            command.add_argument('--authorization', required=True)
            options = command.parse_args(remaining)
            value = publish(root, options.proposal, options.digest, options.authorization)
        else:
            from lib.publishing import finish
            command = argparse.ArgumentParser(prog='research.py finish')
            command.add_argument('--run', required=True)
            command.add_argument('--discard', action='store_true')
            command.add_argument('--abandon-reason', help='用户明确废弃中断运行的授权及原因；不伪造科学接受')
            options = command.parse_args(remaining)
            value = finish(root, options.run, options.discard, options.abandon_reason)
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        return 1 if isinstance(value.get('errors'), int) and value['errors'] else 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, yaml.YAMLError,
            subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
