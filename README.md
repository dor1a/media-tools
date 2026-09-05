# media-tools

Tools for keeping `/Volumes/Media` in the shape it is supposed to be in.
They expect that library's naming rules, so they are of little use anywhere
else.

`media.py` is the way in. It asks which medium first, because the two sides
want different things: video needs mkvtoolnix and collects mkv files, audio
needs neither and reads mp3 and flac.

```bash
python3 media.py                 # 영상 or 음원
python3 media.py "/path/here"    # the folder is handed to whichever you pick
```

Both sides still stand on their own — `python3 mkvtool.py`, `python3 -m
music.cli` — and the video side keeps its own single-folder habit: it asks
once and tracks, subtitles and renaming all work on that folder.

## mkvtool.py

Menu-driven mkv handling, built on mkvtoolnix. Replaces ten shell scripts
that differed by a single `mkvmerge` flag each.

```bash
python3 mkvtool.py "/path/to/folder"
python3 mkvtool.py                      # lists the folders below you that hold mkv files
```

The folder prompt has shell-style editing — arrows, delete, and tab
completion over directories. Spaces are not treated as separators, so
`Re Z<tab>` finishes a folder whose name contains them.

| Menu | What it does |
| --- | --- |
| 이름 정리 (TMDB) | hands the same folder to the renamer below — first because it is the one used most |
| 트랙 정보 보기 | video, audio and subtitle tracks per file |
| 오디오 언어 검사 | reports files whose first audio track is not the expected language |
| 오디오 언어 변경 | `mkvpropedit`, no remux |
| 자막 넣기 | muxes `<name>.kor.srt`/`.ass`, optionally dropping existing subtitles |
| 오디오 트랙 제거 | drops one audio track by id |

Remuxing rewrites the whole file, so it keeps the original as `<name>.backup`
and asks whether to delete those once the batch finishes — right after you can
see whether it worked. It also prints a percentage per file: on a network
mount a single episode is a multi-GB round trip, and without that the tool
looks hung.

Two track numbering schemes are in play and the info screen prints both:
`mkvpropedit` counts audio tracks (`a1`, `a2`), `mkvmerge` uses global track
ids (`id0`, `id1`). Mixing them up removes the wrong track.

Requires `mkvtoolnix`. The mkv menus have no Python dependencies; the renamer
is imported only when you pick it, so `requests` is needed only then.

## renamer

Renames episodes from TMDB metadata into

```
[2022-09-28] 원제 - S01E05 - 에피소드제목.mkv
```

Illegal characters become their full-width forms (`:` → `：`, `?` → `？`), and
subtitles get `.kor` inserted before the extension.

```bash
cp .env.example .env      # then put your TMDB key in it
pip install -r requirements.txt
```

Reachable from `mkvtool.py` as 이름 정리 (TMDB), which is usually what you
want since the folder is already chosen. It also stands on its own:

```bash
python3 -m renamer.cli "/path/to/series"
python3 -m renamer.gui
```

Rename last. The renamer inserts `.kor` into subtitle names, and 자막 넣기
looks for `<name>.kor.srt` — do it the other way round and the names no
longer line up.

Seasons are found either way:

- **자동 감지** — reads the folder layout (`Season 01`, `Specials`, or the
  `S##E##` in the files) and does every season it finds
- **직접 선택** — you pick one season from TMDB

`core.py` holds the lookups and the naming rules and knows nothing about
either front end, which is why the CLI and the GUI cannot drift apart.

## music

Files a staging folder into the library from iTunes metadata, and tags them
on the way in.

```
Music/<장르>/<아티스트>/<타입>/[2021.01.06] <앨범>/03 - <곡>.flac
```

```bash
pip install -r requirements.txt    # `uv pip install` where the venv has no pip
python3 media.py                   # or: python3 -m music.cli "/path/to/staging"
```

It shows the whole plan and moves nothing until you say so. The destination
is `/Volumes/Media`, where a wrong move is not something you can undo.

Two levels are yours to pick, because the metadata cannot supply them:

- **장르** — the top bucket. Apple's genre does not map onto J-Pop / Utaite /
  Melody, and whether an artist is Anime or J-Pop is a judgement about this
  library rather than about the music
- **타입** — proposed, then confirmable. `Indies` never gets proposed since no
  external source knows about it

The store matters: ask `jp` for J-Pop and `kr` for K-Pop, or the readings and
release dates come back wrong.

What the type proposal reads:

| Result | From |
| --- | --- |
| Singles & EPs | iTunes appends ` - Single` / ` - EP` to the album title, or three tracks and under |
| Live Albums | a live marker in the title |
| Compilations | a best-of marker in the title |
| (no folder) | Various Artists — soundtracks sit flat, as the library has them |
| Albums | everything else |

Album names keep their characters. Illegal ones take their full-width forms,
the same as the renamer, rather than being dropped: the old script stripped
anything non-alphanumeric and turned `Bird & Insect` into `Bird  Insect`.

`core.py` holds the lookups and the path rules, `tags.py` writes ID3v2.4 +
APEv2 + FLAC with cover art, and `cli.py` is the only part that asks
questions. Eight scripts used to carry copies of the first two between them.

## Not here

Windows packaging (pyinstaller, `.exe`) was dropped — nothing here runs on
Windows any more.
