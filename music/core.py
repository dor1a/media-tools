"""iTunes lookups and music library path rules. UI lives in cli.py."""

import os
import re
import unicodedata
import shutil
from dataclasses import dataclass
from pathlib import Path

import requests

ITUNES_BASE = "https://itunes.apple.com"
# The library stores only MP3 and FLAC, but accepts more input formats.
# The convert module converts the other formats to 320 kbps MP3.
LIBRARY_EXTS = ['.mp3', '.flac']
AUDIO_EXTS = LIBRARY_EXTS + ['.wma', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.aiff']

# Regional stores used by the library. Album names and release dates can vary
# by region; search availability is tracked separately in EMPTY_SEARCH_STORES.
COUNTRY_CODES = ['jp', 'kr', 'us', 'gb', 'tw', 'cn', 'de', 'fr']

# Replace Windows-illegal characters using the same mapping as renamer,
# keeping path rules consistent across the library.
SANITIZE_MAP = {
    '/': '／', '\\': '＼', ':': '：', '*': '＊', '?': '？',
    '"': "'", '<': '〈', '>': '〉', '|': '｜',
}

TYPES = ['Albums', 'Singles & EPs', 'Compilations', 'Live Albums', 'Indies']

# The genre tag names the style, in English and one per track. The folder
# bucket is a separate, coarser choice and is never written into the tag.
# This list is what the library and its older backup used on 2026-10-31, plus
# the Epidemic Sound styles the Hallman releases brought in on 2026-11-01.
GENRE_TAGS = [
    'Pop', 'Dance Pop', 'Ballad', 'Rock', 'Alternative', 'Indie Rock', 'Punk',
    'Heavy Metal', 'R&B', 'Hip-Hop/Rap', 'Country', 'Folk', 'Blues', 'Jazz',
    'Reggae', 'J-Pop', 'K-Pop', 'C-Pop', 'Utaite', 'Vocaloid', 'Soundtrack', 'New Age',
    'Electronic', 'Electronica', 'Dance', 'Eurodance', 'Lounge',
    'House', 'Soft House', 'Progressive House', 'Melodic House', 'Tropical House',
    'Deep House', 'Future House', 'Electro House', 'Slap House', 'Big Room House',
    'Tech House', 'Bass House', 'Electro', 'Melodic Techno', 'Future Bass', 'Glitch Hop',
    'Eurobeat',
]

# Names follow iTunes, and stores answer in their own language. The older
# `OST` and `Hip Hop/Rap` tags map to the iTunes spelling (decision
# 2026-10-31). Other non-English names are picked by hand.
GENRE_ALIASES = {
    'OST': 'Soundtrack', 'Hip Hop/Rap': 'Hip-Hop/Rap',
    'ロック': 'Rock', 'R&B／ソウル': 'R&B',
}

# Infer live and best-of releases from album titles because iTunes does not
# label them separately. These matches are suggestions for the user to review.
LIVE_MARKERS = ('(live)', '[live]', ' - live', 'ライブ', '라이브', 'live at ', 'live in ')
BEST_MARKERS = ('best', 'greatest hits', 'complete', 'collection', 'anthology', 'ベスト')

VARIOUS = 'various artists'

# Search catalogs can be empty in regions without iTunes music sales, even
# when lookup returns the same tracks. Do not mistake zero search results for
# missing tracks. On 2026-09-05, kr returned no results for `BTS`, IU's Korean
# name, or `Wheesung`. On 2026-10-31, cn returned none for `周杰倫` or
# `Taylor Swift`, while tw returned both.
EMPTY_SEARCH_STORES = {
    'kr': '한국은 iTunes 음원을 안 팔아서 검색 카탈로그가 비어 있어. jp 나 us 로 찾아봐.',
    'cn': '중국 스토어도 검색 카탈로그가 비어 있어. 중국어 곡은 tw 로 찾아봐.',
}


class ITunesError(RuntimeError):
    pass


# --- iTunes ----------------------------------------------------------------

def _get(path, **params):
    r = requests.get(f"{ITUNES_BASE}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json().get('results', [])


def as_album(track):
    """Extract album metadata carried in a track search result."""
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
    """Return raw track search results, each including album metadata."""
    return _get('/search', term=query, media='music', entity='song',
                limit=limit, country=country)


def search_albums(query, country='jp', limit=25):
    """Search for tracks and collect the albums that contain them.

    Filenames usually follow `artist - track`, so search for songs rather than
    using `entity=album` directly to find their containing albums.
    """
    seen = {}
    for t in search_tracks(query, country, limit):
        cid = t.get('collectionId')
        if cid and cid not in seen:
            seen[cid] = as_album(t)
    return list(seen.values())


def first_match(query, country='jp'):
    """Return the first search result for automatic mode without manual selection."""
    rows = search_tracks(query, country, limit=1)
    return rows[0] if rows else None


def album_tracks(collection_id, country='jp'):
    """Return album tracks, filtering out the album record returned by lookup."""
    rows = _get('/lookup', id=collection_id, entity='song', country=country)
    return [t for t in rows if t.get('kind') == 'song' and t.get('trackName')]


# --- Path rules --------------------------------------------------------------

def sanitize(name):
    """Replace forbidden characters with safe equivalents instead of dropping them.

    The old script removed every non-alphanumeric character, turning
    `Bird & Insect` into `Bird  Insect`. Preserve album title metadata.
    """
    for bad, safe in SANITIZE_MAP.items():
        name = name.replace(bad, safe)
    return name.strip()


TRAILING_DOTS = re.compile(r'\.+$')


def folder_name(name):
    """Sanitize a folder name and turn its trailing dots into full-width dots.

    Windows does not support folder names that end in a period. The library
    writes `Summer．` and had no folder ending in `.` on 2026-10-31. Filenames
    keep their dots because the extension follows them (`L.O.V.E..mp3`).
    """
    return TRAILING_DOTS.sub(lambda m: '．' * len(m.group()), sanitize(name))


def genre_tag(name):
    """Return the English genre tag for a store genre, or None to pick one instead."""
    name = (name or '').strip()
    name = GENRE_ALIASES.get(name, name)
    return name if name and name.isascii() else None


def album_type(album):
    """Return the type folder, or None for a flat Various Artists layout.

    `Indies` is a personal category absent from external metadata, so it must
    be selected manually and is never inferred here.
    """
    artist = (album.get('artistName') or '').strip().lower()
    if artist == VARIOUS:
        return None

    name = (album.get('collectionName') or '').lower()
    # iTunes appends ` - Single` or ` - EP` to release titles. Matching 'Single'
    # anywhere in a title previously misclassified names such as `Single Bed`.
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
    """Build `[2023.03.29] Album title` or `[2021] Album title`.

    iTunes provides a full ISO release date; the old script kept only the year.
    """
    iso = (album.get('releaseDate') or '')[:10]
    if full_date and len(iso) == 10:
        stamp = iso.replace('-', '.')
    else:
        stamp = iso[:4] or '연도미상'
    return f"[{stamp}] {folder_name(album.get('collectionName') or '')}"


# Match trailing featured-artist credits. Exclude `ft.` because library titles
# use it inside remix credits (`… Neetz ft. Ryohu … Remix`), where wrapping the
# rest of the title would produce incorrect parentheses.
FEAT_RE = re.compile(r'\s+(feat\.|featuring)\s+(.+)$', re.I)
WRAPPED_RE = re.compile(r'[(\[]\s*(feat\.|ft\.|featuring)', re.I)


def wrap_feat(name):
    """Turn `Title feat. X` into `Title (feat. X)`; leave wrapped credits alone.

    iTunes supplies unwrapped credits, while the library uses parentheses.
    On 2026-09-05, 159 of 170 featured-artist credits used `(feat.` and none
    were unwrapped. Apple also uses `[feat. …]` when a title already contains
    parentheses; those credits are already wrapped and pass through.
    """
    if WRAPPED_RE.search(name):
        return name
    m = FEAT_RE.search(name)
    if not m:
        return name
    return f"{name[:m.start()].rstrip()} ({m.group(1)} {m.group(2).strip()})"


def track_file(track, ext):
    """Build `## - Track title.ext` with a two-digit track number."""
    no = str(track.get('trackNumber') or 1).zfill(2)
    return f"{no} - {sanitize(wrap_feat(track.get('trackName') or ''))}{ext}"


def artist_folder(album, source=None):
    """Choose the artist folder, preferring filenames that follow `artist - track`.

    Stores can change artist spelling: searching jp when kr is empty returns
    `Wheesung` instead of the Korean name. The library uses native spelling
    (all K-Pop artist folders use Hangul), already present in source filenames.

    Compilations are an exception: filenames identify track artists rather
    than the album artist.
    """
    itunes = (album.get('artistName') or '').strip()
    if itunes.lower() == VARIOUS:
        return itunes
    if source:
        head, sep, _ = stem_nfc(source).partition(' - ')
        if sep and head.strip():
            return head.strip()
    return itunes


def destination(base, genre, album, track, ext, full_date=True, type_folder=..., source=None):
    """Build `<base>/<genre>/<artist>/<type>/[date] <album>/## - <track>.ext`.

    The caller supplies the genre. iTunes `primaryGenreName` cannot be mapped
    automatically to the library's categories (J-Pop / Utaite / Melody …).
    """
    kind = album_type(album) if type_folder is ... else type_folder
    parts = [str(base), folder_name(genre), folder_name(artist_folder(album, source))]
    if kind:
        parts.append(kind)
    parts += [release_folder(album, full_date), track_file(track, ext)]
    return Path(*parts)


# --- Files --------------------------------------------------------------------

def audio_files(path, recursive=False):
    """List direct children by default; use recursive to include subdirectories.

    Retained originals (`*.backup`) are excluded by their extension.
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


# Common staging locations. Scan these and their immediate subdirectories so
# users can pick a folder instead of typing a path, as in the MKV tool.
SCAN_ROOTS = ['.', '~/Downloads', '~/Desktop']


def find_candidates(roots=None):
    """Return deduplicated audio folder candidates as [(path, file count)]."""
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


def stem_nfc(path):
    """Normalize the filename stem to NFC for metadata comparisons.

    Filenames can contain decomposed Hangul syllables or voiced kana. Without
    normalization, visually identical names may not match iTunes NFC strings.
    """
    return unicodedata.normalize('NFC', Path(path).stem)


def guess_query(path):
    """Build a query from `artist - track`, or use the whole stem without a separator."""
    stem = stem_nfc(path)
    artist, sep, title = stem.partition(' - ')
    return f"{artist.strip()} {title.strip()}" if sep else stem.strip()


# --- Planning and application ------------------------------------------------

@dataclass
class Move:
    src: Path
    dst: Path
    track: dict
    artist: str = ''
    genre: str = ''  # English genre tag; the folder bucket is already in dst


    @property
    def dst_rel(self):
        """Return the destination as text for the confirmation display."""
        return str(self.dst)


def apply(moves, tagger=None):
    """Apply planned moves and return [(Move, None|error message), ...].

    Planning is separate because the destination is the library. Review moves
    before applying them; discovering mistakes afterward offers no automatic undo.
    """
    results = []
    for m in moves:
        try:
            if m.dst.exists():
                raise FileExistsError('같은 경로에 파일이 이미 있습니다')
            m.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(m.src), str(m.dst))
            if tagger:
                tagger(m.dst, m.track, artist=m.artist, genre=m.genre)
            results.append((m, None))
        except Exception as e:
            results.append((m, str(e)))
    return results
