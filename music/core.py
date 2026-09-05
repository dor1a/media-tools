"""iTunes 조회와 음원 라이브러리 경로 규칙. UI 없음 — cli.py 가 이 위에 얹힌다."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import requests

ITUNES_BASE = "https://itunes.apple.com"
# 라이브러리에 들어가는 확장자는 mp3 와 flac 둘뿐이지만, 받아들이는 입력은
# 그보다 넓다. 나머지는 convert 가 mp3 320 으로 맞춘다.
LIBRARY_EXTS = ['.mp3', '.flac']
AUDIO_EXTS = LIBRARY_EXTS + ['.wma', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.aiff']

# 라이브러리가 쓰는 국가 스토어. 같은 앨범도 스토어마다 메타데이터가 달라서,
# J-Pop 은 jp, K-Pop 은 kr 로 물어야 원어 표기와 발매일이 맞다.
COUNTRY_CODES = ['jp', 'kr', 'us', 'gb', 'tw', 'cn', 'de', 'fr']

# Windows-illegal characters, replaced with their full-width forms. renamer 와
# 같은 표를 쓴다 — 라이브러리 전체가 한 규칙이어야 한다.
SANITIZE_MAP = {
    '/': '／', '\\': '＼', ':': '：', '*': '＊', '?': '？',
    '"': "'", '<': '〈', '>': '〉', '|': '｜',
}

TYPES = ['Albums', 'Singles & EPs', 'Compilations', 'Live Albums', 'Indies']

# 앨범명으로만 알 수 있는 것들. iTunes 는 라이브도 베스트도 따로 표시해주지
# 않아서 이름을 볼 수밖에 없고, 그래서 결과는 제안이지 결정이 아니다.
LIVE_MARKERS = ('(live)', '[live]', ' - live', 'ライブ', '라이브', 'live at ', 'live in ')
BEST_MARKERS = ('best', 'greatest hits', 'complete', 'collection', 'anthology', 'ベスト')

VARIOUS = 'various artists'


class ITunesError(RuntimeError):
    pass


# --- iTunes ----------------------------------------------------------------

def _get(path, **params):
    r = requests.get(f"{ITUNES_BASE}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json().get('results', [])


def as_album(track):
    """곡 결과에서 앨범 부분만 뽑는다. 곡 결과가 앨범 정보를 다 들고 온다."""
    return {
        'collectionId': track.get('collectionId'),
        'collectionName': track.get('collectionName', ''),
        'artistName': track.get('collectionArtistName') or track.get('artistName', ''),
        'releaseDate': track.get('releaseDate', ''),
        'trackCount': track.get('trackCount'),
        'primaryGenreName': track.get('primaryGenreName', ''),
        'artworkUrl100': track.get('artworkUrl100', ''),
    }


def search_tracks(query, country='jp', limit=25):
    """곡 검색 결과 그대로. 앨범 정보가 각 행에 함께 온다."""
    return _get('/search', term=query, media='music', entity='song',
                limit=limit, country=country)


def search_albums(query, country='jp', limit=25):
    """곡으로 검색해서 그 곡이 실린 앨범들을 추린다.

    앨범(`entity=album`)으로 바로 찾지 않는 것은 파일명이 대개 `아티스트 - 곡`
    이라서다. 곡으로 물어야 수록 앨범이 딸려 나온다.
    """
    seen = {}
    for t in search_tracks(query, country, limit):
        cid = t.get('collectionId')
        if cid and cid not in seen:
            seen[cid] = as_album(t)
    return list(seen.values())


def first_match(query, country='jp'):
    """첫 검색 결과. 자동 모드가 쓰는 것 — 고르지 않고 맨 앞을 믿는다."""
    rows = search_tracks(query, country, limit=1)
    return rows[0] if rows else None


def album_tracks(collection_id, country='jp'):
    """앨범의 수록곡. lookup 은 첫 결과로 앨범 자신을 돌려주므로 걸러낸다."""
    rows = _get('/lookup', id=collection_id, entity='song', country=country)
    return [t for t in rows if t.get('kind') == 'song' and t.get('trackName')]


# --- 경로 규칙 --------------------------------------------------------------

def sanitize(name):
    """금지문자를 전각으로 바꾼다. 글자를 버리지 않는 게 요점.

    옛 스크립트는 `isalnum()` 이 아닌 문자를 통째로 지워서 `Bird & Insect` 가
    `Bird  Insect` 가 됐다. 앨범명은 메타데이터라 손대면 안 된다.
    """
    for bad, safe in SANITIZE_MAP.items():
        name = name.replace(bad, safe)
    return name.strip()


def album_type(album):
    """타입 폴더 이름. Various Artists 면 None — 타입 폴더 없이 평면 배치한다.

    `Indies` 는 여기서 안 나온다. 외부 메타데이터에 없는 개인 분류라 사람이
    고르는 수밖에 없다.
    """
    artist = (album.get('artistName') or '').strip().lower()
    if artist == VARIOUS:
        return None

    name = (album.get('collectionName') or '').lower()
    # iTunes 는 싱글과 EP 의 제목 뒤에 실제로 ` - Single` / ` - EP` 를 붙인다.
    # 이름 중간에 든 'Single' 을 세던 옛 판정은 `Single Bed` 같은 제목을 오분류했다.
    if name.endswith(' - single') or name.endswith(' - ep'):
        return 'Singles & EPs'
    if any(m in name for m in LIVE_MARKERS):
        return 'Live Albums'
    if any(m in name for m in BEST_MARKERS):
        return 'Compilations'
    count = album.get('trackCount') or 0
    if 0 < count <= 3:
        return 'Singles & EPs'
    return 'Albums'


def release_folder(album, full_date=True):
    """`[2023.03.29] 앨범명` 또는 `[2021] 앨범명`.

    iTunes 는 발매일을 ISO 로 온전히 주는데 옛 스크립트는 연도만 쓰고 버렸다.
    """
    iso = (album.get('releaseDate') or '')[:10]
    if full_date and len(iso) == 10:
        stamp = iso.replace('-', '.')
    else:
        stamp = iso[:4] or '연도미상'
    return f"[{stamp}] {sanitize(album.get('collectionName') or '')}"


def track_file(track, ext):
    """`## - 곡명.ext` — 트랙번호는 두 자리."""
    no = str(track.get('trackNumber') or 1).zfill(2)
    return f"{no} - {sanitize(track.get('trackName') or '')}{ext}"


def destination(base, genre, album, track, ext, full_date=True, type_folder=...):
    """`<base>/<장르>/<아티스트>/<타입>/[날짜] <앨범>/## - <곡>.ext`

    장르는 부르는 쪽이 준다. iTunes 의 `primaryGenreName` 은 이 라이브러리의
    버킷(J-Pop / Utaite / Melody …)과 대응하지 않아서 자동으로 못 채운다.
    """
    kind = album_type(album) if type_folder is ... else type_folder
    parts = [str(base), sanitize(genre), sanitize(album.get('artistName') or '')]
    if kind:
        parts.append(kind)
    parts += [release_folder(album, full_date), track_file(track, ext)]
    return Path(*parts)


# --- 파일 --------------------------------------------------------------------

def audio_files(path, recursive=False):
    """기본은 그 폴더 바로 아래만. 하위까지 훑으려면 recursive.

    비켜난 원본(`*.backup`)은 확장자가 달라 저절로 빠진다.
    """
    p = Path(path).expanduser()
    if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
        return [p]
    if not p.is_dir():
        return []
    it = p.rglob('*') if recursive else p.iterdir()
    return sorted(
        f for f in it
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )


# 스테이징이 실제로 놓이는 자리들. 여기와 그 바로 아래를 훑어 고르게 한다 --
# 경로를 외워 치는 것보다 고르는 쪽이 빠르고, mkv 도구도 같은 방식이다.
SCAN_ROOTS = ['.', '~/Downloads', '~/Desktop']


def find_candidates(roots=None):
    """음원이 든 폴더 후보. [(경로, 파일수)] — 중복은 없앤다."""
    out, seen = [], set()
    for root in (roots or SCAN_ROOTS):
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for d in [base] + sorted(x for x in base.iterdir() if x.is_dir()):
            real = str(d.resolve())
            if real in seen:
                continue
            try:
                n = sum(1 for f in d.iterdir()
                        if f.is_file() and f.suffix.lower() in AUDIO_EXTS)
            except OSError:
                continue
            if n:
                seen.add(real)
                out.append((d, n))
    return out


def guess_query(path):
    """`아티스트 - 곡` 파일명에서 검색어. 구분자가 없으면 이름 통째로."""
    stem = Path(path).stem
    artist, sep, title = stem.partition(' - ')
    return f"{artist.strip()} {title.strip()}" if sep else stem.strip()


# --- 계획과 적용 --------------------------------------------------------------

@dataclass
class Move:
    src: Path
    dst: Path
    track: dict

    @property
    def dst_rel(self):
        """base 아래 상대경로 — 확인 화면에 쓰라고."""
        return str(self.dst)


def apply(moves, tagger=None):
    """계획을 실제로 반영. [(Move, None|에러문자열), ...] 반환.

    계획과 적용을 나눈 것은 목적지가 라이브러리이기 때문이다. 옮기고 나서야
    틀린 걸 알면 되돌릴 방법이 없다.
    """
    results = []
    for m in moves:
        try:
            if m.dst.exists():
                raise FileExistsError('같은 경로에 파일이 이미 있습니다')
            m.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(m.src), str(m.dst))
            if tagger:
                tagger(m.dst, m.track)
            results.append((m, None))
        except Exception as e:
            results.append((m, str(e)))
    return results
