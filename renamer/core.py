"""TMDB lookups and filename rules shared by cli.py and gui.py."""

import os
import re
from dataclasses import dataclass

import requests

TMDB_BASE_URL = "https://api.themoviedb.org/3"
SUBTITLE_EXTS = ['.srt', '.smi', '.ass']

# Windows-illegal characters, replaced with their full-width forms so the
# names survive on any filesystem and still read the same.
SANITIZE_MAP = {
    '/': '／', '\\': '＼', ':': '：', '*': '＊', '?': '？',
    '"': "'", '<': '〈', '>': '〉', '|': '｜',
}


class TmdbError(RuntimeError):
    pass


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
    r = requests.get(f"{TMDB_BASE_URL}{path}", params=params, timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


# --- TMDB ------------------------------------------------------------------

def search_series(query):
    """[{id, name, year, original_language}, ...]"""
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


def list_seasons(series_id):
    """Return [{season_number, episode_count, air_date}, ...], including season 0."""
    data = _get(f'/tv/{series_id}') or {}
    return [
        {
            'season_number': s['season_number'],
            'episode_count': s.get('episode_count'),
            'air_date': s.get('air_date'),
        }
        for s in data.get('seasons', [])
    ]


def episode_map(series_id, season_number, lang='en'):
    """Return {episode_number: {air_date, title}}, falling back to English."""
    data = _get(f'/tv/{series_id}/season/{season_number}', language=lang)
    if not data or not data.get('episodes'):
        data = _get(f'/tv/{series_id}/season/{season_number}', language='en')
    if not data:
        return {}
    return {
        ep['episode_number']: {
            'air_date': ep.get('air_date') or '방영일 없음',
            'title': ep.get('name') or '제목 없음',
        }
        for ep in data.get('episodes', [])
    }


# --- Filenames -------------------------------------------------------------

def sanitize(name):
    for bad, safe in SANITIZE_MAP.items():
        name = name.replace(bad, safe)
    return name


def episode_code(filename):
    """Extract (season, episode) from a filename, or return None."""
    m = re.search(r'S(\d{1,2})E(\d{1,2})', filename, re.IGNORECASE)
    return (int(m.group(1)), int(m.group(2))) if m else None


def build_name(title, season, episode, air_date, ep_title, ext):
    """Build [air_date] original_title - S01E01 - episode_title.ext; add .kor for subtitles."""
    if ext.lower() in SUBTITLE_EXTS:
        ext = f'.kor{ext}'
    return f"[{air_date}] {title} - S{season:02}E{episode:02} - {sanitize(ep_title)}{ext}"


# --- Season discovery ------------------------------------------------------

def detect_seasons(base_dir):
    """Infer seasons from folders. Return [(relative_path, season_number), ...].

    'Specials' is 0 and 'Season N' is N; otherwise inspect S##E## in filenames.
    Include '.' when base_dir itself contains episode files.
    """
    found = []
    for entry in sorted(os.listdir(base_dir)):
        full = os.path.join(base_dir, entry)
        if not os.path.isdir(full):
            continue
        if re.fullmatch(r'(?i)specials', entry):
            found.append((entry, 0))
            continue
        m = re.search(r'Season[ _]?(\d+)', entry, re.IGNORECASE)
        if m:
            found.append((entry, int(m.group(1))))
            continue
        for f in sorted(os.listdir(full)):
            code = episode_code(f)
            if code:
                found.append((entry, code[0]))
                break

    for f in sorted(os.listdir(base_dir)):
        if os.path.isfile(os.path.join(base_dir, f)) and episode_code(f):
            found.append(('.', episode_code(f)[0]))
            break

    return found


# --- Rename plans ----------------------------------------------------------

@dataclass
class Rename:
    old: str
    new: str

    @property
    def old_name(self):
        return os.path.basename(self.old)

    @property
    def new_name(self):
        return os.path.basename(self.new)


def plan(directory, title, season_number, episodes):
    """Plan renames for one season in a directory without changing files."""
    out = []
    for fname in sorted(os.listdir(directory)):
        code = episode_code(fname)
        if not code:
            continue
        season, ep_num = code
        if season != season_number or ep_num not in episodes:
            continue
        info = episodes[ep_num]
        ext = os.path.splitext(fname)[1]
        new = build_name(title, season_number, ep_num, info['air_date'], info['title'], ext)
        out.append(Rename(os.path.join(directory, fname), os.path.join(directory, new)))
    return out


def apply(renames):
    """Apply the plan and return [(Rename, None|error_message), ...]."""
    results = []
    for r in renames:
        try:
            if os.path.exists(r.new) and os.path.abspath(r.new) != os.path.abspath(r.old):
                raise FileExistsError('같은 이름의 파일이 이미 있습니다')
            os.rename(r.old, r.new)
            results.append((r, None))
        except Exception as e:
            results.append((r, str(e)))
    return results
