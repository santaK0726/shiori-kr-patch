"""Apply the source-checked Korean localization to a new output folder.

Python 3.10+ and the xdelta3 executable supplied in the release ZIP are required.
The source dump, installed game, emulator settings and saves are never written.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_join(root, relative):
    p = PurePosixPath(relative)
    if not relative or p.is_absolute() or '\\' in relative or ':' in relative or '..' in p.parts:
        raise ValueError('잘못된 패키지 경로: ' + str(relative))
    result = root.joinpath(*p.parts).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('폴더 밖을 가리키는 경로: ' + str(relative))
    return result


def load_package():
    profile = json.loads((HERE / 'package.json').read_text('utf-8'))
    manifest_path = HERE / 'manifest.json'
    if sha(manifest_path) != profile['manifest_sha256']:
        raise ValueError('패키지 매니페스트가 손상되었습니다. 원래 ZIP을 다시 압축 해제하세요.')
    manifest = json.loads(manifest_path.read_text('utf-8'))
    if manifest['schema'] != 1 or manifest['title_id'] != '01007BF026AFC000':
        raise ValueError('지원하지 않는 패키지입니다.')
    if manifest['file_count'] != len(manifest['files']):
        raise ValueError('파일 목록이 손상되었습니다.')
    seen = set()
    for row in manifest['files']:
        if row['path'] in seen or row['kind'] not in ('add', 'vcdiff'):
            raise ValueError('중복되거나 알 수 없는 파일 항목입니다.')
        seen.add(row['path'])
        safe_join(HERE, row['payload'])
        safe_join(HERE, row['path'])
    return profile, manifest


def check_inputs(source, manifest):
    if not (source / 'data').is_dir():
        raise ValueError('data 폴더가 들어 있는 원본 RomFS 폴더를 선택하세요.')
    tool = safe_join(HERE, manifest['tool']['path'])
    if not tool.is_file() or sha(tool) != manifest['tool']['sha256']:
        raise ValueError('xdelta3 도구가 없거나 손상되었습니다. 전체 릴리스 ZIP을 사용하세요.')
    for row in manifest['files']:
        payload = safe_join(HERE, row['payload'])
        if not payload.is_file() or payload.stat().st_size != row['payload_size'] or sha(payload) != row['payload_sha256']:
            raise ValueError('차분 파일이 없거나 손상되었습니다: ' + row['payload'])
        original = safe_join(source, row['path'])
        if row['source_sha256'] is None:
            if original.exists():
                raise ValueError('이미 수정된 원본일 수 있습니다: ' + row['path'])
        elif not original.is_file() or original.stat().st_size != row['source_size'] or sha(original) != row['source_sha256']:
            raise ValueError('일본판 1.0.1 원본과 다릅니다. 기존 패치가 없는 본편+업데이트 병합 RomFS를 사용하세요: ' + row['path'])
    return tool


def apply(source, output, platform):
    profile, manifest = load_package()
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise ValueError('출력 폴더가 이미 있습니다. 기존 파일을 덮어쓰지 않도록 새 폴더를 지정하세요.')
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('원본과 출력은 서로 분리된 폴더여야 합니다.')
    print('원본과 패키지를 검사합니다.', flush=True)
    tool = check_inputs(source, manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.with_name(output.name + '.building-' + uuid.uuid4().hex)
    stage.mkdir()
    if platform == 'emulator':
        relative_root = Path(manifest['title_id']) / manifest['mod_name'] / 'romfs'
    elif platform == 'switch':
        relative_root = Path('atmosphere/contents') / manifest['title_id'] / 'romfs'
    else:
        raise ValueError('알 수 없는 출력 종류입니다.')
    romfs = stage / relative_root
    records = {}
    try:
        for i, row in enumerate(manifest['files'], 1):
            target = safe_join(romfs, row['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = safe_join(HERE, row['payload'])
            if row['kind'] == 'add':
                shutil.copyfile(payload, target)
            else:
                original = safe_join(source, row['path'])
                result = subprocess.run([str(tool), '-d', '-D', '-R', '-s', str(original), str(payload), str(target)],
                                        capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode:
                    raise ValueError('차분 적용 실패: ' + row['path'] + '\n' + result.stderr.decode('utf-8', errors='replace'))
            if target.stat().st_size != row['target_size'] or sha(target) != row['target_sha256']:
                raise ValueError('생성 파일 검증 실패: ' + row['path'])
            records[row['path']] = {'bytes': row['target_size'], 'sha256': row['target_sha256']}
            if i % 25 == 0 or i == len(manifest['files']):
                print(f'{i}/{len(manifest["files"])} 파일 검증 완료', flush=True)
        actual = {p.relative_to(romfs).as_posix() for p in romfs.rglob('*') if p.is_file()}
        if actual != set(records):
            raise ValueError('최종 파일 구성이 매니페스트와 다릅니다.')
        receipt = {'build': manifest['build'], 'platform': platform, 'romfs': relative_root.as_posix(),
                   'files': records, 'source_modified': False, 'runtime': 'See docs/QA.md for scope-bound results'}
        (stage / 'install-result.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', 'utf-8')
        (stage / 'INSTALL.txt').write_text((HERE / ('docs/EDEN.md' if platform == 'emulator' else 'docs/SWITCH.md')).read_text('utf-8'), 'utf-8')
        if output.exists():
            raise ValueError('작업 도중 같은 출력 폴더가 생성됐습니다. 덮어쓰지 않습니다.')
        stage.rename(output)
    except Exception:
        print('완성 출력은 만들지 않았습니다. 조사용 임시 폴더: ' + str(stage), file=sys.stderr)
        raise
    print('완료: ' + str(output), flush=True)
    return output


def interactive():
    import tkinter as tk
    from tkinter import filedialog, messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        profile, _ = load_package()
        platform = profile['platform']
        original = filedialog.askdirectory(title='일본판 1.0.1 원본 RomFS 선택 — data 폴더가 들어 있는 폴더')
        if not original:
            return
        parent = filedialog.askdirectory(title='완성 패치를 저장할 상위 폴더 선택')
        if not parent:
            return
        output = Path(parent) / ('Shiori_KR_009_Emulator' if platform == 'emulator' else 'Shiori_KR_009_Switch')
        result = apply(Path(original), output, platform)
        messagebox.showinfo('완료', '패치를 만들고 전체 파일을 검증했습니다.\n' + str(result) + '\n\n폴더 안의 INSTALL.txt를 확인하세요.')
    except Exception as exc:
        messagebox.showerror('패치 생성 실패', str(exc))
        raise
    finally:
        root.destroy()


def main():
    ap = argparse.ArgumentParser(description='쿠가야마 시오리의 죽음 수첩 JP 1.0.1 한국어 패치 생성')
    ap.add_argument('--romfs', type=Path)
    ap.add_argument('--output', type=Path)
    ap.add_argument('--platform', choices=['emulator', 'switch'])
    args = ap.parse_args()
    if not any((args.romfs, args.output, args.platform)):
        interactive()
        return
    if not args.romfs or not args.output:
        ap.error('--romfs와 --output을 함께 지정하세요.')
    platform = args.platform or load_package()[0]['platform']
    apply(args.romfs, args.output, platform)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
