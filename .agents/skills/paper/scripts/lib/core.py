"""Shared paths, hashes and single-writer storage for the research CLI."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', value):
        raise ValueError('标识须为小写字母、数字及连字符，长度1..128')
    return value


def inside(root, value):
    root = Path(root).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f'路径超出知识库: {value}')
    return path


def digest(value):
    raw = value.encode('utf-8') if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.writing-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


@contextmanager
def locked(root, key):
    """Advisory lock released by the OS on exit; never infer a stale lock by age."""
    directory = inside(root, '.research/locks')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest(str(key))[:24] + '.lock')
    with path.open('a+', encoding='utf-8') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def default_root():
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if all((candidate / name).is_dir() for name in ('论文', '知识', '专题')):
            return candidate.resolve()
    return Path(__file__).resolve().parents[5]
