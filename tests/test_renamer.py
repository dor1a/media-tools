"""Offline provider, rename preservation, and failure-path checks."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from renamer import cli, core, tvdb


def response(status=200, data=None, links=None):
    payload = {'status': 'success', 'data': data}
    if links is not None:
        payload['links'] = links
    return Mock(status_code=status, json=Mock(return_value=payload))


def episode(identity=1, season=1, number=1, absolute=1, title='Title'):
    return {'id': identity, 'seasonNumber': season, 'number': number,
            'absoluteNumber': absolute, 'name': title, 'aired': '2024-01-01'}


class TvdbTests(unittest.TestCase):
    def setUp(self):
        self.client = tvdb.Client('test-key')

    def test_login_without_pin_and_authorization_header(self):
        self.client.session.request = Mock(side_effect=[
            response(data={'token': 'test-token'}), response(data=[])])
        self.assertEqual(self.client.search_series('Example'), [])
        calls = self.client.session.request.call_args_list
        self.assertEqual(calls[0].kwargs['json'], {'apikey': 'test-key'})
        self.assertEqual(calls[1].kwargs['headers'], {'Authorization': 'Bearer test-token'})

    def test_subscription_pin_is_optional(self):
        self.client._pin = 'test-pin'
        self.client.session.request = Mock(return_value=response(data={'token': 'test-token'}))
        self.client._login()
        self.assertEqual(self.client.session.request.call_args.kwargs['json']['pin'], 'test-pin')

    def test_token_refresh_once(self):
        self.client._token = 'old'
        self.client.session.request = Mock(side_effect=[
            response(401), response(data={'token': 'new'}), response(data=[])])
        self.assertEqual(self.client.get('/search')['data'], [])
        self.assertEqual(self.client._token, 'new')

    def test_second_unauthorized_does_not_loop(self):
        self.client._token = 'old'
        self.client.session.request = Mock(side_effect=[
            response(401), response(data={'token': 'new'}), response(401)])
        with self.assertRaises(tvdb.TvdbError):
            self.client.get('/search')
        self.assertEqual(self.client.session.request.call_count, 3)

    def test_transport_error_does_not_expose_credentials(self):
        self.client.session.request = Mock(side_effect=requests.ConnectionError('secret-token'))
        with self.assertRaises(tvdb.TvdbError) as caught:
            self.client.get('/search')
        self.assertNotIn('secret-token', str(caught.exception))

    def test_malformed_json(self):
        self.client._token = 'test'
        self.client.session.request = Mock(return_value=Mock(status_code=200, json=Mock(side_effect=ValueError())))
        with self.assertRaises(tvdb.TvdbError):
            self.client.get('/search')

    def test_malformed_login_data(self):
        self.client.session.request = Mock(return_value=response(data=['unexpected']))
        with self.assertRaises(tvdb.TvdbError):
            self.client._login()

    def test_search_uses_original_language_and_numeric_id(self):
        self.client.get = Mock(return_value={'data': [
            {'tvdb_id': '42', 'name': 'English', 'primary_language': 'jpn',
             'translations': {'jpn': '日本語'}, 'year': '2007'}]})
        result = self.client.search_series('日本語')[0]
        self.assertEqual((result['id'], result['name'], result['original_language']), (42, '日本語', 'jpn'))

    def test_all_pages_and_cache(self):
        next_url = tvdb.BASE_URL + '/series/42/episodes/default/kor?page=1'
        self.client.get = Mock(side_effect=[
            {'data': {'episodes': [episode()]}, 'links': {'next': next_url, 'total_items': 2}},
            {'data': {'episodes': [episode(2, number=2)]}, 'links': {'next': None, 'total_items': 2}}])
        self.assertEqual(len(self.client.episodes(42, 'kor')), 2)
        self.assertEqual(len(self.client.episodes(42, 'kor')), 2)
        self.assertEqual(self.client.get.call_count, 2)

    def test_untrusted_next_url_is_not_requested(self):
        self.client.get = Mock(return_value={'data': {'episodes': []},
                                           'links': {'next': 'https://other.example/?page=1'}})
        with self.assertRaises(tvdb.TvdbError):
            self.client.episodes(42)
        self.assertEqual(self.client.get.call_count, 1)

    def test_repeated_page_is_rejected(self):
        self.client.get = Mock(return_value={'data': {'episodes': []},
            'links': {'next': tvdb.BASE_URL + '/series/42/episodes/default/eng?page=0'}})
        with self.assertRaises(tvdb.TvdbError):
            self.client.episodes(42)

    def test_missing_final_page_is_rejected(self):
        self.client.get = Mock(return_value={'data': {'episodes': [episode()]},
                                           'links': {'next': None, 'total_items': 2}})
        with self.assertRaises(tvdb.TvdbError):
            self.client.episodes(42)

    def test_duplicate_episode_ids_are_rejected(self):
        self.client.get = Mock(return_value={'data': {'episodes': [episode(), episode()]}})
        with self.assertRaises(tvdb.TvdbError):
            self.client.episodes(42)

    def test_season_zero_and_year_seasons(self):
        self.client.episodes = Mock(return_value=[episode(season=0), episode(2, season=2024)])
        self.assertEqual([s['season_number'] for s in self.client.list_seasons(42)], [0, 2024])

    def test_local_and_absolute_numbers_are_distinct(self):
        self.client.episodes = Mock(return_value=[episode(season=2024, number=3, absolute=686)])
        self.assertEqual(set(self.client.episode_map(42, 2024)), {3})
        self.assertEqual(set(self.client.episode_map(42, 2024, numbering='absolute')), {686})

    def test_missing_absolute_number_is_not_invented(self):
        self.client.episodes = Mock(return_value=[episode(absolute=0)])
        with self.assertRaises(tvdb.TvdbError):
            self.client.episode_map(42, 1, numbering='absolute')

    def test_specials_use_local_numbers(self):
        self.client.episodes = Mock(return_value=[episode(season=0, absolute=0)])
        self.assertEqual(set(self.client.episode_map(42, 0, numbering='absolute')), {1})

    def test_duplicate_episode_numbers_are_rejected(self):
        self.client.episodes = Mock(return_value=[episode(), episode(2)])
        with self.assertRaises(tvdb.TvdbError):
            self.client.episode_map(42, 1)

    def test_english_fallback_matches_id_not_list_position(self):
        self.client.episodes = Mock(side_effect=[
            [episode(7, title=None)], [episode(8, title='Wrong'), episode(7, title='Correct')]])
        self.assertEqual(self.client.episode_map(42, 1, 'kor')[1]['title'], 'Correct')


class ProviderTests(unittest.TestCase):
    def test_video_menu_propagates_failure_and_refreshes_files(self):
        from mkv import cli as mkv_cli
        action = Mock(return_value=1)
        with patch.object(mkv_cli.pathinput, 'enable'), \
             patch.object(mkv_cli.core, 'missing_tools', return_value=[]), \
             patch.object(mkv_cli, 'ask_dir', return_value=('/media', ['/media/old.mkv'])), \
             patch.object(mkv_cli, 'ACTIONS', [('Rename', action)]), \
             patch.object(mkv_cli, 'pick', return_value='Rename'), \
             patch.object(mkv_cli.core, 'mkvs_in', return_value=['/media/new.mkv']) as refresh, \
             patch('builtins.input', return_value='n'):
            self.assertEqual(mkv_cli.main('/media'), 1)
        action.assert_called_once_with(['/media/old.mkv'])
        refresh.assert_called_once_with('/media')

    def test_tvdb_selection_does_not_call_tmdb(self):
        with patch.object(core, '_tvdb_call', return_value=[]) as tv, patch.object(core, '_get') as tm:
            core.search_series('Example', 'TVDB')
            tv.assert_called_once_with('search_series', 'Example')
            tm.assert_not_called()

    def test_tmdb_selection_keeps_its_language_and_ids(self):
        with patch.object(core, '_get', return_value={'episodes': [
                {'episode_number': 123, 'air_date': '2024-01-01', 'name': 'Title'}]}) as get:
            result = core.episode_map(42, 1, 'ja', 'TMDB')
            self.assertIn(123, result)
            get.assert_called_once_with('/tv/42/season/1', language='ja')

    def test_tmdb_rejects_absolute_numbering(self):
        with self.assertRaises(core.MetadataError):
            core.episode_map(42, 1, provider='TMDB', numbering='absolute')

    def test_tmdb_http_error_hides_api_key(self):
        with patch.object(core, 'api_key', return_value='test-key'), \
             patch.object(core.requests, 'get', return_value=response(401)):
            with self.assertRaises(core.MetadataError) as caught:
                core.search_series('Example', 'TMDB')
        self.assertNotIn('test-key', str(caught.exception))


class RenameTests(unittest.TestCase):
    def setUp(self):
        base = Path('/tmp') / f'codex-{os.getuid()}' / 'media-tools-tests'
        base.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def file(self, name, content=b'unchanged media bytes'):
        path = self.root / name
        path.write_bytes(content)
        return path

    def metadata(self, number=123):
        return {number: {'air_date': '2024-01-01', 'title': '제목'}}

    def test_full_episode_numbers(self):
        self.assertEqual(core.episode_code('Show - S01E123.mkv'), (1, 123))
        self.assertEqual(core.episode_code('S2024E686.mkv'), (2024, 686))
        self.assertEqual(core.episode_code('S00E04.kor.srt'), (0, 4))

    def test_multi_episode_files_are_not_partially_parsed(self):
        for name in ('S01E01E02.mkv', 'S01E01-E02.mkv', 'S01E01-02.mkv', 'S01E1234x.mkv'):
            self.assertIsNone(core.episode_code(name), name)

    def test_rename_preserves_bytes_and_metadata(self):
        source = self.file('S01E123.mkv')
        before = core.file_signature(source)
        renames = core.plan(str(self.root), 'Show', 1, self.metadata())
        self.assertIn('E123', renames[0].new_name)
        self.assertIsNone(core.apply(renames)[0][1])
        target = Path(renames[0].new)
        self.assertEqual(target.read_bytes(), b'unchanged media bytes')
        self.assertEqual(core.file_signature(target), before)
        self.assertFalse(source.exists())

    def test_existing_destination_stops_whole_batch(self):
        first, second = self.file('first.mkv'), self.file('second.mkv')
        target = self.file('taken.mkv', b'existing')
        renames = [core.Rename(str(first), str(self.root / 'new.mkv')),
                   core.Rename(str(second), str(target))]
        self.assertTrue(all(error for _, error in core.apply(renames)))
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(target.read_bytes(), b'existing')

    def test_duplicate_destination_stops_whole_batch(self):
        first, second = self.file('a.mkv'), self.file('b.mkv')
        target = str(self.root / 'new.mkv')
        result = core.apply([core.Rename(str(first), target), core.Rename(str(second), target)])
        self.assertTrue(all(error for _, error in result))
        self.assertTrue(first.exists())

    def test_source_changed_since_preview_is_rejected(self):
        source = self.file('S01E123.mkv')
        renames = core.plan(str(self.root), 'Show', 1, self.metadata())
        source.write_bytes(b'replaced content')
        self.assertIsNotNone(core.apply(renames)[0][1])
        self.assertTrue(source.exists())

    def test_missing_episode_metadata_stops_planning(self):
        self.file('S01E123.mkv')
        with self.assertRaises(core.RenameError):
            core.plan(str(self.root), 'Show', 1, self.metadata(12))

    def test_missing_title_or_air_date_stops_planning(self):
        self.file('S01E123.mkv')
        for info in ({'title': '', 'air_date': '2024-01-01'}, {'title': 'Title', 'air_date': ''}):
            with self.assertRaises(core.RenameError):
                core.plan(str(self.root), 'Show', 1, {123: info})

    def test_symlinks_are_not_renamed(self):
        target = self.file('real.mkv')
        (self.root / 'S01E123.mkv').symlink_to(target)
        with self.assertRaises(core.RenameError):
            core.plan(str(self.root), 'Show', 1, self.metadata())

    def test_original_path_precedes_nfc_fallback(self):
        source = self.file('가.mkv')
        self.assertEqual(core.existing_path(str(source)), str(source))
        nfd = core.unicodedata.normalize('NFD', str(source))
        self.assertEqual(core.existing_path(nfd), str(source))
        with self.assertRaises(FileNotFoundError):
            core.existing_path(str(self.root / 'missing.mkv'))

    def test_nfc_directory_fallback(self):
        directory = self.root / '가'
        directory.mkdir()
        (directory / 'S01E123.mkv').write_bytes(b'media')
        nfd = core.unicodedata.normalize('NFD', str(directory))
        self.assertEqual(len(core.plan(nfd, 'Show', 1, self.metadata())), 1)

    def test_multiple_seasons_in_one_directory(self):
        self.file('S01E123.mkv')
        self.file('S2024E686.mkv')
        self.assertEqual(core.detect_seasons(str(self.root)), [('.', 1), ('.', 2024)])

    def test_subtitle_suffix_is_not_duplicated(self):
        self.file('S01E123.kor.srt')
        plan = core.plan(str(self.root), 'Show', 1, self.metadata())
        self.assertTrue(plan[0].new_name.endswith('.kor.srt'))
        self.assertNotIn('.kor.kor', plan[0].new_name)

    def test_partial_failure_is_reported(self):
        first, second = self.file('a.mkv'), self.file('b.mkv')
        rename = os.rename

        def fail_second(old, new):
            if old == str(second):
                raise PermissionError('simulated failure')
            rename(old, new)

        plans = [core.Rename(str(first), str(self.root / 'a-new.mkv')),
                 core.Rename(str(second), str(self.root / 'b-new.mkv'))]
        with patch.object(core.os, 'rename', side_effect=fail_second):
            result = core.apply(plans)
        self.assertIsNone(result[0][1])
        self.assertIsNotNone(result[1][1])
        self.assertTrue(second.exists())

    def test_cli_failure_returns_nonzero_for_both_providers(self):
        self.file('S01E123.mkv')
        series = {'id': 42, 'name': 'Show', 'year': '2024', 'original_language': 'eng'}
        for provider in core.PROVIDERS:
            choices = [provider] + (['시즌별 회차'] if provider == 'TVDB' else [])
            choices += [series, '폴더 구조에서 자동 감지']
            with patch.object(cli, 'pick', side_effect=choices), \
                 patch('builtins.input', side_effect=['Show', 'y']), \
                 patch.object(core, 'search_series', return_value=[series]), \
                 patch.object(core, 'episode_map', return_value=self.metadata()), \
                 patch.object(core.os, 'rename', side_effect=PermissionError('simulated failure')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.run(str(self.root)), 1)


if __name__ == '__main__':
    unittest.main()
