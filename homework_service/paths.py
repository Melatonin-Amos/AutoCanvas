import re
from pathlib import Path


def slug(value):
    return re.sub(r'[^\w.\-]+', '-', str(value)).strip('.-')[:80] or 'homework'


def workspace_path(root: Path, relative: str, *, file=False):
    root = root.resolve()
    candidate = Path(relative)
    if candidate.is_absolute() or '..' in candidate.parts or '\\' in relative or not relative.strip():
        raise ValueError('请使用工作区内的相对路径')
    path = (root/candidate).resolve()
    if path == root or not path.is_relative_to(root) or '.homework-manager' in path.relative_to(root).parts:
        raise ValueError('路径必须位于作业工作区内')
    if file and not path.is_file():
        raise FileNotFoundError('文件不存在')
    return path


def overlap(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)
