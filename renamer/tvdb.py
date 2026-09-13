"""TheTVDB v4 client with in-memory authentication and pagination."""

from functools import lru_cache
from urllib.parse import parse_qs, urlsplit

import requests

BASE_URL = 'https://api4.thetvdb.com/v4'


class TvdbError(RuntimeError):
    pass


class Client:
    def __init__(self, api_key, pin=None):
        self._api_key = api_key
        self._pin = pin
        self._token = None
        self.session = requests.Session()

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, BASE_URL + path,
                                            timeout=20, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise TvdbError('TVDB 연결에 실패했습니다. 네트워크 상태를 확인하세요.') from None
        return response

    @staticmethod
    def _json(response):
        try:
            payload = response.json()
        except ValueError:
            raise TvdbError('TVDB 응답을 해석할 수 없습니다.') from None
        if not isinstance(payload, dict) or payload.get('status') != 'success':
            raise TvdbError('TVDB가 정상 데이터를 반환하지 않았습니다.')
        return payload

    def _login(self):
        credentials = {'apikey': self._api_key}
        if self._pin:
            credentials['pin'] = self._pin
        response = self._request('POST', '/login', json=credentials)
        if response.status_code != 200:
            raise TvdbError(f'TVDB 인증 실패 (HTTP {response.status_code}). 키 활성 상태를 확인하세요.')
        data = self._json(response).get('data')
        token = data.get('token') if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise TvdbError('TVDB 인증 토큰이 없습니다.')
        self._token = token

    def get(self, path, **params):
        if self._token is None:
            self._login()
        for attempt in range(2):
            response = self._request('GET', path, params=params,
                                     headers={'Authorization': 'Bearer ' + self._token})
            if response.status_code == 401 and attempt == 0:
                self._login()
                continue
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise TvdbError(f'TVDB 조회 실패 (HTTP {response.status_code}).')
            return self._json(response)

    def search_series(self, query):
        payload = self.get('/search', query=query, type='series', limit=50) or {}
        results = []
        for item in payload.get('data') or []:
            language = item.get('primary_language') or 'eng'
            translations = item.get('translations') or {}
            results.append({
                'id': int(item['tvdb_id']),
                'name': translations.get(language) or item.get('name') or '',
                'year': item.get('year') or (item.get('first_air_time') or '')[:4],
                'original_language': language,
            })
        return results

    @lru_cache(maxsize=32)
    def episodes(self, series_id, language='eng'):
        """Fetch every page in the series default order, without following arbitrary URLs."""
        if not language.isalpha() or len(language) not in (2, 3, 4):
            raise TvdbError('TVDB 언어 코드가 올바르지 않습니다.')
        path = f'/series/{int(series_id)}/episodes/default/{language}'
        result, seen_pages, seen_ids = [], set(), set()
        page = 0
        while True:
            if page in seen_pages:
                raise TvdbError('TVDB 페이지가 반복됩니다. 불완전한 데이터로 진행하지 않습니다.')
            seen_pages.add(page)
            payload = self.get(path, page=page)
            if payload is None:
                raise TvdbError('TVDB에서 시리즈 회차를 찾지 못했습니다.')
            data = payload.get('data') or {}
            episodes = data.get('episodes')
            if not isinstance(episodes, list):
                raise TvdbError('TVDB 회차 목록이 없습니다.')
            for episode in episodes:
                identity = episode.get('id')
                if identity is None or identity in seen_ids:
                    raise TvdbError('TVDB 회차 ID가 없거나 중복됩니다.')
                seen_ids.add(identity)
                result.append(episode)
            links = payload.get('links') or {}
            next_page = links.get('next')
            if next_page is None:
                total = links.get('total_items')
                if total is not None and len(result) != int(total):
                    raise TvdbError('TVDB 회차 수가 맞지 않습니다. 다시 조회하세요.')
                return tuple(result)
            try:
                parsed = urlsplit(next_page)
                expected = urlsplit(BASE_URL + path)
                if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
                    raise ValueError
                next_number = int(parse_qs(parsed.query)['page'][0])
                if next_number <= page:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise TvdbError('TVDB 다음 페이지 정보가 올바르지 않습니다.') from None
            page = next_number

    def list_seasons(self, series_id, language='eng'):
        seasons = {}
        for episode in self.episodes(series_id, language):
            number = episode.get('seasonNumber')
            if not isinstance(number, int) or number < 0:
                raise TvdbError('TVDB 시즌 번호가 올바르지 않습니다.')
            season = seasons.setdefault(number, {'season_number': number, 'episode_count': 0, 'air_date': None})
            season['episode_count'] += 1
            aired = episode.get('aired')
            if aired and (season['air_date'] is None or aired < season['air_date']):
                season['air_date'] = aired
        return [seasons[number] for number in sorted(seasons)]

    def episode_map(self, series_id, season_number, language='eng', numbering='season'):
        if numbering not in ('season', 'absolute'):
            raise TvdbError('TVDB 회차 기준이 올바르지 않습니다.')
        selected = [ep for ep in self.episodes(series_id, language)
                    if ep.get('seasonNumber') == season_number]
        fallback = {}
        if language != 'eng' and any(not ep.get('name') for ep in selected):
            fallback = {ep['id']: ep for ep in self.episodes(series_id, 'eng')}
        result = {}
        for episode in selected:
            # Specials keep their season-local numbering; zero is not an absolute index.
            field = 'absoluteNumber' if numbering == 'absolute' and season_number != 0 else 'number'
            number = episode.get(field)
            if not isinstance(number, int) or number <= 0:
                hint = ' 시즌별 회차와 파일 번호를 확인하세요.' if field == 'absoluteNumber' else ''
                raise TvdbError(f'TVDB 시즌 {season_number}에 유효한 {field}가 없는 회차가 있습니다.' + hint)
            if number in result:
                raise TvdbError(f'TVDB 시즌 {season_number}의 회차 {number}가 중복됩니다.')
            result[number] = {
                'air_date': episode.get('aired') or '',
                'title': episode.get('name') or fallback.get(episode['id'], {}).get('name') or '',
            }
        return result
