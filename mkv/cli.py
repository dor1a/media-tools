#!/usr/bin/env python3
"""Terminal interface for batch MKV processing.

    python3 -m mkv.cli [folder]

Ask for a folder once and use it for tracks, subtitles and renaming.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathinput
from mkv import core

NAME_W = 44


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


def short(name):
    return name if len(name) <= NAME_W else name[:NAME_W - 1] + '…'


def ask_dir(argv_path=None):
    path = argv_path
    if not path:
        # Accept either a number or a path. With tab completion available,
        # entering a path is valid even when the numbered list is shown.
        cands = core.find_candidates()
        print()
        for i, c in enumerate(cands, 1):
            label = os.path.relpath(c) if c != os.getcwd() else '.'
            print(f"  {i}. {label}  ({len(core.mkvs_in(c))}개)")
        if not cands:
            print("  (가까운 곳에서 mkv 폴더를 못 찾았습니다)")
        while True:
            raw = input("\n📁 번호 또는 경로 (0=취소): ").strip().strip('"')
            if raw == '0':
                return None, []
            if raw.isdigit() and 1 <= int(raw) <= len(cands):
                path = cands[int(raw) - 1]
                break
            if raw:
                path = raw
                break
            print("  번호나 경로를 입력하세요.")

    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        print(f"❌ 폴더가 없습니다: {path}")
        return None, []
    mkvs = core.mkvs_in(path)
    if not mkvs:
        print(f"❌ mkv 파일이 없습니다: {path}")
        return path, []
    print(f"✅ {len(mkvs)}개 발견")
    return path, mkvs


def run_remux(items, pre_args):
    """Remux [(mkv, sub)] or [mkv] sequentially with progress on one line.

    Ask whether to delete this run's original backups once the results
    are visible, rather than making cleanup a separate menu action.
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
        ok, err = core.remux(mkv, pre_args, sub, on_progress=show)
        print(f"\r  {'✅' if ok else '❌'} [{i}/{total}] {name}"
              + (f"  {err}" if err else "") + ' ' * 10)
        if ok:
            done.append(mkv)
    offer_backup_cleanup(done)


def offer_backup_cleanup(mkvs):
    baks = core.backups_for(mkvs)
    if not baks:
        return
    print(f"\n원본 백업 {len(baks)}개 ({core.backup_size_gb(baks):.1f} GB)")
    if input("지울까요? (y/n): ").strip().lower() == 'y':
        core.delete_backups(baks)
        print("✅ 지웠습니다.")
    else:
        print("남겨뒀습니다. 결과 확인하고 직접 지우면 됩니다.")


# --- actions ---------------------------------------------------------------

def act_info(mkvs):
    print()
    for mkv in mkvs:
        s = core.summary(mkv)
        if not s:
            print(f"  {os.path.basename(mkv)}: ❌ 읽기 실패")
            continue
        audio = ', '.join(f"a{a}/id{i}:{lang} {codec}" for a, i, lang, codec in s['audio']) or '-'
        print(f"  {os.path.basename(mkv)}")
        print(f"    🎬 {s['video'] or '-'}   🔊 {audio}   💬 {', '.join(s['subs']) or '없음'}")


def act_check_audio(mkvs):
    want = pick("기대하는 오디오 언어", core.LANGS, default='kor')
    print()
    bad = 0
    for mkv in mkvs:
        lang = core.audio_language(mkv)
        if lang is False:
            continue
        if lang != want:
            bad += 1
            print(f"  ❌ {os.path.basename(mkv)}: {lang or '없음'}")
    print(f"\n{'전부 일치합니다.' if not bad else f'{bad}개가 {want}가 아닙니다.'}")


def act_set_audio(mkvs):
    lang = pick("설정할 오디오 언어", core.LANGS, default='kor')
    idx = input("오디오 트랙 (정보 화면의 a번호, 엔터 시 1): ").strip() or '1'
    if not confirm(len(mkvs)):
        return
    print()
    for mkv in mkvs:
        ok = core.set_audio_language(mkv, idx, lang)
        print(f"  {'✅' if ok else '❌'} {os.path.basename(mkv)}")


def act_add_subs(mkvs):
    keep = pick("기존 자막은?", ['유지하고 추가', '전부 제거하고 교체'], default='전부 제거하고 교체')
    pre = [] if keep.startswith('유지') else ['-S']

    pairs, missing = [], []
    for mkv in mkvs:
        sub = core.sub_for(mkv)
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
    """Rename using TVDB/TMDB, importing the renamer on demand.

    Users of the MKV operations alone do not need that dependency.
    """
    try:
        from renamer import cli as renamer_cli
    except ImportError as e:
        print(f"\n❌ renamer 를 불러오지 못했습니다: {e}")
        print("   uv pip install -r requirements.txt")
        return 1
    return renamer_cli.run(os.path.dirname(mkvs[0]))


ACTIONS = [
    ("이름 정리 (TVDB/TMDB)", act_rename),
    ("트랙 정보 보기", act_info),
    ("오디오 언어 검사", act_check_audio),
    ("오디오 언어 변경", act_set_audio),
    ("자막 넣기", act_add_subs),
    ("오디오 트랙 제거", act_drop_audio),
]


def main(path_arg=None):
    """Accept the folder argument forwarded by media.py as path_arg."""
    pathinput.enable()

    missing = core.missing_tools()
    if missing:
        print(f"❌ {', '.join(missing)} 이 없습니다. brew install mkvtoolnix")
        return 1

    path, mkvs = ask_dir(path_arg or (sys.argv[1] if len(sys.argv) > 1 else None))
    if not mkvs:
        return 1

    result = 0
    while True:
        choice = pick("\n무엇을 할까요? (0=종료)", [n for n, _ in ACTIONS], allow_exit=True)
        if choice is None:
            return result
        for name, fn in ACTIONS:
            if name == choice:
                action_result = fn(mkvs)
                if action_result:
                    result = action_result
                mkvs = core.mkvs_in(path)
                break
        if input("\n계속할까요? (y/n): ").strip().lower() != 'y':
            return result
        if not mkvs:
            print('처리할 mkv 파일이 없습니다.')
            return result or 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n중단했습니다.")
        sys.exit(130)
