"""Write tags and covers: ID3v2.4 + APEv2 for MP3, Vorbis comments for FLAC.

Pillow is unnecessary here. The width and height needed for the FLAC picture
block can be read directly from the JPEG header.
"""

from pathlib import Path

import requests
from mutagen.apev2 import APEv2, error as APEv2Error
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK

from music import core

# Request artwork closer to the original resolution by changing the URL size.
ARTWORK_SIZE = '10000x10000'


def _artwork(track, album=None):
    url = (album or {}).get('artworkUrl100') or track.get('artworkUrl100') or ''
    if not url:
        return None
    if 'mzstatic.com' in url:
        url = url.replace('100x100', ARTWORK_SIZE)
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def _jpeg_size(data):
    """Read (width, height) from a JPEG SOF marker, or return (0, 0)."""
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h = int.from_bytes(data[i + 5:i + 7], 'big')
            w = int.from_bytes(data[i + 7:i + 9], 'big')
            return w, h
        i += 2 + int.from_bytes(data[i + 2:i + 4], 'big')
    return 0, 0


def _fields(track, album=None, artist=None, genre=None):
    """Build tag values, overriding the artist with the folder value.

    Raw search results can disagree with the folder: a Korean artist name may
    become `Wheesung`. The genre is the English style chosen for the album, not
    the folder bucket. Without one, the store's genre is used once `genre_tag`
    has mapped it to English, and a name it cannot map leaves the tag empty.

    Titles also pass through `wrap_feat` to match filenames from `track_file`.
    Otherwise a filename containing `(feat. Noa)` would have `feat. Noa` in
    `TIT2`. All 161 library tracks with `(feat.` in their filenames also had
    parentheses in `TIT2` when checked on 2026-09-11.

    Multi-disc positions are `n/total`, like the track number. The library
    stores them as `1/2` in both `TPOS` and APE `DISCNUMBER`, and single-disc
    albums have no disc number (all MP3s checked on 2026-10-25).
    """
    total = track.get('trackCount') or (album or {}).get('trackCount') or ''
    num = track.get('trackNumber', 1)
    discs = track.get('discCount') or 1
    name = artist or track.get('artistName', '')
    return {
        'title': core.wrap_feat(track.get('trackName', '')),
        'album': track.get('collectionName', ''),
        'artist': name,
        'albumartist': artist or (album or {}).get('artistName') or track.get('artistName', ''),
        'track': f"{num}/{total}" if total else str(num),
        'disc': f"{track.get('discNumber') or 1}/{discs}",
        'multidisc': discs > 1,
        'date': (track.get('releaseDate') or '')[:4],
        'genre': genre or core.genre_tag(track.get('primaryGenreName')) or '',
    }


def _write_mp3(path, f, cover):
    # Saving a new ID3 object replaces the existing tag block entirely.
    # There is no need to delete leftover frames separately.
    tag = ID3()
    tag['TIT2'] = TIT2(encoding=3, text=f['title'])
    tag['TALB'] = TALB(encoding=3, text=f['album'])
    tag['TPE1'] = TPE1(encoding=3, text=f['artist'])
    tag['TPE2'] = TPE2(encoding=3, text=f['albumartist'])
    tag['TRCK'] = TRCK(encoding=3, text=f['track'])
    if f['genre']:
        tag['TCON'] = TCON(encoding=3, text=f['genre'])
    tag['TDRC'] = TDRC(encoding=3, text=f['date'])
    if f['multidisc']:
        tag['TPOS'] = TPOS(encoding=3, text=f['disc'])
    if cover:
        tag['APIC'] = APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover)

    # Do not write ID3v1 (decision: 2026-09-12). We always write ID3v2.4;
    # ID3v2.4 carries the Unicode text required by this library.
    # Mutagen writes v1 as Latin-1, replacing Korean text with question marks.
    # That is worse than the library's existing CP949 tags written by mp3tag.
    # `v1=0` also removes any ID3v1 tag left in the source.
    tag.save(path, v2_version=4, v1=0)

    # Some players only read APEv2. Ignore APE failures after ID3v2 is saved.
    #
    # Match the casing of the library's mp3tag keys. Mutagen treats APE keys
    # case-insensitively, but stored spelling should still match the library.
    # The album artist key is `Album Artist`, including the space.
    #
    # The cover goes in APIC only (decision: 2026-11-01). APE sits at the end
    # of the file and ffmpeg counts it as audio. Once it passes 1/16 of the
    # audio bytes, ffmpeg drops the Info frame count and guesses the length
    # from the bitrate. `clear()` also removes a cover mp3tag left here.
    try:
        try:
            ape = APEv2(path)
            ape.clear()
        except APEv2Error:
            ape = APEv2()
        ape['TITLE'], ape['ARTIST'] = f['title'], f['artist']
        ape['ALBUM'], ape['YEAR'] = f['album'], f['date']
        ape['TRACK'] = f['track']
        if f['genre']:
            ape['GENRE'] = f['genre']
        ape['Album Artist'] = f['albumartist']
        if f['multidisc']:
            ape['DISCNUMBER'] = f['disc']
        ape.save(path)
    except Exception as e:
        print(f"  APEv2 건너뜀: {Path(path).name} — {e}")


def _write_flac(path, f, cover):
    audio = FLAC(path)
    audio['title'], audio['album'] = f['title'], f['album']
    audio['artist'], audio['albumartist'] = f['artist'], f['albumartist']
    audio['tracknumber'], audio['date'] = f['track'], f['date']
    if f['genre']:
        audio['genre'] = f['genre']
    elif 'genre' in audio:
        del audio['genre']
    if f['multidisc']:
        audio['discnumber'] = f['disc']
    if cover:
        pic = Picture()
        pic.data, pic.type, pic.mime, pic.desc = cover, 3, 'image/jpeg', 'Cover'
        pic.width, pic.height = _jpeg_size(cover)
        pic.depth = 24
        audio.clear_pictures()
        audio.add_picture(pic)
    audio.save()


def apply_tags(path, track, album=None, artist=None, genre=None):
    """Write tags in the format for the extension; propagate failures."""
    path = Path(path)
    f = _fields(track, album, artist, genre)
    cover = _artwork(track, album)
    if path.suffix.lower() == '.mp3':
        _write_mp3(path, f, cover)
    elif path.suffix.lower() == '.flac':
        _write_flac(path, f, cover)
