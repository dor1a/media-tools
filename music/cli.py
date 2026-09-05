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

# 라이브러리 최상위 버킷. iTunes 의 장르로는 못 채운다 — Utaite·Melody 는
# 애플에 대응 장르가 없고, 같은 아티스트가 Anime 인지 J-Pop 인지도 사람 기준이다.
GENRES = ['J-Pop', 'K-Pop', 'C-Pop', 'Pop', 'Anime', 'Game', 'Utaite', 'Melody']


def pick(prompt, options, render, default=None):
    print()
    for i, o in enumerate(options, 1):
        mark = ' (기본)' if o == default else ''
        print(f"  {i}. {render(o)}{mark}")
    while True:
        raw = input(f"{prompt} (0=건너뜀{', 엔터=기본' if default is not None else ''}): ").strip()
        if raw == '0':
            return None
        if not raw and default is not None:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  잘못된 입력입니다.")


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


def main():
    pathinput.enable()
    raw = sys.argv[1] if len(sys.argv) > 1 else input("정리할 폴더 또는 파일: ").strip()
    files = core.audio_files(raw)
    if not files:
        print("❌ mp3 · flac 파일이 없습니다.")
        return 1

    base = Path(input("라이브러리 루트 (예: /Volumes/Media/Music): ").strip() or '.').expanduser()

    genre = pick("장르 버킷", GENRES, str)
    if not genre:
        print("장르를 골라야 경로가 정해집니다.")
        return 1

    full_date = input("발매일을 YYYY.MM.DD 로 쓸까? (엔터=예, n=연도만): ").strip().lower() != 'n'
    country = ask_country() or 'jp'

    print(f"\n파일 {len(files)}개")
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
        print("⚠️ mutagen 이 없어 태그 없이 옮깁니다. pip install mutagen")
        apply_tags = None

    ok = 0
    for move, err in core.apply(moves, tagger=apply_tags):
        if err:
            print(f"  ❌ {move.src.name} — {err}")
        else:
            ok += 1
    print(f"\n완료 {ok}/{len(moves)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
