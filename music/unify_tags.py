#!/usr/bin/env python3
"""Normalize library MP3s to a single ID3v2.4 tag.

  - Remove ID3v1 by truncating the final 128 bytes without rewriting audio.
  - Upgrade ID3v2.3 to ID3v2.4, rewriting those files' text frames as UTF-8.

Preserve APEv2, APIC, and frame encodings in existing v2.4 files. Latin-1 and
UTF-8 have identical bytes in the ASCII range, so that difference alone does
not justify rewriting 2,300 tracks.

Verify each run by hashing every file's audio span (MPEG frames without tags),
APE values, and cover images before and after processing. Any difference or
unreadable file causes verification to fail.

Dumps and full backups go to --backup-dir, or the working directory when
omitted. Their output paths must be outside the library, including symlinks.

Run directly by path; this script imports nothing from the local package.

  python3 <path>/music/unify_tags.py <root>                  # Dry run
  python3 <path>/music/unify_tags.py <root> --apply          # Apply and verify
  python3 <path>/music/unify_tags.py <root> --restore <dump> # Restore ID3v1
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import struct
import sys
import unicodedata
from datetime import datetime

from mutagen.id3 import ID3, ID3NoHeaderError, delete as id3_delete
from mutagen.apev2 import APEv2, APENoHeaderError


# ─────────────────────────── File discovery ───────────────────────────

def walk_mp3(root, errors=None):
    """Try an NFC path when the filename returned by readdir cannot be resolved.

    macOS SMB can return decomposed filenames. Normalize them for lookup
    compatibility when the original path does not resolve.

    Collect inaccessible directories in `errors` when supplied. os.walk skips
    them silently by default, which could otherwise allow an incomplete scan
    to be reported as fully verified.
    """
    def on_error(e):
        if errors is not None:
            errors.append('순회 실패: %s (%s)' % (getattr(e, 'filename', '?'), e))

    for dirpath, _dirnames, filenames in os.walk(root, onerror=on_error):
        for name in sorted(filenames):
            if not name.lower().endswith('.mp3'):
                continue
            p = os.path.join(dirpath, name)
            if not os.path.exists(p):
                p_nfc = unicodedata.normalize('NFC', p)
                if os.path.exists(p_nfc):
                    p = p_nfc
            yield p


# ─────────────────────────── Tag boundaries ───────────────────────────

def has_id3v2(path):
    """Check the first three bytes directly for an ID3v2 block.

    Without ID3v2, `ID3()` may read ID3v1 and return `version = (1, 1)` instead
    of raising an exception. A successful load alone does not prove v2 exists.
    """
    try:
        with open(path, 'rb') as f:
            return f.read(3) == b'ID3'
    except OSError:
        return False


def read_v1(path):
    """Return the final 128 bytes unchanged if they contain an ID3v1 tag."""
    try:
        with open(path, 'rb') as f:
            f.seek(-128, 2)
            tail = f.read(128)
    except OSError:
        return None
    return tail if tail[:3] == b'TAG' else None


def synchsafe(b):
    """Decode the ID3v2 size field using seven payload bits per valid input byte.

    Masking and reading as an ordinary 32-bit integer spaces the groups eight
    bits apart, inflating the tag size and shifting the audio start. A previous
    check hashed one file after incorrectly skipping 5,948,288 bytes.
    """
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def audio_span(path):
    """Return the audio span (start, end), excluding tags.

    Exclude leading ID3v2 and trailing ID3v1/APEv2. Compare the bytes in this
    span before and after processing to verify that the audio is unchanged.
    """
    size = os.path.getsize(path)
    start, end = 0, size
    with open(path, 'rb') as f:
        head = f.read(10)
        if len(head) == 10 and head[:3] == b'ID3':
            flags = head[5]
            start = 10 + synchsafe(head[6:10]) + (10 if flags & 0x10 else 0)
        if size >= 128:
            f.seek(-128, 2)
            if f.read(3) == b'TAG':
                end -= 128
        if end >= 32:
            f.seek(end - 32)
            if f.read(8) == b'APETAGEX':
                f.seek(end - 32 + 12)
                ape_size = struct.unpack('<I', f.read(4))[0]   # footer + items
                f.seek(end - 32 + 20)
                ape_flags = struct.unpack('<I', f.read(4))[0]
                end -= ape_size
                if ape_flags & 0x80000000:                     # Include the optional header.
                    end -= 32
    if start > size or end > size or start >= end:
        raise ValueError('태그 경계가 파일 범위를 벗어난다: start=%d end=%d size=%d'
                         % (start, end, size))
    return start, end


def audio_sha(path):
    start, end = audio_span(path)
    h = hashlib.sha256()
    remain = end - start
    with open(path, 'rb') as f:
        f.seek(start)
        while remain > 0:
            chunk = f.read(min(1 << 20, remain))
            if not chunk:
                raise ValueError('오디오 구간이 끝나기 전에 파일이 끝났다')
            remain -= len(chunk)
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────── Snapshots ───────────────────────────

def ape_digest(path):
    """Hash APE keys and values together, returning (error, digest).

    Checking keys alone misses changed values. Keep errors separate from the
    digest: identical error strings before and after must not count as proof
    that APE contents were preserved.
    """
    try:
        ape = APEv2(path)
    except APENoHeaderError:
        return None, 'none'
    except Exception as e:
        return 'APE 읽기 실패: %s' % type(e).__name__, None
    h = hashlib.sha256()
    for k in sorted(ape.keys()):
        h.update(k.encode('utf-8', 'replace'))
        h.update(b'\x00')
        h.update(bytes(ape[k]))
        h.update(b'\x00')
    return None, h.hexdigest()


def cover_digest(tag):
    """Hash APIC image bytes too. Comparing sizes alone would miss replacement
    with a different image of the same size."""
    h = hashlib.sha256()
    for k in sorted(k for k in tag.keys() if k.startswith('APIC')):
        fr = tag[k]
        h.update(k.encode('utf-8', 'replace'))
        h.update(b'\x00')
        h.update(fr.data)
        h.update(b'\x00')
    return h.hexdigest()


def snapshot(root, with_audio=True, skip=()):
    """Return (records, traversal errors) for before/after comparison.

    Records with err values and traversal errors fail verification.
    """
    out = {}
    walk_errors = []
    skip = tuple(os.path.abspath(s) + os.sep for s in skip)
    for p in walk_mp3(root, errors=walk_errors):
        if any(os.path.abspath(p).startswith(s) for s in skip):
            continue
        rec = {'v1': None, 'v2': None, 'ver': None, 'enc': set(),
               'ape': None, 'cover': None, 'err': None, 'audio': None}
        rec['v1'] = read_v1(p) is not None
        rec['v2'] = has_id3v2(p)
        try:
            tag = ID3(p, translate=False)
            rec['ver'] = tag.version[1]
            rec['enc'] = {int(tag[k].encoding) for k in tag if hasattr(tag[k], 'encoding')}
            rec['cover'] = cover_digest(tag)
        except ID3NoHeaderError:
            rec['ver'] = 0                      # No ID3v2 tag.
            rec['cover'] = 'none'
        except Exception as e:
            rec['err'] = type(e).__name__
        ape_err, rec['ape'] = ape_digest(p)
        if ape_err and not rec['err']:
            rec['err'] = ape_err
        if with_audio and rec['err'] is None:
            try:
                rec['audio'] = audio_sha(p)
            except (OSError, ValueError) as e:
                rec['err'] = 'audio:%s' % e
        out[p] = rec
    return out, walk_errors


# ─────────────────────────── Restore ───────────────────────────

def restore(root, dump_path):
    dump = json.load(open(dump_path, encoding='utf-8'))
    done = skip = miss = 0
    for rel, b64 in dump.items():
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            p_nfc = unicodedata.normalize('NFC', p)
            if not os.path.exists(p_nfc):
                miss += 1
                print('  없음: %s' % rel)
                continue
            p = p_nfc
        if read_v1(p) is not None:
            skip += 1
            continue
        with open(p, 'ab') as f:
            f.write(base64.b64decode(b64))
        done += 1
    print('ID3v1 복원 %d건 · 이미 있어 건너뜀 %d건 · 파일 없음 %d건' % (done, skip, miss))
    return 1 if miss else 0


# ─────────────────────────── Main ───────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--apply', action='store_true', help='실제로 쓴다 (없으면 dry-run)')
    ap.add_argument('--backup-dir', default=None,
                    help='덤프·원본 백업 저장 폴더 (기본: 현재 작업 디렉토리)')
    ap.add_argument('--dump', default=None, help='제거할 ID3v1 을 저장할 JSON 경로')
    ap.add_argument('--restore', default=None, help='이 덤프로 ID3v1 을 되돌린다')
    ap.add_argument('--no-audio-check', action='store_true',
                    help='오디오 해시 대조를 건너뛴다 (빠르지만 보증이 약해진다)')
    args = ap.parse_args()

    # Resolve symlinks such as macOS `/tmp` → `/private/tmp`. abspath alone
    # can make paths inside the root appear different and bypass the check.
    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        sys.exit('루트가 없다: %s' % root)
    if args.restore:
        return restore(root, args.restore)

    # Resolve symlinks before checking that output stays outside the library.
    out_dir = os.path.realpath(os.path.expanduser(args.backup_dir or os.getcwd()))
    if os.path.commonpath((root, out_dir)) == root:
        sys.exit('백업 폴더가 검사 루트 안이다. --backup-dir 로 루트 밖을 지정할 것:\n'
                 '  루트 = %s\n  백업 = %s' % (root, out_dir))
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        sys.exit('백업 경로가 폴더가 아니다: %s' % out_dir)
    # An explicit --dump keeps its working-directory-relative path semantics.
    dump_path = os.path.realpath(os.path.expanduser(args.dump)) if args.dump else None
    if dump_path and os.path.commonpath((root, dump_path)) == root:
        sys.exit('덤프 경로가 검사 루트 안이다. 루트 밖을 지정할 것: %s' % dump_path)

    want_audio = not args.no_audio_check
    print('처리 전 상태를 읽는다%s …' % ('' if not want_audio else ' (오디오 해시 포함, 오래 걸린다)'))
    before, walk_err_before = snapshot(root, with_audio=want_audio)

    unreadable = [p for p, r in before.items() if r['err']]
    # Removing v1 without a v2 tag would leave no ID3 metadata. Report these
    # files separately and leave them untouched until their tags migrate to v2.
    v1_only = [p for p, r in before.items() if r['v1'] and not r['v2'] and not r['err']]
    drop_v1 = [p for p, r in before.items() if r['v1'] and r['v2'] and not r['err']]
    to_v24 = [p for p, r in before.items() if r['v2'] and r['ver'] == 3]

    print()
    print('mp3 %d개' % len(before))
    print('  백업 저장 폴더      : %s' % out_dir)
    if dump_path:
        print('  ID3v1 덤프 경로     : %s' % dump_path)
    print('  ID3v1 제거 대상      : %d' % len(drop_v1))
    print('  v2.3 → v2.4 대상     : %d' % len(to_v24))
    print('  ID3v1 뿐이라 건너뜀  : %d' % len(v1_only))
    print('  열기 실패            : %d' % len(unreadable))
    print('  순회 실패            : %d' % len(walk_err_before))
    for e in walk_err_before:
        print('     %s' % e)
    for p in unreadable:
        print('     %s  (%s)' % (p, before[p]['err']))
    for p in v1_only:
        print('     [v1 only] %s' % p)
    for p in to_v24:
        print('     [v2.3] %s' % p)

    if not args.apply:
        print()
        print('dry-run 이다. 적용하려면 --apply 를 준다.')
        return 0

    if unreadable or walk_err_before:
        sys.exit('\n읽지 못한 파일이나 폴더가 있다. 그대로 두고 원인을 먼저 본다.')

    if to_v24 or (drop_v1 and dump_path is None):
        os.makedirs(out_dir, mode=0o700, exist_ok=True)

    # Never overwrite a dump: it is the only way to restore removed v1 tags.
    # Two runs in one second can collide; an empty second dump previously
    # overwrote the first. Do not create a dump when there is nothing to remove.
    if not drop_v1:
        print('\n제거할 ID3v1 이 없어 덤프를 만들지 않는다.')
    else:
        dump_path = dump_path or os.path.join(
            out_dir, 'id3v1-dump-%s.json' % datetime.now().strftime('%Y%m%d-%H%M%S'))
        if os.path.exists(dump_path):
            sys.exit('덤프가 이미 있다, 덮어쓰지 않는다: %s' % dump_path)
        # Store paths relative to root so a dump made from a copy can also
        # restore the originals; absolute paths would refer to the wrong tree.
        payload = {os.path.relpath(p, root): base64.b64encode(read_v1(p)).decode()
                   for p in drop_v1}
        with open(dump_path, 'x', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print('\nID3v1 %d건을 먼저 덤프했다 → %s (%d B)'
              % (len(payload), dump_path, os.path.getsize(dump_path)))
        print('되돌리기: python3 %s <루트> --restore %s'
              % (os.path.abspath(__file__), dump_path))

    failures = []

    # Only v2.3 upgrades rewrite the start of a file. An interrupted write can
    # corrupt it, and a 128-byte v1 dump cannot restore it. Keep a full copy of
    # each file selected for upgrade.
    if to_v24:
        bak = os.path.join(out_dir,
                           'v23-backup-%s' % datetime.now().strftime('%Y%m%d-%H%M%S'))
        # Timestamps have one-second resolution. Reject directory collisions
        # rather than silently overwriting backups with matching relative paths.
        try:
            os.makedirs(bak, exist_ok=False)
        except FileExistsError:
            sys.exit('백업 디렉토리가 이미 있다, 덮어쓰지 않는다: %s' % bak)
        for p in to_v24:
            dst = os.path.join(bak, os.path.relpath(p, root))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(p, dst)      # copy2 metadata copying failed with EPERM on SMB.
        print('v2.3 %d곡을 통째로 복사해 뒀다 → %s' % (len(to_v24), bak))

    # Loading with translate=True upgrades v2.3 frames, including TYER to
    # TDRC. Set encoding explicitly because translation alone does not change it.
    n23 = 0
    for p in to_v24:
        try:
            tag = ID3(p)
            for k in tag:
                fr = tag[k]
                if hasattr(fr, 'encoding'):
                    fr.encoding = 3
            tag.save(p, v2_version=4, v1=0)
            n23 += 1
        except Exception as e:
            failures.append('v2.3 승격 실패: %s (%r)' % (p, e))
    print('v2.3 → v2.4 %d건 완료' % n23)

    # Removing v1 truncates the final 128 bytes without rewriting audio.
    # Running again on a file without v1 does nothing.
    n1 = 0
    for p in drop_v1:
        try:
            if read_v1(p) is None:
                continue
            id3_delete(p, delete_v1=True, delete_v2=False)
            n1 += 1
        except Exception as e:
            failures.append('v1 제거 실패: %s (%r)' % (p, e))
    print('ID3v1 제거 %d건 완료' % n1)

    # ─── Verification ───
    print('\n=== 검증 ===')
    after, walk_err_after = snapshot(root, with_audio=want_audio)

    upgraded = set(to_v24)
    # The goal is ID3v2.4 with no ID3v1 in every file. Include untouched files
    # in this check; excluding them would report success while v1 remains.
    left_v1 = [p for p, r in after.items() if r['v1']]
    not_v24 = [p for p, r in after.items() if not r['v2'] or r['ver'] != 4]
    left_v23 = [p for p, r in after.items() if r['v2'] and r['ver'] == 3]
    # Only files upgraded in this run were rewritten as UTF-8. Existing v2.4
    # frame encodings are unchanged, so UTF-16 in those files is expected.
    left_utf16 = [p for p in upgraded if p in after and 1 in after[p]['enc']]
    err_after = [p for p, r in after.items() if r['err']]
    audio_diff, ape_diff, cover_diff, vanished, v1only_touched = [], [], [], [], []

    for p, b in before.items():
        a = after.get(p)
        if a is None:
            vanished.append(p)
            continue
        if want_audio and b['audio'] != a['audio']:
            audio_diff.append(p)
        if b['ape'] != a['ape']:
            ape_diff.append(p)
        if b['cover'] != a['cover']:
            cover_diff.append(p)
        if p in v1_only and (b['v1'], b['v2']) != (a['v1'], a['v2']):
            v1only_touched.append(p)

    # Check preservation and completion separately. Untouched files can pass
    # preservation checks while still leaving the normalization goal unmet.
    safety = [
        ('오디오 바이트 변형', audio_diff if want_audio else []),
        ('APE 내용 변화', ape_diff),
        ('커버 내용 변화', cover_diff),
        ('v1 전용 파일 변형', v1only_touched),
        ('파일 사라짐', vanished),
        ('처리 후 읽기 실패', err_after),
        ('처리 후 순회 실패', walk_err_after),
        ('처리 중 예외', failures),
    ]
    goal = [
        ('ID3v1 남음', left_v1),
        ('v2.3 남음', left_v23),
        ('ID3v2.4 아님', not_v24),
        ('승격분에 UTF-16', left_utf16),
    ]

    def report(title, rows):
        worst = False
        print('  [%s]' % title)
        for label, items in rows:
            print('    %s %-20s %d' % ('OK  ' if not items else '실패', label, len(items)))
            if items:
                worst = True
                for it in items[:10]:
                    print('          %s' % it)
        return worst

    broke = report('안전 — 건드리지 말아야 할 것', safety)
    if not want_audio:
        print('    --  오디오 해시 대조는 건너뜀')
    unfinished = report('목표 — ID3v2.4 한 벌', goal)

    hashed = sum(1 for r in after.values() if r['audio'])
    if want_audio and hashed != len(after):
        broke = True
        print('    실패 해시 누락             %d곡 중 %d곡만' % (len(after), hashed))

    print()
    if broke:
        print('안전 검사 실패. 위 항목을 확인할 것. 덤프로 ID3v1 은 되돌릴 수 있다.')
        return 1
    if unfinished:
        print('망가진 것은 없지만 목표 미달이다. 위 파일들은 ID3v2 가 없거나 v1 이 남아')
        print('있어 이 도구가 손대지 않는다 — v2 로 옮긴 뒤 다시 돌릴 것.')
        return 2
    if not want_audio:
        print('통과. 다만 오디오 해시를 건너뛰었으므로 오디오 보존은 확인되지 않았다.')
        return 0
    print('전부 통과. %d곡이 ID3v2.4 한 벌이고, 오디오 해시 동일, APE·커버 내용 보존.'
          % len(after))
    return 0


if __name__ == '__main__':
    sys.exit(main())
