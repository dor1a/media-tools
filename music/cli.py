#!/usr/bin/env python3
"""Terminal interface for organizing music with iTunes metadata.

    python3 -m music.cli [folder]

Show the complete plan before moving files. Changes to the destination library
are difficult to undo.
"""

import os
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathinput
from music import core

# Top-level library categories cannot be inferred from iTunes genres. Apple
# has no Utaite or Melody equivalent, and Anime vs. J-Pop is a personal choice.
GENRES = ['J-Pop', 'K-Pop', 'C-Pop', 'Pop', 'EDM', 'Anime', 'Game', 'Utaite', 'Melody']


def _width(text):
    """Return terminal cell width, counting wide Hangul and CJK characters as two."""
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def _pad(text, width):
    """Pad to display width. ljust counts characters and misaligns columns
    containing wide characters such as Hangul."""
    return text + ' ' * max(1, width - _width(text))


def rule(label=''):
    """Draw a separator between steps.

    Consecutive prompts otherwise make completed steps hard to distinguish.
    """
    total = 56
    if label:
        head = f"── {label} "
        print(f"\n{head}" + "─" * max(3, total - _width(head)))
    else:
        print("\n" + "─" * total)


def pick(prompt, options, render, default=None, zero='건너뜀'):
    print()
    for i, o in enumerate(options, 1):
        # Put the default marker first. A trailing marker can look like part
        # of a long track title containing `feat.` credits.
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
    """Ask the user to select a folder containing audio.

    Accept both numbers and paths at the same prompt. With tab completion
    enabled, typing a path is natural and should not be rejected as invalid.
    """
    cands = core.find_candidates()
    rule('작업 폴더')
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
    rule('스토어')
    print("어느 나라 스토어에서 찾을까? 같은 앨범이라도 스토어마다 표기랑 발매일이 달라.")
    return pick("스토어", core.COUNTRY_CODES, str, default=current or 'jp')


TYPE_IN = '직접 입력'


def pick_style(default=None):
    """Pick an English genre tag from the list, or type one in English."""
    choice = pick("장르 태그", core.GENRE_TAGS + [TYPE_IN], str, default=default)
    while choice == TYPE_IN:
        raw = input("  장르 태그 (영어로): ").strip()
        if raw and raw.isascii():
            return raw
        print("  장르 태그는 영어로만 넣어.")
    return choice


def ask_style(album):
    """Offer the store's genre in English as the album's tag, or pick another."""
    store = album.get('primaryGenreName') or ''
    suggested = core.genre_tag(store)
    if not suggested:
        print(f"\n  스토어 장르 '{store or '없음'}' 는 영어로 못 바꿔서 골라야 해.")
        return pick_style()
    print(f"\n  장르 태그는 {suggested} 로 볼게 (스토어: {store}).")
    if input("  그대로 쓸까? (엔터=응, n=고르기): ").strip().lower() == 'n':
        return pick_style(suggested)
    return suggested


def _norm(text):
    """Normalize to NFC, lowercase, and remove spaces and symbols for comparison.

    Apply NFC first: filenames can contain decomposed Hangul syllables or
    voiced kana, making visually identical names compare differently.
    """
    text = unicodedata.normalize('NFC', text)
    return ''.join(c for c in text.lower() if c.isalnum())


def best_track(tracks, filename):
    """Return the track whose title best matches the filename, or None.

    Supply a suggested default while retaining manual selection so the user
    can review the match before accepting it.
    """
    stem = _norm(core.stem_nfc(filename))
    hits = [t for t in tracks if t.get('trackName') and _norm(t['trackName']) in stem]
    return max(hits, key=lambda t: len(_norm(t['trackName']))) if hits else None


def choose_album(f, country, genre):
    """Search using one file and select its album, type, and track list.

    Return (album, kind, tracks, country, genre, style), including any store or
    bucket changes. Folders commonly mix Japanese and Korean songs, so both
    settings must be adjustable per album rather than only per run. `style` is
    the album's English genre tag, chosen apart from the folder bucket.
    """
    query = core.guess_query(f)
    rule(f'🔍 {query}')

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
            return None, None, None, country, genre, None
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

    style = ask_style(album)
    return album, kind, core.album_tracks(album['collectionId'], country), country, genre, style


def plan_moves(files, base, country, genre, full_date):
    """Search per file, with an option to reuse one album for the whole folder."""
    moves = []
    album = kind = tracks = style = None
    reuse = False
    asked_reuse = False

    for f in files:
        if not reuse:
            album, kind, tracks, country, genre, style = choose_album(f, country, genre)
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
                             full_date=full_date, type_folder=kind, source=f),
            track,
            core.artist_folder(album, f),
            style,
        ))

        # Whole-album downloads are common. Offer reuse once to avoid repeated
        # selection without repeatedly interrupting folders containing mixed albums.
        if not asked_reuse and len(files) > 1:
            asked_reuse = True
            reuse = input("\n나머지도 같은 앨범? (엔터=아니오, y=예): ").strip().lower() == 'y'

    return moves


def normalize(files):
    """Preview changes, then normalize to library formats: 320 kbps MP3 or FLAC."""
    from music import convert

    plans = [(f, *convert.verdict(f)) for f in files]
    todo = [p for p in plans if p[1] == 'encode']
    keep = [p for p in plans if p[1] == 'keep']

    if keep:
        rule(f'그대로 두는 것 {len(keep)}개')
        for f, _, why in keep:
            print(f"  {_pad(why, 20)}{f.name}")
    if not todo:
        print("\n변환할 것이 없습니다.")
        return files

    rule(f'변환 {len(todo)}개')
    for f, _, why in todo:
        print(f"  {_pad(why, 20)}{f.name}")
    if input("\n변환할까? (엔터=예, n=취소): ").strip().lower() == 'n':
        return []

    # Failed conversions retain their sources regardless of this choice.
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


def describe(files):
    """Display each file's current format. Conversion has a separate menu action."""
    from music import convert

    rule(f'파일 {len(files)}개')
    off = 0
    for f in files:
        label, ok = convert.state(f)
        if not ok:
            off += 1
        # Align status first and keep one line per file to fit more on screen.
        # Mark mismatches with `!` in the same position as the default `*` marker.
        print(f"{'!' if not ok else ' '} {_pad(label, 12)}{f.name}")
    if off:
        print(f"\n  {off}개가 라이브러리 포맷이 아니야. 바꾸려면 메뉴의 '변환만' 을 먼저 돌려.")


def run_convert(files):
    """Convert formats only, leaving organization for later."""
    return 0 if normalize(files) else 1


def ask_common(default_base):
    """Ask for settings before listing files. Return (base, full_date) or None.

    Default to the source folder. Library paths vary by machine (mounted on
    Mac, local on the server); a tree built beside the originals can be moved
    into the library later.

    Ask for genre and store after showing the file list.
    """
    rule('설정')
    raw_base = input(f"라이브러리 루트 (엔터 시 {_short_path(default_base)}): ").strip()
    base = Path(raw_base).expanduser() if raw_base else Path(default_base)
    if not base.is_dir():
        print(f"❌ 그런 폴더가 없습니다: {base}")
        return None

    full_date = input("발매일을 YYYY.MM.DD 로 쓸까? (엔터=예, n=연도만): ").strip().lower() != 'n'
    return base, full_date


def confirm_and_apply(moves):
    """Show the plan, accept exclusions, and move the remaining files."""
    rule('계획')
    for i, m in enumerate(moves, 1):
        print(f"  {i}. {m.src.name}\n     → {m.dst}  [장르 태그: {m.genre or '없음'}]")

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

    # Proceed only on Enter or y. Stop on other input because files will
    # actually move from this point onward.
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
    """Organize files with manual album and track selection."""
    common = ask_common(source)
    if not common:
        return 1
    base, full_date = common

    describe(files)
    rule('장르')
    genre = pick("장르 버킷", GENRES, str)
    if not genre:
        print("장르를 골라야 경로가 정해집니다.")
        return 1
    country = ask_country() or 'jp'

    moves = plan_moves(files, base, country, genre, full_date)
    if not moves:
        print("\n옮길 것이 없습니다.")
        return 0
    return confirm_and_apply(moves)


def run_auto(files, source):
    """Use first search results, then let the user exclude entries from the plan."""
    common = ask_common(source)
    if not common:
        return 1
    base, full_date = common

    describe(files)
    rule('장르')
    genre = pick("장르 버킷", GENRES, str)
    if not genre:
        print("장르를 골라야 경로가 정해집니다.")
        return 1
    country = ask_country() or 'jp'

    print("\n검색 중...")
    moves, misses = [], []
    for f in files:
        hit = core.first_match(core.guess_query(f), country)
        if not hit:
            misses.append(f)
            continue
        album = core.as_album(hit)
        moves.append(core.Move(
            f, core.destination(base, genre, album, hit, f.suffix,
                               full_date=full_date, source=f), hit,
            core.artist_folder(album, f), core.genre_tag(hit.get('primaryGenreName'))))

    if misses:
        print(f"\n검색 결과 없음 {len(misses)}개 (그대로 둡니다):")
        for f in misses:
            print(f"  {f.name}")
    if not moves:
        print("\n옮길 것이 없습니다.")
        return 0
    return confirm_and_apply(moves)


def main(path_arg=None):
    """Accept path_arg when media.py forwards its own command-line argument."""
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
