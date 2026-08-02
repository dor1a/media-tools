#!/usr/bin/env python3
"""mkv 일괄 처리 — 트랙 확인 / 오디오 언어 / 자막 교체 / 트랙 제거."""

import json
import os
import subprocess
import sys

SUB_EXTS = ['.srt', '.ass']
LANGS = ['kor', 'jpn', 'eng', 'und']


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(mkv):
    r = run(['mkvmerge', '-J', mkv])
    if r.returncode not in (0, 1):
        return None
    return json.loads(r.stdout)


def tracks_of(info, kind):
    return [t for t in info['tracks'] if t['type'] == kind]


def find_candidates(root='.'):
    """현재 위치와 바로 아래 디렉토리 중 mkv 가 들어있는 곳."""
    root = os.path.abspath(root)
    out = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return out
    if any(f.lower().endswith('.mkv') for f in entries):
        out.append(root)
    for name in entries:
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        try:
            if any(f.lower().endswith('.mkv') for f in os.listdir(d)):
                out.append(d)
        except OSError:
            pass
    return out


def ask_dir(argv_path=None):
    path = argv_path
    if not path:
        cands = find_candidates()
        if cands:
            labels = [
                f"{os.path.relpath(c) if c != os.getcwd() else '.'}"
                f"  ({sum(1 for f in os.listdir(c) if f.lower().endswith('.mkv'))}개)"
                for c in cands
            ]
            choice = pick("\n📁 작업 폴더 (0=경로 직접 입력)", labels, allow_exit=True)
            if choice is not None:
                path = cands[labels.index(choice)]
        if not path:
            path = input("\n📁 작업 폴더 경로: ").strip().strip('"')
    if not path:
        print("❌ 경로가 필요합니다.")
        return None, []

    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        print(f"❌ 폴더가 없습니다: {path}")
        return None, []
    mkvs = sorted(
        os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith('.mkv')
    )
    if not mkvs:
        print(f"❌ mkv 파일이 없습니다: {path}")
        return path, []
    print(f"✅ {len(mkvs)}개 발견")
    return path, mkvs


def pick(prompt, options, default=None, allow_exit=False):
    print()
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    suffix = f" (엔터 시 {default})" if default else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default:
            return default
        if allow_exit and raw == '0':
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  잘못된 입력입니다.")


def confirm(n):
    return input(f"\n{n}개 파일에 적용할까요? (y/n): ").strip().lower() == 'y'


def sub_for(mkv):
    """Find <basename>.kor.srt / .kor.ass next to the mkv."""
    base = mkv[:-4]
    for ext in SUB_EXTS:
        cand = f"{base}.kor{ext}"
        if os.path.isfile(cand):
            return cand
    return None


def remux(mkv, pre_args, sub=None):
    """Rebuild the mkv, keeping the original as <name>.backup."""
    tmp = os.path.join(os.path.dirname(mkv), f"temp_{os.path.basename(mkv)}")
    cmd = ['mkvmerge', '-o', tmp] + pre_args + [mkv]
    if sub:
        cmd += ['--language', '0:kor', sub]
    r = run(cmd)
    if r.returncode not in (0, 1):
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, r.stderr.strip().splitlines()[-1] if r.stderr else 'mkvmerge 실패'
    os.replace(mkv, f"{mkv}.backup")
    os.replace(tmp, mkv)
    return True, None


# --- actions ---------------------------------------------------------------

def act_info(mkvs):
    print()
    for mkv in mkvs:
        info = probe(mkv)
        if not info:
            print(f"  {os.path.basename(mkv)}: ❌ 읽기 실패")
            continue
        v = tracks_of(info, 'video')
        a = tracks_of(info, 'audio')
        s = tracks_of(info, 'subtitles')
        vs = f"{v[0]['codec']} {v[0]['properties'].get('pixel_dimensions', '?')}" if v else '-'
        # a<n> is what mkvpropedit takes, id=<n> is what mkvmerge takes
        as_ = ', '.join(
            f"a{i+1}/id{t['id']}:{t['properties'].get('language', 'und')} {t['codec']}"
            for i, t in enumerate(a)
        ) or '-'
        ss = ', '.join(t['properties'].get('language', 'und') for t in s) or '없음'
        print(f"  {os.path.basename(mkv)}")
        print(f"    🎬 {vs}   🔊 {as_}   💬 {ss}")


def act_check_audio(mkvs):
    want = pick("기대하는 오디오 언어", LANGS, default='kor')
    print()
    bad = 0
    for mkv in mkvs:
        info = probe(mkv)
        if not info:
            continue
        a = tracks_of(info, 'audio')
        lang = a[0]['properties'].get('language', 'und') if a else '없음'
        if lang != want:
            bad += 1
            print(f"  ❌ {os.path.basename(mkv)}: {lang}")
    print(f"\n{'전부 일치합니다.' if not bad else f'{bad}개가 {want}가 아닙니다.'}")


def act_set_audio(mkvs):
    lang = pick("설정할 오디오 언어", LANGS, default='kor')
    idx = input("오디오 트랙 (정보 화면의 a번호, 엔터 시 1): ").strip() or '1'
    if not confirm(len(mkvs)):
        return
    print()
    for mkv in mkvs:
        r = run(['mkvpropedit', mkv, '--edit', f'track:a{idx}', '--set', f'language={lang}'])
        ok = r.returncode == 0
        print(f"  {'✅' if ok else '❌'} {os.path.basename(mkv)}")


def act_add_subs(mkvs):
    keep = pick("기존 자막은?", ['유지하고 추가', '전부 제거하고 교체'], default='전부 제거하고 교체')
    pre = [] if keep.startswith('유지') else ['-S']

    pairs, missing = [], []
    for mkv in mkvs:
        sub = sub_for(mkv)
        (pairs if sub else missing).append((mkv, sub))
    if missing:
        print(f"\n⚠️  자막을 못 찾은 파일 {len(missing)}개 (<이름>.kor.srt/.ass 규칙):")
        for mkv, _ in missing[:5]:
            print(f"     {os.path.basename(mkv)}")
    if not pairs:
        print("\n❌ 처리할 파일이 없습니다.")
        return
    print(f"\n적용 대상 {len(pairs)}개:")
    for mkv, sub in pairs[:5]:
        print(f"  {os.path.basename(mkv)} ← {os.path.basename(sub)}")
    if len(pairs) > 5:
        print(f"  ... 외 {len(pairs) - 5}개")
    if not confirm(len(pairs)):
        return
    print()
    for mkv, sub in pairs:
        ok, err = remux(mkv, pre, sub)
        print(f"  {'✅' if ok else '❌'} {os.path.basename(mkv)}" + (f"  {err}" if err else ""))


def act_drop_audio(mkvs):
    idx = input("\n제거할 오디오 트랙 (정보 화면의 id번호): ").strip()
    if not idx.isdigit():
        print("❌ 숫자를 입력하세요.")
        return
    if not confirm(len(mkvs)):
        return
    print()
    for mkv in mkvs:
        ok, err = remux(mkv, ['-a', f'!{idx}'])
        print(f"  {'✅' if ok else '❌'} {os.path.basename(mkv)}" + (f"  {err}" if err else ""))


def act_clean_backups(mkvs):
    baks = [f"{m}.backup" for m in mkvs if os.path.isfile(f"{m}.backup")]
    if not baks:
        print("\n삭제할 .backup 파일이 없습니다.")
        return
    size = sum(os.path.getsize(b) for b in baks) / 1024**3
    print(f"\n{len(baks)}개, {size:.1f} GB")
    if input("정말 삭제할까요? (y/n): ").strip().lower() == 'y':
        for b in baks:
            os.remove(b)
        print("✅ 삭제했습니다.")


ACTIONS = [
    ("트랙 정보 보기", act_info),
    ("오디오 언어 검사", act_check_audio),
    ("오디오 언어 변경", act_set_audio),
    ("자막 넣기", act_add_subs),
    ("오디오 트랙 제거", act_drop_audio),
    (".backup 정리", act_clean_backups),
]


def main():
    for tool in ('mkvmerge', 'mkvpropedit'):
        if not run(['which', tool]).stdout.strip():
            print(f"❌ {tool} 이 없습니다. brew install mkvtoolnix")
            return

    path, mkvs = ask_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    if not mkvs:
        return

    while True:
        choice = pick("\n무엇을 할까요? (0=종료)", [n for n, _ in ACTIONS], allow_exit=True)
        if choice is None:
            return
        for name, fn in ACTIONS:
            if name == choice:
                fn(mkvs)
                break
        if input("\n계속할까요? (y/n): ").strip().lower() != 'y':
            return


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n중단했습니다.")
        sys.exit(130)
