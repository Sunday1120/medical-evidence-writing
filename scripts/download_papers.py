#!/usr/bin/env python3
"""按来源编号下载已核实授权的 PDF；不执行论文检索或医学证据判断。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener


DEFAULT_MAX_BYTES = 25 * 1024 * 1024
SOURCE_TYPES = {'paper', 'guideline', 'regulatory', 'local-material', 'dataset', 'web'}


def validate_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('下载链接必须是字符串')
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('下载链接仅支持完整 HTTP/HTTPS 地址')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('下载链接不得包含用户名或密码')
    return value


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


urlopen = build_opener(SafeRedirect()).open


def validate_references(data) -> list[dict]:
    refs = data.get('references') if isinstance(data, dict) else data
    if not isinstance(refs, list) or not refs:
        raise ValueError('需要非空来源列表或含 references 列表的对象')
    ids, numbers = set(), set()
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError('每条来源必须是对象')
        for key in ['id', 'title']:
            if not isinstance(ref.get(key), str) or not ref[key].strip():
                raise ValueError(f'来源缺少有效的 {key}')
        if ref['id'] in ids:
            raise ValueError('来源 ID 重复')
        ids.add(ref['id'])
        if not isinstance(ref.get('type'), str) or ref['type'] not in SOURCE_TYPES:
            raise ValueError('来源 type 不受支持')
        claims = ref.get('used_for')
        if not isinstance(claims, list) or not claims or not all(isinstance(c, str) and c.strip() for c in claims):
            raise ValueError('used_for 必须是非空表述 ID 列表')
        if 'citation_number' in ref:
            number = ref['citation_number']
            if type(number) is not int or number < 1 or number in numbers:
                raise ValueError('正文编号必须为唯一的正整数')
            numbers.add(number)
        if 'pdf_url' in ref:
            validate_url(ref['pdf_url'])
        if 'access' in ref and (not isinstance(ref['access'], str) or
                                ref['access'] not in {'open-access', 'user-authorized'}):
            raise ValueError('access 必须为 open-access 或 user-authorized')
    if numbers and numbers != set(range(1, len(numbers) + 1)):
        raise ValueError('正文编号必须从 1 开始连续；未引用来源应省略编号')
    return sorted(refs, key=lambda ref: ref.get('citation_number', float('inf')))


def safe_filename(ref: dict, index: int) -> str:
    slug = re.sub(r'[^A-Za-z0-9._-]+', '_', ref['id']).strip('._-')[:80] or 'source'
    prefix = f"{ref['citation_number']:03d}" if 'citation_number' in ref else f'source-{index:03d}'
    return f'{prefix}-{slug}.pdf'


def fetch_pdf(url: str, destination: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[bool, str]:
    validate_url(url)
    if max_bytes < 5:
        raise ValueError('文件大小上限过小')
    if destination.exists() or destination.is_symlink():
        return False, '目标文件已存在，未覆盖且未核验'
    temporary = None
    try:
        req = Request(url, headers={'User-Agent': 'MedicalEvidenceWriting/0.1',
                                    'Accept': 'application/pdf'})
        with urlopen(req, timeout=30) as response:
            length = response.headers.get('Content-Length')
            if length and int(length) > max_bytes:
                return False, '响应超过文件大小上限'
            first = response.read(5)
            if first != b'%PDF-':
                return False, '缺少 PDF 文件头，拒绝将登录页或错误页保存为论文'
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.part', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(first)
                size = len(first)
                while True:
                    chunk = response.read(min(65536, max_bytes - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        return False, '响应超过文件大小上限'
                    handle.write(chunk)
        # 同目录硬链接使成品只在下载结束后出现，且已有文件绝不会被替换。
        os.link(temporary, destination)
        return True, f'已保存 {size} 字节；仅完成格式初检，内容身份仍需核对'
    except HTTPError as exc:
        return False, f'HTTP {exc.code}'
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f'下载未完成：{type(exc).__name__}'
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_all(refs: list[dict], output_dir: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    refs = validate_references(refs)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, ref in enumerate(refs, 1):
        result = {'id': ref['id'], 'citation_number': ref.get('citation_number')}
        url = ref.get('pdf_url')
        if not url:
            result['status'] = 'no_pdf_url' if ref['type'] in {'paper', 'guideline'} else 'not_requested'
        elif ref.get('access') not in {'open-access', 'user-authorized'}:
            result['status'] = 'access_unconfirmed'
        else:
            filename = safe_filename(ref, index)
            target = output_dir / filename
            if target.exists() or target.is_symlink():
                result['status'] = 'exists_unverified'
            else:
                ok, message = fetch_pdf(url, target, max_bytes)
                result.update(status='downloaded' if ok else 'failed', message=message)
                if ok:
                    result['file'] = filename
        results.append(result)
    return {'reference_count': len(refs),
            'downloaded_count': sum(r['status'] == 'downloaded' for r in results),
            'results': results}


def report_exit_code(report: dict) -> int:
    return int(any(r['status'] not in {'downloaded', 'not_requested'} for r in report['results']))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('references_json', help='结构化来源 JSON')
    parser.add_argument('--output-dir', default='papers', help='PDF 与报告目录')
    parser.add_argument('--max-mb', type=int, default=25, help='每份 PDF 大小上限，默认 25 MiB')
    args = parser.parse_args()
    if args.max_mb <= 0:
        parser.error('--max-mb 必须为正整数')
    try:
        source = Path(args.references_json)
        output = Path(args.output_dir)
        report_path = output / 'download_report.json'
        if source.resolve() == report_path.resolve():
            parser.error('报告路径不得覆盖来源 JSON')
        refs = validate_references(json.loads(source.read_text(encoding='utf-8-sig')))
        report = download_all(refs, output, args.max_mb * 1024 * 1024)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    except (ValueError, OSError) as exc:
        parser.error(f'输入或输出错误：{type(exc).__name__}；请检查来源结构、编号和路径')
    print(f"已下载 {report['downloaded_count']} 份；其余状态见 download_report.json")
    return report_exit_code(report)


if __name__ == '__main__':
    raise SystemExit(main())
