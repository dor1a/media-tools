"""Metadata selection and filename rules shared by the CLI and GUI."""

import os
import re
import stat
import unicodedata
from dataclasses import dataclass

import requests
from renamer import tvdb

TMDB_BASE_URL = "https://api.themoviedb.org/3"
SUBTITLE_EXTS = ['.srt', '.smi', '.ass']
MEDIA_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.ts', '.m2ts', '.webm', *SUBTITLE_EXTS}
PROVIDERS = ('TVDB', 'TMDB')

# Windows-illegal characters, replaced with their full-width forms so the
# names survive on any filesystem and still read the same.
SANITIZE_MAP = {
    '/': '／', '\\': '＼', ':': '：', '*': '＊', '?': '？',
    '"': "'", '<': '〈', '>': '〉', '|': '｜',
}


class MetadataError(RuntimeError):
    pass


class TmdbError(MetadataError):
    pass


class RenameError(RuntimeError):
    pass


_tvdb_client = None


def tvdb_client():
    global _tvdb_client
    if _tvdb_client is None:
        _load_dotenv()
        key = os.environ.get('TVDB_API_KEY')
        if not key:
            raise MetadataError('TVDB_API_KEY가 없습니다. .env에 키를 설정하세요.')
        _tvdb_client = tvdb.Client(key, os.environ.get('TVDB_PIN'))
    return _tvdb_client


def _tvdb_call(method, *args):
    try:
        return getattr(tvdb_client(), method)(*args)
    except tvdb.TvdbError as error:
        raise MetadataError(str(error)) from None


def _provider(provider):
    if provider not in PROVIDERS:
        raise MetadataError('메타데이터 제공자를 선택하세요.')


def _load_dotenv():
    """Load the repo root's .env into the environment, keeping existing values."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_key():
    k = os.environ.get('TMDB_API_KEY')
    if not k:
        _load_dotenv()
        k = os.environ.get('TMDB_API_KEY')
    if not k:
        raise TmdbError("TMDB_API_KEY 가 없습니다. .env.example 를 .env 로 복사하고 키를 넣으세요.")
    return k


def _get(path, **params):
    params['api_key'] = api_key()
    try:
        r = requests.get(f"{TMDB_BASE_URL}{path}", params=params, timeout=10, allow_redirects=False)
    except requests.RequestException:
        raise TmdbError('TMDB 연결에 실패했습니다. 네트워크 상태를 확인하세요.') from None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise TmdbError(f'TMDB 조회 실패 (HTTP {r.status_code}).')
    try:
        return r.json()
    except ValueError:
        raise TmdbError('TMDB 응답을 해석할 수 없습니다.') from None


# --- TMDB ------------------------------------------------------------------

def search_series(query, provider='TVDB'):
    """[{id, name, year, original_language}, ...]"""
    _provider(provider)
    if provider == 'TVDB':
        return _tvdb_call('search_series', query)
    data = _get('/search/tv', query=query) or {}
    return [
        {
            'id': s['id'],
            'name': s['name'],
            'year': (s.get('first_air_date') or '')[:4],
            'original_language': s.get('original_language', 'en'),
        }
        for s in data.get('results', [])
    ]


def list_seasons(series_id, provider='TVDB', lang='eng'):
    """Return [{season_number, episode_count, air_date}, ...], including season 0."""
    _provider(provider)
    if provider == 'TVDB':
        return _tvdb_call('list_seasons', series_id, lang)
    data = _get(f'/tv/{series_id}') or {}
    return [
        {
            'season_number': s['season_number'],
            'episode_count': s.get('episode_count'),
            'air_date': s.get('air_date'),
        }
        for s in data.get('seasons', [])
    ]


def episode_map(series_id, season_number, lang='eng', provider='TVDB', numbering='season'):
    """Return {episode_number: {air_date, title}}, falling back to English."""
    _provider(provider)
    if provider == 'TVDB':
        return _tvdb_call('episode_map', series_id, season_number, lang, numbering)
    if numbering != 'season':
        raise MetadataError('TMDB는 시즌별 회차만 지원합니다.')
    if lang == 'eng':
        lang = 'en'
    data = _get(f'/tv/{series_id}/season/{season_number}', language=lang)
    if not data or not data.get('episodes'):
        data = _get(f'/tv/{series_id}/season/{season_number}', language='en')
    if not data:
        return {}
    return {
        ep['episode_number']: {
            'air_date': ep.get('air_date') or '',
            'title': ep.get('name') or '',
        }
        for ep in data.get('episodes', [])
    }


# --- Filenames -------------------------------------------------------------

def sanitize(name):
    name = unicodedata.normalize('NFC', name)
    for bad, safe in SANITIZE_MAP.items():
        name = name.replace(bad, safe)
    return name


def episode_code(filename):
    """Extract (season, episode) from a filename, or return None."""
    m = re.search(r'(?<![A-Za-z0-9])S([0-9]+)E([0-9]+)(?![A-Za-z0-9]|[-_]E?[0-9])', filename, re.IGNORECASE)
    return (int(m.group(1)), int(m.group(2))) if m else None


def build_name(title, season, episode, air_date, ep_title, ext):
    """Build [air_date] original_title - S01E01 - episode_title.ext; add .kor for subtitles."""
    title = sanitize(title)
    if ext.lower() in SUBTITLE_EXTS:
        ext = f'.kor{ext}'
    return f"[{air_date}] {title} - S{season:02}E{episode:02} - {sanitize(ep_title)}{ext}"


# --- Season discovery ------------------------------------------------------

def existing_path(path):
    """Try the directory entry spelling before an NFC fallback for SMB."""
    try:
        os.lstat(path)
        return path
    except FileNotFoundError:
        normalized = unicodedata.normalize('NFC', path)
        if normalized == path:
            raise
        os.lstat(normalized)
        return normalized


def detect_seasons(base_dir):
    """Infer seasons from folders. Return [(relative_path, season_number), ...].

    'Specials' is 0 and 'Season N' is N; otherwise inspect S##E## in filenames.
    Include '.' when base_dir itself contains episode files.
    """
    base_dir = existing_path(base_dir)
    found = []
    for entry in sorted(os.listdir(base_dir)):
        full = existing_path(os.path.join(base_dir, entry))
        if not os.path.isdir(full):
            continue
        if re.fullmatch(r'(?i)specials', entry):
            found.append((entry, 0))
            continue
        m = re.search(r'Season[ _]?(\d+)', entry, re.IGNORECASE)
        if m:
            found.append((entry, int(m.group(1))))
            continue
        numbers = {episode_code(f)[0] for f in os.listdir(full)
                   if os.path.splitext(f)[1].lower() in MEDIA_EXTS and episode_code(f)
                   and os.path.isfile(existing_path(os.path.join(full, f)))}
        found.extend((entry, number) for number in sorted(numbers))

    base_seasons = {episode_code(f)[0] for f in os.listdir(base_dir)
                    if os.path.splitext(f)[1].lower() in MEDIA_EXTS and episode_code(f)
                    and os.path.isfile(existing_path(os.path.join(base_dir, f)))}
    found.extend(('.', number) for number in sorted(base_seasons))

    return found


# --- Rename plans ----------------------------------------------------------

@dataclass
class Rename:
    old: str
    new: str
    signature: tuple | None = None

    @property
    def old_name(self):
        return os.path.basename(self.old)

    @property
    def new_name(self):
        return os.path.basename(self.new)


def plan(directory, title, season_number, episodes):
    """Plan renames for one season in a directory without changing files."""
    directory = existing_path(directory)
    out = []
    for fname in sorted(os.listdir(directory)):
        ext = os.path.splitext(fname)[1]
        if ext.lower() not in MEDIA_EXTS:
            continue
        code = episode_code(fname)
        if not code:
            if re.search(r'S[0-9]+E', fname, re.IGNORECASE):
                raise RenameError(f'지원하지 않는 회차 표기입니다: {fname}')
            continue
        season, ep_num = code
        if season != season_number:
            continue
        if ep_num not in episodes:
            raise RenameError(f'회차 정보를 찾지 못했습니다: {fname}. 제공자와 회차 기준을 확인하세요.')
        info = episodes[ep_num]
        if not info.get('title') or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', info.get('air_date') or ''):
            raise RenameError(f'제목 또는 방영일이 없는 회차입니다: {fname}')
        new = build_name(title, season_number, ep_num, info['air_date'], info['title'], ext)
        old_path = existing_path(os.path.join(directory, fname))
        new_path = os.path.join(directory, new)
        if unicodedata.normalize('NFC', old_path) == unicodedata.normalize('NFC', new_path):
            new_path = old_path
        out.append(Rename(old_path, new_path, file_signature(old_path)))
    return out


def file_signature(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise RenameError(f'일반 파일이 아닙니다: {path}')
    return (info.st_dev, info.st_ino, info.st_size, info.st_uid, info.st_gid,
            stat.S_IMODE(info.st_mode), info.st_mtime_ns)


def check_plan(renames):
    """Reject collisions and changed sources before the first rename."""
    sources, destinations = set(), set()
    for rename in renames:
        source = unicodedata.normalize('NFC', os.path.abspath(rename.old))
        destination = unicodedata.normalize('NFC', os.path.abspath(rename.new))
        if source in sources or destination in destinations:
            raise RenameError('rename 계획에 중복 원본 또는 목적지가 있습니다.')
        sources.add(source)
        destinations.add(destination)
        signature = file_signature(rename.old)
        if rename.signature is not None and signature != rename.signature:
            raise RenameError(f'미리보기 이후 원본이 바뀌었습니다: {rename.old_name}')
        if os.path.lexists(rename.new) and os.path.abspath(rename.old) != os.path.abspath(rename.new):
            raise RenameError(f'목적지 파일이 이미 있습니다: {rename.new_name}')


def apply(renames):
    """Apply the plan and return [(Rename, None|error_message), ...]."""
    results = []
    try:
        check_plan(renames)
    except (OSError, RenameError) as error:
        return [(rename, str(error)) for rename in renames]
    for r in renames:
        try:
            before = file_signature(r.old)
            if r.signature is not None and before != r.signature:
                raise RenameError('미리보기 이후 원본이 바뀌었습니다')
            if os.path.lexists(r.new) and os.path.abspath(r.new) != os.path.abspath(r.old):
                raise FileExistsError('같은 이름의 파일이 이미 있습니다')
            if os.path.abspath(r.old) != os.path.abspath(r.new):
                os.rename(r.old, r.new)
            if file_signature(r.new) != before:
                raise RenameError('변경 후 파일 식별자·크기·소유권·권한·수정 시각이 달라졌습니다')
            results.append((r, None))
        except Exception as e:
            results.append((r, str(e)))
    return results
