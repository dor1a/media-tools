#!/usr/bin/env python3
"""iTunes 음원 정리 — 터미널.

    python3 -m music.cli [폴더]

파일을 옮기기 전에 계획을 통째로 보여준다. 목적지가 라이브러리라 되돌리기가
어렵다.
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
        # 기본값 표시는 줄 앞에 둔다. 뒤에 붙이면 `feat.` 이 달린 긴 곡명과
        # 이어져 읽혀서 제목의 일부처럼 보인다.
        print(f"{'*' if o == default else ' '}{i}. {render(o)}")
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
    """음원이 든 폴더를 고르게 한다.

    번호와 경로를 같은 자리에서 받는다. tab 보완이 켜져 있어서 목록을 띄워두면
    경로를 치는 쪽이 더 자연스럽고, 번호만 받으면 그게 오입력이 된다.
    """
    cands = core.find_candidates()
    print()
    for i, (d, n) in enumerate(cands, 1):
        print(f"  {i}. {_short_path(d)}  ({n}개)")
    if not cands:
        print("  (가까운 곳에서 음원 폴더를 못 찾았습니다)")

    hint = ', 엔터=1' if cands else ''
    while True:
        raw = input(f"\n📁 번호 또는 경로 (0=취소{hint}): ").strip().strip('"')
        if raw == '0':
            return None
        if not raw and cands:
            return str(cands[0][0])
        if raw.isdigit() and 1 <= int(raw) <= len(cands):
            return str(cands[int(raw) - 1][0])
        if raw:
            path = Path(raw).expanduser()
            if path.exists():
                return str(path)
            print(f"  그런 경로가 없습니다: {path}")
        else:
            print("  번호나 경로를 입력하세요.")


def _short_path(p):
    home = Path.home()
    try:
        return '~/' + str(p.relative_to(home))
    except ValueError:
        return str(p)


def ask_country(current=None):
    print("\n어느 나라 스토어에서 찾을까? 같은 앨범이라도 스토어마다 표기랑 발매일이 달라.")
    return pick("스토어", core.COUNTRY_CODES, str, default=current or 'jp')


def _norm(text):
    """비교용으로 문자를 줄인다 — 공백과 기호를 빼고 소문자로."""
    return ''.join(c for c in text.lower() if c.isalnum())


def best_track(tracks, filename):
    """파일명에 곡명이 들어 있으면 그 트랙. 없으면 None.

    고를 것을 없애지는 않고 기본값만 준다 — 잘못 맞췄을 때 엔터 한 번으로
    엉뚱한 트랙이 되면 안 된다.
    """
    stem = _norm(Path(filename).stem)
    hits = [t for t in tracks if t.get('trackName') and _norm(t['trackName']) in stem]
    return max(hits, key=lambda t: len(_norm(t['trackName']))) if hits else None


def choose_album(f, country, genre):
    """파일 하나로 검색해 앨범과 타입, 수록곡을 정한다.

    (album, kind, tracks, country, genre) — 스토어나 장르를 바꿨으면 바뀐 값이
    돌아온다. 한 폴더에 일본 곡과 한국 곡이 섞이는 게 보통이라, 둘 다 실행
    단위가 아니라 앨범 단위로 정해질 수 있어야 한다.
    """
    query = core.guess_query(f)
    print(f"\n🔍 검색어: {query}")

    while True:
        albums = core.search_albums(query, country)
        if albums:
            print()
            for i, a in enumerate(albums, 1):
                print(f" {i}. [{(a['releaseDate'] or '')[:10]}] {a['collectionName']}"
                      f" / {a['artistName']}  ({a.get('trackCount') or '?'}곡)")
        else:
            print(f"\n  {country} 스토어에는 없어.")
            note = core.EMPTY_SEARCH_STORES.get(country)
            if note:
                print(f"  {note}")

        raw = input(f"앨범 (0=건너뜀, s=스토어, g=장르, 지금 {country} / {genre}): ").strip().lower()
        if raw == '0':
            return None, None, None, country, genre
        if raw == 's':
            country = ask_country(country) or country
            continue
        if raw == 'g':
            genre = pick("장르 버킷", GENRES, str, default=genre) or genre
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(albums):
            album = albums[int(raw) - 1]
            break
        print("  번호나 s, g 를 입력해.")

    suggested = core.album_type(album)
    print(f"\n  타입은 {suggested or 'Various Artists 라 폴더 없이'} 로 볼게.")
    if input("  그대로 쓸까? (엔터=응, n=고르기): ").strip().lower() == 'n':
        kind = pick("타입", core.TYPES, str)
    else:
        kind = suggested

    return album, kind, core.album_tracks(album['collectionId'], country), country, genre


def plan_moves(files, base, country, genre, full_date):
    """파일마다 검색한다. 폴더가 한 앨범이면 한 번만 고르고 넘어갈 수 있다."""
    moves = []
    album = kind = tracks = None
    reuse = False
    asked_reuse = False

    for f in files:
        if not reuse:
            album, kind, tracks, country, genre = choose_album(f, country, genre)
            if album is None:
                print(f"  건너뜀: {f.name}")
                continue

        track = pick(
            f"'{f.name}' 의 트랙", tracks,
            lambda t: f"{str(t.get('trackNumber') or '?').zfill(2)} - {t['trackName']}",
            default=best_track(tracks, f.name),
        )
        if not track:
            print(f"  건너뜀: {f.name}")
            continue

        moves.append(core.Move(
            f,
            core.destination(base, genre, album, track, f.suffix,
                             full_date=full_date, type_folder=kind),
            track,
        ))

        # 한 앨범을 통째로 받은 경우가 흔하다. 그때 매번 다시 고르게 하지 않되,
        # 물어보는 것은 한 번뿐이라 섞인 폴더에서 방해가 되지 않는다.
        if not asked_reuse and len(files) > 1:
            asked_reuse = True
            reuse = input("\n나머지도 같은 앨범? (엔터=아니오, y=예): ").strip().lower() == 'y'

    return moves


def normalize(files):
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
    if input("\n변환할까? (엔터=예, n=취소): ").strip().lower() == 'n':
        return []

    # 실패한 것의 원본은 어느 쪽을 골라도 남는다. 안 그러면 아무것도 안 남는다.
    keep = input("원본은? (엔터=지움, k=.backup 으로 남김): ").strip().lower() == 'k'

    def report(src, action, why, err):
        if action == 'encode':
            print(f"  ✅ {Path(src).name}")
        elif action == 'fail':
            print(f"  ❌ {Path(src).name} — {err}")

    print()
    try:
        return convert.convert_all(files, on_each=report, keep_original=keep)
    except convert.FFmpegMissing as e:
        print(f"❌ {e}")
        return []


ACTIONS = [
    ("편입 (직접 고르기)   곡마다 앨범과 트랙을 고른다", 'organize'),
    ("편입 (자동)          첫 검색 결과로 잡고 계획에서 뺄 것만 고른다", 'auto'),
    ("변환만               mp3 320 으로 맞추기 (flac 과 이미 320 인 것은 그대로)", 'convert'),
]


def run_convert(files):
    """포맷만 맞춘다. 정리는 나중에 하고 싶을 때."""
    return 0 if normalize(files) else 1


def ask_common(default_base):
    """편입에 필요한 것들. (base, genre, full_date, country) 또는 None.

    기본값은 소스 폴더다. 라이브러리 경로는 머신마다 다르고(맥은 마운트,
    서버는 로컬), 원본 옆에 트리를 세워두면 나중에 통째로 옮기면 된다.
    """
    raw_base = input(f"\n라이브러리 루트 (엔터 시 {_short_path(default_base)}): ").strip()
    base = Path(raw_base).expanduser() if raw_base else Path(default_base)
    if not base.is_dir():
        print(f"❌ 그런 폴더가 없습니다: {base}")
        return None

    genre = pick("장르 버킷", GENRES, str)
    if not genre:
        print("장르를 골라야 경로가 정해집니다.")
        return None

    full_date = input("발매일을 YYYY.MM.DD 로 쓸까? (엔터=예, n=연도만): ").strip().lower() != 'n'
    return base, genre, full_date, ask_country() or 'jp'


def confirm_and_apply(moves):
    """계획을 보여주고, 뺄 것을 받고, 옮긴다."""
    print("\n── 계획 ──")
    for i, m in enumerate(moves, 1):
        print(f"  {i}. {m.src.name}\n     → {m.dst}")

    raw = input("\n뺄 번호 (쉼표로, 없으면 엔터): ").strip()
    if raw:
        try:
            drop = {int(x) for x in raw.replace(' ', '').split(',') if x}
        except ValueError:
            print("숫자를 쉼표로 넣으세요. 취소합니다.")
            return 0
        moves = [m for i, m in enumerate(moves, 1) if i not in drop]
    if not moves:
        print("남은 것이 없습니다.")
        return 0

    # 엔터와 y 만 진행. 다른 입력은 오타로 보고 멈춘다 — 여기서부터는 파일이
    # 실제로 움직인다.
    if input(f"\n{len(moves)}개를 옮기고 태그를 쓸까? (엔터=응, n=취소): ").strip().lower() not in ('', 'y'):
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


def run_organize(files, source):
    """앨범과 트랙을 직접 고른다."""
    common = ask_common(source)
    if not common:
        return 1
    base, genre, full_date, country = common

    print(f"\n파일 {len(files)}개")
    files = normalize(files)
    if not files:
        return 1

    moves = plan_moves(files, base, country, genre, full_date)
    if not moves:
        print("\n옮길 것이 없습니다.")
        return 0
    return confirm_and_apply(moves)


def run_auto(files, source):
    """묻지 않고 첫 검색 결과로 잡는다. 계획을 보고 뺄 것만 고른다."""
    common = ask_common(source)
    if not common:
        return 1
    base, genre, full_date, country = common

    print(f"\n파일 {len(files)}개")
    files = normalize(files)
    if not files:
        return 1

    print("\n검색 중...")
    moves, misses = [], []
    for f in files:
        hit = core.first_match(core.guess_query(f), country)
        if not hit:
            misses.append(f)
            continue
        album = core.as_album(hit)
        moves.append(core.Move(
            f, core.destination(base, genre, album, hit, f.suffix, full_date=full_date), hit))

    if misses:
        print(f"\n검색 결과 없음 {len(misses)}개 (그대로 둡니다):")
        for f in misses:
            print(f"  {f.name}")
    if not moves:
        print("\n옮길 것이 없습니다.")
        return 0
    return confirm_and_apply(moves)


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
    deep = core.audio_files(raw, recursive=True)
    if len(deep) > len(files):
        extra = len(deep) - len(files)
        print(f"\n하위 폴더에 {extra}개가 더 있습니다.")
        if input("포함할까? (엔터=이 폴더만, y=하위까지): ").strip().lower() == 'y':
            files = deep
    if not files:
        print("❌ 음원 파일이 없습니다.")
        return 1

    source = Path(raw).expanduser()
    if source.is_file():
        source = source.parent

    if kind == 'convert':
        return run_convert(files)
    if kind == 'auto':
        return run_auto(files, source)
    return run_organize(files, source)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n중단했습니다.")
        sys.exit(130)
