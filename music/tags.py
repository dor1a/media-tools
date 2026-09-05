"""파일에 태그와 커버를 쓴다. mp3 는 ID3v2.4 + APEv2, flac 은 Vorbis comment.

Pillow 를 쓰지 않는다. FLAC 그림 블록이 원하는 건 가로와 세로 두 값뿐이고,
그건 JPEG 헤더에서 바로 읽을 수 있다.
"""

from pathlib import Path

import requests
from mutagen.apev2 import APEv2, error as APEv2Error
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK

# iTunes 아트워크는 URL 의 크기 부분을 바꾸면 원본에 가까운 것을 준다.
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
    """JPEG SOF 마커에서 (width, height). 못 읽으면 (0, 0)."""
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
    """태그에 쓸 값. artist 와 genre 는 폴더에 쓴 값으로 덮는다.

    둘 다 검색 결과를 그대로 두면 폴더와 태그가 어긋난다 — 폴더는 `휘성` 인데
    태그는 `Wheesung`, 폴더는 `K-Pop` 인데 태그는 `R&B／ソウル` 이 된다.
    """
    total = track.get('trackCount') or (album or {}).get('trackCount') or ''
    num = track.get('trackNumber', 1)
    name = artist or track.get('artistName', '')
    return {
        'title': track.get('trackName', ''),
        'album': track.get('collectionName', ''),
        'artist': name,
        'albumartist': artist or (album or {}).get('artistName') or track.get('artistName', ''),
        'track': f"{num}/{total}" if total else str(num),
        'disc': str(track.get('discNumber', 1)),
        'multidisc': (track.get('discCount') or 1) > 1,
        'date': (track.get('releaseDate') or '')[:4],
        'genre': genre or track.get('primaryGenreName', ''),
    }


def _write_mp3(path, f, cover):
    # 새 ID3 객체를 저장하면 기존 태그 블록이 통째로 교체된다. 남은 프레임을
    # 따로 지울 필요가 없다.
    tag = ID3()
    tag['TIT2'] = TIT2(encoding=3, text=f['title'])
    tag['TALB'] = TALB(encoding=3, text=f['album'])
    tag['TPE1'] = TPE1(encoding=3, text=f['artist'])
    tag['TPE2'] = TPE2(encoding=3, text=f['albumartist'])
    tag['TRCK'] = TRCK(encoding=3, text=f['track'])
    tag['TCON'] = TCON(encoding=3, text=f['genre'])
    tag['TDRC'] = TDRC(encoding=3, text=f['date'])
    if f['multidisc']:
        tag['TPOS'] = TPOS(encoding=3, text=f['disc'])
    if cover:
        tag['APIC'] = APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover)
    tag.save(path, v2_version=4)

    # 일부 플레이어가 APEv2 만 읽는다. 실패해도 ID3v2 는 이미 쓰였으므로 넘어간다.
    try:
        try:
            ape = APEv2(path)
            ape.clear()
        except APEv2Error:
            ape = APEv2()
        ape['Title'], ape['Album'] = f['title'], f['album']
        ape['Artist'], ape['AlbumArtist'] = f['artist'], f['albumartist']
        ape['Track'], ape['Disc'] = f['track'], f['disc']
        ape['Genre'], ape['Year'] = f['genre'], f['date']
        ape.save(path)
    except Exception as e:
        print(f"  APEv2 건너뜀: {Path(path).name} — {e}")

    # ID3v1 을 맨 마지막에 쓴다. 정의상 파일의 마지막 128바이트라, APE 를 뒤에
    # 붙이면 그 자리에서 밀려나 없는 것과 같아진다. 라이브러리의 mp3 는 v1 과
    # v2.4 와 APE 를 다 갖고 있다.
    tag.save(path, v2_version=4, v1=2)


def _write_flac(path, f, cover):
    audio = FLAC(path)
    audio['title'], audio['album'] = f['title'], f['album']
    audio['artist'], audio['albumartist'] = f['artist'], f['albumartist']
    audio['tracknumber'], audio['date'] = f['track'], f['date']
    audio['genre'] = f['genre']
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
    """확장자에 맞는 포맷으로 태그를 쓴다. 실패는 예외로 올린다."""
    path = Path(path)
    f = _fields(track, album, artist, genre)
    cover = _artwork(track, album)
    if path.suffix.lower() == '.mp3':
        _write_mp3(path, f, cover)
    elif path.suffix.lower() == '.flac':
        _write_flac(path, f, cover)
