#!/usr/bin/env python3
"""iTunes 음원 정리 — 터미널.

    python3 -m music.cli [폴더]

파일을 옮기기 전에 계획을 통째로 보여준다. 목적지가 /Volumes/Media 라
되돌리기가 어렵다.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathinput
from music import core

# 라이브러리 최상위 버킷. iTunes 의 장르로는 못 채운다 — Utaite 와 Melody 는
# 애플에 대응 장르가 없고, 같은 아티스트가 Anime 인지 J-Pop 인지도 사람 기준이다.
GENRES = ['J-Pop', 'K-Pop', 'C-Pop', 'Pop', 'Anime', 'Game', 'Utaite', 'Melody']


def pick(prompt, options, render, default=None, zero='건너뜀'):
    print()
    for i, o in enumerate(options, 1):
        mark = ' (기본)' if o == default else ''
        print(f"  {i}. {render(o)}{mark}")
    while True:
        raw = input(f"{prompt} (0={zero}{', 엔터=기본' if default is not None else ''}): ").strip()
        if raw == '0':
            return None
        if not raw and default is not None:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  잘못된 입력입니다.")


def ask_source():
    """음원이 든 폴더를 고르게 한다. 못 찾으면 직접 입력."""
    cands = core.find_candidates()
    if cands:
        labels = [f"{_short_path(d)}  ({n}개)" for d, n in cands]
        choice = pick("\n📁 작업 폴더", labels, str, zero='경로 직접 입력')
        if choice is not None:
            return str(cands[labels.index(choice)][0])
    return input("\n📁 폴더 또는 파일 경로: ").strip().strip('"')


def _short_path(p):
    home = Path.home()
    try:
        return '~/' + str(p.relative_to(home))
    except ValueError:
        return str(p)


def ask_country():
    print("\n스토어를 고른다. 같은 앨범도 스토어마다 표기와 발매일이 다르다.")
    return pick("국가", core.COUNTRY_CODES, str, default='jp')


def plan_album(files, base, country, genre, full_date):
    """파일들을 한 앨범으로 묶어 계획을 만든다. 앨범과 타입은 한 번만 묻는다."""
    query = core.guess_query(files[0])
    print(f"\n🔍 검색어: {query}")
    albums = core.search_albums(query, country)
    if not albums:
        print("  검색 결과가 없습니다.")
        return []

    album = pick(
        "앨범", albums,
        lambda a: f"[{(a['releaseDate'] or '')[:10]}] {a['collectionName']} / {a['artistName']}"
                  f"  ({a.get('trackCount') or '?'}곡)",
    )
    if not album:
        return []

    suggested = core.album_type(album)
    label = suggested or '(타입 폴더 없음 — Various Artists)'
    print(f"\n  제안된 타입: {label}")
    if input("  그대로 쓸까? (엔터=예, n=고르기): ").strip().lower() == 'n':
        kind = pick("타입", core.TYPES, str)
    else:
        kind = suggested

    tracks = core.album_tracks(album['collectionId'], country)

    moves = []
    for f in files:
        track = pick(
            f"'{f.name}' 의 트랙", tracks,
            lambda t: f"{str(t.get('trackNumber') or '?').zfill(2)} - {t['trackName']}",
        )
        if not track:
            print(f"  건너뜀: {f.name}")
            continue
        dst = core.destination(base, genre, album, track, f.suffix,
                               full_date=full_date, type_folder=kind)
        moves.append(core.Move(f, dst, track))
    return moves


def normalize(files, workdir):
    """라이브러리 포맷(mp3 320, flac)으로 맞춘다. 무엇을 할지 먼저 보여준다."""
    from music import convert

    plans = [(f, *convert.verdict(f)) for f in files]
    todo = [p for p in plans if p[1] == 'encode']
    keep = [p for p in plans if p[1] == 'keep']

    if keep:
        print(f"\n── 그대로 두는 것 {len(keep)}개 ──")
        for f, _, why in keep:
            print(f"  {f.name}  ({why})")
    if not todo:
        print("\n변환할 것이 없습니다.")
        return files

    print(f"\n── 변환 {len(todo)}개 ──")
    for f, _, why in todo:
        print(f"  {f.name}\n    {why}")
    print("  원본은 지우지 않습니다.")
    if input("\n변환할까? (엔터=예, n=취소): ").strip().lower() == 'n':
        return []

    def report(src, action, why, err):
        if action == 'encode':
            print(f"  ✅ {Path(src).name}")
        elif action == 'fail':
            print(f"  ❌ {Path(src).name} — {err}")

    print()
    try:
        return convert.convert_all(files, workdir, on_each=report)
    except convert.FFmpegMissing as e:
        print(f"❌ {e}")
        return []


ACTIONS = [
    ("라이브러리 편입   변환 → 검색 → 이동 → 태그", 'organize'),
    ("변환만            mp3 320 으로 맞추기 (flac 과 이미 320 인 것은 그대로)", 'convert'),
]


def run_convert(files, workdir):
    """포맷만 맞춘다. 정리는 나중에 하고 싶을 때."""
    out = normalize(files, workdir)
    if not out:
        return 1
    moved = [f for f in out if f.parent.name == '_converted']
    if moved:
        print(f"\n{len(moved)}개를 {workdir / '_converted'} 에 두었습니다.")
    return 0


def run_organize(files, workdir):
    """검색해서 라이브러리에 편입한다."""
    default_base = '/Volumes/Media/Music'
    raw_base = input(f"라이브러리 루트 (엔터 시 {default_base}): ").strip()
    base = Path(raw_base or default_base).expanduser()
    if not base.is_dir():
        print(f"❌ 그런 폴더가 없습니다: {base}")
        return 1

    genre = pick("장르 버킷", GENRES, str)
    if not genre:
        print("장르를 골라야 경로가 정해집니다.")
        return 1

    full_date = input("발매일을 YYYY.MM.DD 로 쓸까? (엔터=예, n=연도만): ").strip().lower() != 'n'
    country = ask_country() or 'jp'

    print(f"\n파일 {len(files)}개")

    files = normalize(files, workdir)
    if not files:
        return 1

    moves = plan_album(files, base, country, genre, full_date)
    if not moves:
        print("\n옮길 것이 없습니다.")
        return 0

    print("\n── 계획 ──")
    for m in moves:
        print(f"  {m.src.name}\n    → {m.dst}")

    if input(f"\n{len(moves)}개를 옮기고 태그를 쓸까? (y): ").strip().lower() != 'y':
        print("취소했습니다. 파일은 그대로입니다.")
        return 0

    try:
        from music.tags import apply_tags
    except ImportError:
        print("⚠️ mutagen 이 없어 태그 없이 옮깁니다. uv pip install mutagen")
        apply_tags = None

    ok = 0
    for move, err in core.apply(moves, tagger=apply_tags):
        if err:
            print(f"  ❌ {move.src.name} — {err}")
        else:
            ok += 1
    print(f"\n완료 {ok}/{len(moves)}")
    return 0


def main(path_arg=None):
    """path_arg 는 media.py 가 자기 인자를 넘겨줄 때 쓴다."""
    pathinput.enable()

    kind = pick("무엇을 할까요?", ACTIONS, lambda a: a[0])
    if kind is None:
        return 0
    kind = kind[1]

    raw = path_arg or (sys.argv[1] if len(sys.argv) > 1 else None) or ask_source()
    if not raw:
        return 1
    files = core.audio_files(raw)
    if not files:
        print("❌ 음원 파일이 없습니다.")
        return 1

    # 파일 하나를 받았을 때 _converted 를 그 파일 아래 만들 수는 없다.
    workdir = Path(raw).expanduser()
    if workdir.is_file():
        workdir = workdir.parent

    if kind == 'convert':
        return run_convert(files, workdir)
    return run_organize(files, workdir)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n중단했습니다.")
        sys.exit(130)
