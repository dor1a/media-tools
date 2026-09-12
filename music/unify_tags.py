#!/usr/bin/env python3
"""라이브러리 mp3 를 ID3v2.4 한 벌로 통일한다.

  - ID3v1 제거 (파일 끝 128바이트 truncate, 오디오는 안 건드린다)
  - ID3v2.3 → ID3v2.4, 그 파일들의 텍스트 프레임은 UTF-8 로 다시 쓴다

APEv2 와 APIC 는 그대로 둔다. 이미 v2.4 인 파일의 프레임 인코딩도 건드리지
않는다 — ASCII 범위에서는 latin-1 과 UTF-8 의 바이트가 같아 기능 차이가 없고,
고치자고 2300 곡을 다시 쓸 이유가 없다.

한 번에 성공해야 하는 작업이라 스스로 검증한다. 처리 전에 모든 파일의 오디오
구간(태그를 벗긴 MPEG 프레임)과 APE 값, 커버 이미지를 해시해 두고, 처리 후
다시 해시해 하나라도 다르면 실패로 끝낸다. 읽지 못한 파일도 실패로 센다.

덤프와 백업은 현재 작업 디렉토리에 남는다. 그 자리가 검사 루트 안이면 사본이
검사 대상으로 섞이므로 실행을 거부한다 — 루트 밖에서 돌린다.

패키지에서 아무것도 가져오지 않으므로 경로로 직접 부른다.

  python3 <경로>/music/unify_tags.py <루트>                   # 보기만
  python3 <경로>/music/unify_tags.py <루트> --apply           # 적용 + 자가검증
  python3 <경로>/music/unify_tags.py <루트> --restore <덤프>  # ID3v1 되돌리기
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


# ─────────────────────────── 파일 훑기 ───────────────────────────

def walk_mp3(root, errors=None):
    """readdir 이 준 이름을 그대로 쓰지 않는다.

    macOS 가 SMB 에서 돌려주는 이름은 NFD 라, 한글·가나가 자모로 쪼개지며
    바이트가 2~3배로 분다. 그대로 open 에 넣으면 255바이트 한도를 넘겨
    EINVAL 이 나는 파일이 생긴다 (2026-09-11 실측, 신서유기 S02 4건).

    `errors` 를 주면 순회 중 열지 못한 디렉토리를 담는다. os.walk 는 기본으로
    그런 디렉토리를 조용히 건너뛰기 때문에, 안 받으면 접근 불가 폴더 하나가
    통째로 빠진 채 "전부 통과" 가 나온다.
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


# ─────────────────────────── 태그 경계 ───────────────────────────

def has_id3v2(path):
    """ID3v2 블록이 있는지 파일 머리 3바이트로 직접 본다.

    mutagen 에 맡기면 안 된다. ID3v2 가 없는 파일에서 `ID3()` 는 예외를 내지
    않고 ID3v1 을 읽어 `version = (1, 1)` 로 돌려준다. 그래서 "v2 가 없다" 를
    예외나 version 으로 판정하면 v1 만 있는 파일이 그물을 빠져나간다.
    """
    try:
        with open(path, 'rb') as f:
            return f.read(3) == b'ID3'
    except OSError:
        return False


def read_v1(path):
    """파일 끝 128바이트가 ID3v1 이면 그대로 돌려준다."""
    try:
        with open(path, 'rb') as f:
            f.seek(-128, 2)
            tail = f.read(128)
    except OSError:
        return None
    return tail if tail[:3] == b'TAG' else None


def synchsafe(b):
    """ID3v2 헤더의 크기 필드. 바이트마다 최상위 비트를 버리고 7비트씩 잇는다.

    마스크만 씌우고 보통 32비트 정수로 읽으면 안 된다. 그러면 자리값이 8비트
    간격이 되어 크기가 부풀고, 오디오 시작 위치가 뒤로 밀린다. 실측에서 한
    파일이 5,948,288바이트를 건너뛴 채 해시됐다.
    """
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def audio_span(path):
    """태그를 벗긴 오디오 구간 (start, end).

    앞은 ID3v2 블록, 뒤는 ID3v1 과 APEv2 를 걷어낸다. 이 구간이 처리 전후로
    같으면 오디오는 한 바이트도 안 건드린 것이다.
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
                if ape_flags & 0x80000000:                     # header 도 있으면
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


# ─────────────────────────── 스냅샷 ───────────────────────────

def ape_digest(path):
    """APE 의 키와 값을 함께 해시해 `(오류, 해시)` 로 돌려준다.

    키 이름만 보면 값이 바뀐 것을 놓친다. 그리고 오류를 해시 자리에 문자열로
    끼워 넣으면 안 된다 — 전후가 똑같이 깨져 있을 때 두 문자열이 같아서
    "APE 내용 보존" 으로 통과한다. 오류는 오류로 올린다.
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
    """APIC 의 이미지 데이터까지 해시한다. 총 크기만 보면 같은 크기의 다른
    그림으로 바뀐 것을 놓친다."""
    h = hashlib.sha256()
    for k in sorted(k for k in tag.keys() if k.startswith('APIC')):
        fr = tag[k]
        h.update(k.encode('utf-8', 'replace'))
        h.update(b'\x00')
        h.update(fr.data)
        h.update(b'\x00')
    return h.hexdigest()


def snapshot(root, with_audio=True, skip=()):
    """처리 전후 대조에 쓸 상태를 `(기록, 순회오류)` 로 돌려준다.

    err 가 있는 항목과 순회 오류는 검증에서 실패로 센다.
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
            rec['ver'] = 0                      # ID3v2 가 없는 파일
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


# ─────────────────────────── 되돌리기 ───────────────────────────

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


# ─────────────────────────── 본체 ───────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--apply', action='store_true', help='실제로 쓴다 (없으면 dry-run)')
    ap.add_argument('--dump', default=None, help='제거할 ID3v1 을 저장할 JSON 경로')
    ap.add_argument('--restore', default=None, help='이 덤프로 ID3v1 을 되돌린다')
    ap.add_argument('--no-audio-check', action='store_true',
                    help='오디오 해시 대조를 건너뛴다 (빠르지만 보증이 약해진다)')
    args = ap.parse_args()

    # realpath 로 푼다. abspath 만 쓰면 macOS 의 `/tmp` → `/private/tmp` 같은
    # 심링크가 안 풀려서, 루트 안에서 돌려도 경로가 달라 보여 검사를 지나친다.
    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        sys.exit('루트가 없다: %s' % root)
    if args.restore:
        return restore(root, args.restore)

    # 덤프와 백업이 검사 루트 안에 떨어지면 사본이 다음 순회에 섞인다.
    out_dir = os.path.realpath(os.getcwd())
    if out_dir == root or out_dir.startswith(root + os.sep):
        sys.exit('작업 디렉토리가 검사 루트 안이다. 루트 밖에서 실행할 것:\n'
                 '  루트 = %s\n  현재 = %s' % (root, out_dir))

    want_audio = not args.no_audio_check
    print('처리 전 상태를 읽는다%s …' % ('' if not want_audio else ' (오디오 해시 포함, 오래 걸린다)'))
    before, walk_err_before = snapshot(root, with_audio=want_audio)

    unreadable = [p for p, r in before.items() if r['err']]
    # ID3v2 가 없는 파일에서 v1 을 떼면 태그가 하나도 안 남는다. 건드리지 않고
    # 따로 보고한다 — 이런 파일은 v2 로 옮기는 것이 먼저다.
    v1_only = [p for p, r in before.items() if r['v1'] and not r['v2'] and not r['err']]
    drop_v1 = [p for p, r in before.items() if r['v1'] and r['v2'] and not r['err']]
    to_v24 = [p for p, r in before.items() if r['v2'] and r['ver'] == 3]

    print()
    print('mp3 %d개' % len(before))
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

    # 덤프는 되돌릴 유일한 수단이라 덮어쓰지 않는다. 같은 초에 두 번 돌리면
    # 이름이 겹치고, 두 번째는 지울 v1 이 없어 빈 덤프가 되어 앞의 것을
    # 날린다 (실측). 지울 것이 없으면 아예 만들지 않는다.
    if not drop_v1:
        print('\n제거할 ID3v1 이 없어 덤프를 만들지 않는다.')
    else:
        dump_path = args.dump or os.path.join(
            out_dir, 'id3v1-dump-%s.json' % datetime.now().strftime('%Y%m%d-%H%M%S'))
        if os.path.exists(dump_path):
            sys.exit('덤프가 이미 있다, 덮어쓰지 않는다: %s' % dump_path)
        # 경로는 루트 기준 상대로 담는다. 사본에서 뽑은 덤프를 원본에
        # 되돌릴 때 절대경로면 맞지 않는다.
        payload = {os.path.relpath(p, root): base64.b64encode(read_v1(p)).decode()
                   for p in drop_v1}
        with open(dump_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print('\nID3v1 %d건을 먼저 덤프했다 → %s (%d B)'
              % (len(payload), dump_path, os.path.getsize(dump_path)))
        print('되돌리기: python3 %s <루트> --restore %s'
              % (os.path.abspath(__file__), dump_path))

    failures = []

    # v2.3 승격은 이 작업에서 유일하게 파일 앞부분을 다시 쓴다. 도중에 죽으면
    # 파일이 깨질 수 있고 덤프(v1 128바이트)로는 되돌릴 수 없다. 대상이 몇
    # 개뿐이라 통째로 복사해 둔다.
    if to_v24:
        bak = os.path.join(out_dir,
                           'v23-backup-%s' % datetime.now().strftime('%Y%m%d-%H%M%S'))
        # 이름이 초 단위라 같은 작업 디렉토리에서 같은 초에 두 번 돌면 겹친다.
        # 겹치면 상대경로가 같은 백업이 조용히 덮이므로 아예 만들지 않는다.
        try:
            os.makedirs(bak, exist_ok=False)
        except FileExistsError:
            sys.exit('백업 디렉토리가 이미 있다, 덮어쓰지 않는다: %s' % bak)
        for p in to_v24:
            dst = os.path.join(bak, os.path.relpath(p, root))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(p, dst)      # copy2 는 SMB 에서 메타데이터 단계가 EPERM 이다
        print('v2.3 %d곡을 통째로 복사해 뒀다 → %s' % (len(to_v24), bak))

    # v2.3 을 올린다. translate=True 로 로드하면 mutagen 이 TYER 을 TDRC 로
    # 바꿔 v2.4 프레임으로 만들어 준다. 인코딩은 따라오지 않으므로 직접 지정한다.
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

    # v1 제거는 끝 128바이트 truncate 라 오디오를 다시 쓰지 않는다. 이미 없는
    # 파일에 다시 돌려도 아무 일도 일어나지 않는다.
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

    # ─── 자가검증 ───
    print('\n=== 검증 ===')
    after, walk_err_after = snapshot(root, with_audio=want_audio)

    upgraded = set(to_v24)
    left_v1 = [p for p, r in after.items() if r['v1'] and p not in v1_only]
    left_v23 = [p for p, r in after.items() if r['v2'] and r['ver'] == 3]
    # UTF-8 로 다시 쓴 것은 이번에 승격한 파일뿐이다. 원래부터 v2.4 인 파일의
    # 인코딩은 건드리지 않으므로 UTF-16 이 남아 있어도 정상이다.
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

    checks = [
        ('ID3v1 남음', left_v1),
        ('v2.3 남음', left_v23),
        ('승격분에 UTF-16', left_utf16),
        ('오디오 바이트 변형', audio_diff if want_audio else []),
        ('APE 내용 변화', ape_diff),
        ('커버 내용 변화', cover_diff),
        ('v1 전용 파일 변형', v1only_touched),
        ('파일 사라짐', vanished),
        ('처리 후 읽기 실패', err_after),
        ('처리 후 순회 실패', walk_err_after),
        ('처리 중 예외', failures),
    ]
    bad = False
    for label, items in checks:
        print('  %s %-20s %d' % ('OK  ' if not items else '실패', label, len(items)))
        if items:
            bad = True
            for it in items[:10]:
                print('        %s' % it)
    if not want_audio:
        print('  --  오디오 해시 대조는 건너뜀')

    print()
    if bad:
        print('검증 실패. 위 항목을 확인할 것. 덤프로 ID3v1 은 되돌릴 수 있다.')
        return 1
    if not want_audio:
        print('통과. 다만 오디오 해시를 건너뛰었으므로 오디오 보존은 확인되지 않았다.')
        return 0
    hashed = sum(1 for r in after.values() if r['audio'])
    if hashed != len(after):
        print('실패: %d곡 중 %d곡만 해시됐다.' % (len(after), hashed))
        return 1
    print('전부 통과. 오디오 %d곡 해시 동일, APE·커버 내용 보존.' % hashed)
    return 0


if __name__ == '__main__':
    sys.exit(main())
