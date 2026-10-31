"""Offline checks for music folder names, empty stores, and disc tags."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mutagen.apev2 import APEv2
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from music import core, tags

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


if __name__ == '__main__':
    unittest.main()
