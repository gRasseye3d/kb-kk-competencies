"""Verify component payload and (optionally) compatible host; never modify files."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def verify(root, entries):
    errors = []
    for relative, expected in entries.items():
        path = PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
            errors.append(f'Unsafe manifest path: {relative}')
            continue
        if not re.fullmatch(r'[0-9a-f]{64}', expected):
            errors.append(f'Invalid SHA256: {relative}')
            continue
        source = root / relative
        if (not source.is_file() or source.is_symlink()
                or not source.resolve().is_relative_to(root.resolve())):
            errors.append(f'Missing/unsafe file: {relative}')
        elif hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            errors.append(f'SHA256 mismatch: {relative}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, help='Full kb-kk root containing kb/ and docs/')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'component-manifest.json').read_text())
    if manifest['format_version'] != 1:
        raise SystemExit('Unsupported manifest version')
    errors = verify(root, manifest['component_files'])
    if args.host:
        errors.extend(verify(args.host, manifest['compatible_host_shared_files']))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f"Component verified: {len(manifest['component_files'])} files")
    if args.host:
        print(f"Compatible host verified: {len(manifest['compatible_host_shared_files'])} shared files")
    else:
        print('Host not checked; this is not a standalone application test.')


if __name__ == '__main__':
    main()
