#!/usr/bin/env python3
"""盘点用户指定目录；默认不导出内容预览或绝对源路径。"""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone


TEXT_EXTENSIONS = {'.csv', '.tsv', '.txt', '.md', '.text', '.json', '.xml',
                   '.html', '.yaml', '.yml'}
SKIP_DIRS = {'.git', '.hg', '.svn', '__pycache__', 'node_modules', '.venv', 'venv'}


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {'.xlsx', '.xlsm', '.xls', '.csv', '.tsv'}:
        return 'spreadsheet'
    if ext in TEXT_EXTENSIONS:
        return 'text'
    return {'.docx': 'word', '.doc': 'word', '.pdf': 'pdf',
            '.pptx': 'presentation', '.ppt': 'presentation',
            '.png': 'image', '.jpg': 'image', '.jpeg': 'image',
            '.webp': 'image', '.gif': 'image', '.tif': 'image',
            '.tiff': 'image'}.get(ext, 'other')


def is_link(path: Path) -> bool:
    # Windows 目录联接与其他重解析点也不跟随。
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def text_preview(path: Path, limit: int) -> dict:
    with path.open('rb') as handle:
        raw = handle.read(min(limit * 4 + 4, 262144))
    encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    for encoding in encodings:
        try:
            decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
            value = decoder.decode(raw, final=False)
            return {'preview': value[:limit], 'preview_encoding': encoding}
        except UnicodeError:
            continue
    return {'preview_error': '无法可靠识别文本编码，未生成预览'}


def make_manifest(root: Path, preview_chars: int = 0, max_files: int | None = None,
                  exclude_paths=()) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError('源目录不存在或不是目录')
    if preview_chars < 0 or (max_files is not None and max_files < 1):
        raise ValueError('预览长度不得为负，文件上限必须为正整数')
    excluded = [Path(p).resolve() for p in exclude_paths]
    entries, errors = [], []
    truncated = False

    def excluded_path(path):
        resolved = path.resolve()
        return any(resolved == item or item in resolved.parents for item in excluded)

    def walk_error(exc):
        # 不将异常中的绝对路径或文件内容写入报告。
        errors.append({'error': type(exc).__name__, 'reason': '部分目录无法访问'})

    for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(current)
        kept = []
        for name in sorted(dirs):
            path = base / name
            try:
                if name not in SKIP_DIRS and not is_link(path) and not excluded_path(path):
                    kept.append(name)
            except OSError as exc:
                walk_error(exc)
        dirs[:] = kept
        for name in sorted(files):
            path = base / name
            if name == '.env' or name.startswith('.env.'):
                continue
            try:
                if is_link(path) or excluded_path(path) or not path.is_file():
                    continue
                if max_files is not None and len(entries) >= max_files:
                    truncated = True
                    break
                stat = path.stat()
                with path.open('rb') as handle:
                    digest = hashlib.file_digest(handle, 'sha256').hexdigest()
                entry = {'relative_path': path.relative_to(root).as_posix(),
                         'kind': classify(path), 'size_bytes': stat.st_size,
                         'sha256': digest}
                if preview_chars and path.suffix.lower() in TEXT_EXTENSIONS:
                    entry.update(text_preview(path, preview_chars))
                entries.append(entry)
            except OSError as exc:
                errors.append({'relative_path': path.relative_to(root).as_posix(),
                               'error': type(exc).__name__})
        if truncated:
            break
    return {'created_utc': datetime.now(timezone.utc).isoformat(),
            'file_count': len(entries), 'truncated': truncated,
            'errors': errors, 'files': entries}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_folder', help='用户明确指定的材料目录')
    parser.add_argument('--output', default='source_manifest.json', help='JSON 输出路径')
    parser.add_argument('--csv', help='可选 CSV 索引路径')
    parser.add_argument('--preview-chars', type=int, default=0, help='显式启用文本预览；默认 0')
    parser.add_argument('--max-files', type=int, help='最大文件数，截断会返回非零退出码')
    parser.add_argument('--exclude', action='append', default=[], help='排除路径，可重复指定')
    args = parser.parse_args()
    output = Path(args.output)
    exclusions = [*args.exclude, output]
    if args.csv:
        exclusions.append(Path(args.csv))
        if Path(args.csv).resolve() == output.resolve():
            parser.error('JSON 与 CSV 输出路径不能相同')
    try:
        result = make_manifest(Path(args.source_folder), args.preview_chars,
                               args.max_files, exclusions)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if args.csv:
            csv_path = Path(args.csv)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
                fields = ['relative_path', 'kind', 'size_bytes', 'sha256']
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
                writer.writeheader()
                for entry in result['files']:
                    row = dict(entry)
                    if row['relative_path'].startswith(('=', '+', '-', '@', '\t', '\r')):
                        row['relative_path'] = "'" + row['relative_path']
                    writer.writerow(row)
    except (ValueError, OSError) as exc:
        parser.error(f'材料清单生成失败：{type(exc).__name__}；请检查目录、参数和写入权限')
    print(f"已记录 {result['file_count']} 个文件；截断={result['truncated']}；读取错误={len(result['errors'])}")
    return 1 if result['truncated'] or result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
