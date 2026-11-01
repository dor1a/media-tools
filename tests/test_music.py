"""Offline checks for music folder names, empty stores, disc, cover and genre tags."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mutagen.apev2 import APEv2, APEValue, BINARY
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from music import cli, core, tags

# Tag writers do not read the audio, so a frame header is enough.
MP3_BYTES = b'\xff\xfb\x90\x00' + bytes(413)


def track(disc=1, discs=1, artist='One Direction'):
    return {
        'trackName': 'A.M.', 'collectionName': 'Made In The A.M.',
        'artistName': artist, 'trackNumber': 17, 'trackCount': 17,
        'discNumber': disc, 'discCount': discs,
        'releaseDate': '2015-11-13T08:00:00Z', 'primaryGenreName': 'Pop',
    }


def flac_bytes():
    """Return a FLAC stream holding only STREAMINFO: 44.1 kHz, stereo, 16-bit."""
    fields = (44100 << 44) | (1 << 41) | (15 << 36)
    info = (4096).to_bytes(2, 'big') * 2 + bytes(6) + fields.to_bytes(8, 'big') + bytes(16)
    return b'fLaC\x80' + len(info).to_bytes(3, 'big') + info


class FolderNameTests(unittest.TestCase):
    def test_trailing_dots_become_full_width(self):
        self.assertEqual(core.folder_name('Summer.'), 'Summer．')
        self.assertEqual(core.folder_name('over...'), 'over．．．')
        self.assertEqual(core.folder_name('A: B. '), 'A： B．')

    def test_inner_dots_stay(self):
        self.assertEqual(core.folder_name('Mr.Children'), 'Mr.Children')
        self.assertEqual(core.folder_name('over... - EP'), 'over... - EP')

    def test_destination_changes_folders_but_not_the_file(self):
        t = track()
        path = core.destination('/lib', 'Pop', core.as_album(t), t, '.mp3')
        self.assertEqual(path, Path('/lib/Pop/One Direction/Albums/'
                                    '[2015.11.13] Made In The A.M．/17 - A.M..mp3'))

    def test_artist_folder_takes_full_width_dots(self):
        t = track(artist='Example Inc.')
        path = core.destination('/lib', 'Pop', core.as_album(t), t, '.mp3')
        self.assertEqual(path.parts[3], 'Example Inc．')


class StoreTests(unittest.TestCase):
    def test_cn_points_to_tw(self):
        self.assertEqual(set(core.EMPTY_SEARCH_STORES), {'kr', 'cn'})
        self.assertIn('tw', core.EMPTY_SEARCH_STORES['cn'])


class DiscTagTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        artwork = patch.object(tags, '_artwork', return_value=None)
        artwork.start()
        self.addCleanup(artwork.stop)

    def write(self, name, data, t):
        path = self.dir / name
        path.write_bytes(data)
        tags.apply_tags(path, t)
        return path

    def test_mp3_multi_disc(self):
        path = self.write('a.mp3', MP3_BYTES, track(2, 2))
        self.assertEqual(ID3(path)['TPOS'].text, ['2/2'])
        self.assertEqual(ID3(path)['TRCK'].text, ['17/17'])
        ape = APEv2(path)
        self.assertIn('DISCNUMBER', list(ape.keys()))
        self.assertEqual(str(ape['DISCNUMBER']), '2/2')

    def test_mp3_single_disc(self):
        path = self.write('a.mp3', MP3_BYTES, track())
        self.assertNotIn('TPOS', ID3(path))
        self.assertNotIn('DISCNUMBER', APEv2(path))

    def test_flac_multi_disc(self):
        path = self.write('a.flac', flac_bytes(), track(1, 2))
        self.assertEqual(FLAC(path)['discnumber'], ['1/2'])
        self.assertEqual(FLAC(path)['tracknumber'], ['17/17'])

    def test_flac_single_disc(self):
        path = self.write('a.flac', flac_bytes(), track())
        self.assertNotIn('discnumber', FLAC(path))


class CoverTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_cover_goes_in_apic_only(self):
        path = self.dir / 'a.mp3'
        path.write_bytes(MP3_BYTES)
        old = APEv2()
        old['Cover Art (Front)'] = APEValue(b'Cover Art (Front).jpg\x00old', BINARY)
        old.save(path)
        with patch.object(tags, '_artwork', return_value=b'\xff\xd8new'):
            tags.apply_tags(path, track())
        self.assertEqual(ID3(path).getall('APIC')[0].data, b'\xff\xd8new')
        ape = APEv2(path)
        self.assertNotIn('Cover Art (Front)', ape)
        self.assertEqual(str(ape['TITLE']), 'A.M.')


class GenreTagTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        artwork = patch.object(tags, '_artwork', return_value=None)
        artwork.start()
        self.addCleanup(artwork.stop)

    def test_store_genres_become_english(self):
        self.assertEqual(core.genre_tag('Soundtrack'), 'Soundtrack')
        self.assertEqual(core.genre_tag('OST'), 'Soundtrack')
        self.assertEqual(core.genre_tag('ロック'), 'Rock')
        self.assertEqual(core.genre_tag('R&B／ソウル'), 'R&B')
        self.assertEqual(core.genre_tag(' Dance '), 'Dance')
        self.assertIsNone(core.genre_tag('ダンス'))
        self.assertIsNone(core.genre_tag(''))
        self.assertTrue(all(g.isascii() for g in core.GENRE_TAGS))

    def test_chosen_style_is_written_not_the_bucket(self):
        path = self.dir / 'a.mp3'
        path.write_bytes(MP3_BYTES)
        tags.apply_tags(path, track(), artist='Hallman', genre='Melodic House')
        self.assertEqual(ID3(path)['TCON'].text, ['Melodic House'])
        self.assertEqual(str(APEv2(path)['GENRE']), 'Melodic House')

    def test_store_genre_is_the_default(self):
        path = self.dir / 'a.mp3'
        path.write_bytes(MP3_BYTES)
        tags.apply_tags(path, dict(track(), primaryGenreName='Soundtrack'))
        self.assertEqual(ID3(path)['TCON'].text, ['Soundtrack'])

    def test_unmapped_store_genre_leaves_no_tag(self):
        mp3, flac = self.dir / 'a.mp3', self.dir / 'a.flac'
        mp3.write_bytes(MP3_BYTES)
        flac.write_bytes(flac_bytes())
        for path in (mp3, flac):
            tags.apply_tags(path, dict(track(), primaryGenreName='ダンス'))
        self.assertNotIn('TCON', ID3(mp3))
        self.assertNotIn('GENRE', APEv2(mp3))
        self.assertNotIn('genre', FLAC(flac))

    def test_ask_style_offers_english_and_rejects_other_input(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with patch('builtins.input', side_effect=['']):
                self.assertEqual(cli.ask_style({'primaryGenreName': 'Soundtrack'}), 'Soundtrack')
            typed = str(len(core.GENRE_TAGS) + 1)
            with patch('builtins.input', side_effect=[typed, '퓨처 베이스', 'Future Bass']):
                self.assertEqual(cli.ask_style({'primaryGenreName': 'ダンス'}), 'Future Bass')


if __name__ == '__main__':
    unittest.main()
