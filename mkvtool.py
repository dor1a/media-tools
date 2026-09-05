#!/usr/bin/env python3
"""mkv 일괄 처리 — 트랙 확인 / 오디오 언어 / 자막 교체 / 트랙 제거."""

import json
import os
import re
import subprocess
import sys

SUB_EXTS = ['.srt', '.ass']
LANGS = ['kor', 'jpn', 'eng', 'und']

# mkvmerge --gui-mode 는 진행률을 줄 단위로 뱉는다 (기본 모드는 \r 로 덮어써서 파싱이 번거롭다)
PROGRESS_RE = re.compile(r'#GUI#progress\s+(\d+)%')
NAME_W = 44


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


def short(name):
    return name if len(name) <= NAME_W else name[:NAME_W - 1] + '…'


def remux(mkv, pre_args, sub=None, on_progress=None):
    """Rebuild the mkv, keeping the original as <name>.backup.

    remux 는 파일 전체를 다시 쓴다. 네트워크 마운트면 GB 단위 왕복이라
    파일당 수 분이 걸리므로, 진행률을 흘려보내지 않으면 멈춘 것처럼 보인다.
    """
    tmp = os.path.join(os.path.dirname(mkv), f"temp_{os.path.basename(mkv)}")
    cmd = ['mkvmerge', '--gui-mode', '-o', tmp] + pre_args + [mkv]
    if sub:
        cmd += ['--language', '0:kor', sub]

    tail = []
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        for line in p.stdout:
            m = PROGRESS_RE.search(line)
            if m:
                if on_progress:
                    on_progress(int(m.group(1)))
            elif line.strip():
                tail.append(line.strip())
                del tail[:-4]
        rc = p.wait()
    except FileNotFoundError:
        return False, 'mkvmerge 를 찾을 수 없습니다'

    if rc not in (0, 1):
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, tail[-1] if tail else 'mkvmerge 실패'
    os.replace(mkv, f"{mkv}.backup")
    os.replace(tmp, mkv)
    return True, None


def run_remux(items, pre_args):
    """[(mkv, sub)] 또는 [mkv] 를 순차 remux 하면서 진행률을 한 줄로 갱신한다.

    끝나면 이번에 생긴 원본 백업을 지울지 묻는다 — 결과를 본 직후가
    지우기 좋은 시점이라 별도 메뉴로 두지 않았다.
    """
    total = len(items)
    done = []
    for i, item in enumerate(items, 1):
        mkv, sub = item if isinstance(item, tuple) else (item, None)
        name = short(os.path.basename(mkv))
        head = f"  [{i}/{total}] {name:<{NAME_W}}"

        def show(pct, head=head):
            print(f"\r{head} {pct:3d}%", end='', flush=True)

        show(0)
        ok, err = remux(mkv, pre_args, sub, on_progress=show)
        print(f"\r  {'✅' if ok else '❌'} [{i}/{total}] {name}"
              + (f"  {err}" if err else "") + ' ' * 10)
        if ok:
            done.append(mkv)
    offer_backup_cleanup(done)


def offer_backup_cleanup(mkvs):
    baks = [f"{m}.backup" for m in mkvs if os.path.isfile(f"{m}.backup")]
    if not baks:
        return
    size = sum(os.path.getsize(b) for b in baks) / 1024 ** 3
    print(f"\n원본 백업 {len(baks)}개 ({size:.1f} GB)")
    if input("지울까요? (y/n): ").strip().lower() == 'y':
        for b in baks:
            os.remove(b)
        print("✅ 지웠습니다.")
    else:
        print("남겨뒀습니다. 결과 확인하고 직접 지우면 됩니다.")


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
    run_remux(pairs, pre)


def act_drop_audio(mkvs):
    idx = input("\n제거할 오디오 트랙 (정보 화면의 id번호): ").strip()
    if not idx.isdigit():
        print("❌ 숫자를 입력하세요.")
        return
    if not confirm(len(mkvs)):
        return
    print()
    run_remux(mkvs, ['-a', f'!{idx}'])


def act_rename(mkvs):
    """TMDB 로 이름 정리. renamer 는 requests 가 필요해서 여기서만 늦게 import 한다
    — mkv 기능만 쓰는 사람이 의존성 없이 돌릴 수 있어야 한다."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from renamer import cli as renamer_cli
    except ImportError as e:
        print(f"\n❌ renamer 를 불러오지 못했습니다: {e}")
        print("   pip install -r requirements.txt")
        return
    renamer_cli.run(os.path.dirname(mkvs[0]))


ACTIONS = [
    ("이름 정리 (TMDB)", act_rename),
    ("트랙 정보 보기", act_info),
    ("오디오 언어 검사", act_check_audio),
    ("오디오 언어 변경", act_set_audio),
    ("자막 넣기", act_add_subs),
    ("오디오 트랙 제거", act_drop_audio),
]


def main(path_arg=None):
    """path_arg 는 media.py 가 자기 인자를 넘겨줄 때 쓴다."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pathinput
    pathinput.enable()

    for tool in ('mkvmerge', 'mkvpropedit'):
        if not run(['which', tool]).stdout.strip():
            print(f"❌ {tool} 이 없습니다. brew install mkvtoolnix")
            return

    path, mkvs = ask_dir(path_arg or (sys.argv[1] if len(sys.argv) > 1 else None))
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
