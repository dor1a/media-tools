# media-tools

Tools for keeping `/Volumes/Media` in the shape it is supposed to be in.
Both of them expect that library's naming rules, so they are of little use
anywhere else.

## mkvtool.py

Menu-driven mkv handling, built on mkvtoolnix. Replaces ten shell scripts
that differed by a single `mkvmerge` flag each.

```bash
python3 mkvtool.py "/path/to/folder"    # or run bare and it asks
```

| Menu | What it does |
| --- | --- |
| 트랙 정보 보기 | video, audio and subtitle tracks per file |
| 오디오 언어 검사 | reports files whose first audio track is not the expected language |
| 오디오 언어 변경 | `mkvpropedit`, no remux |
| 자막 넣기 | muxes `<name>.kor.srt`/`.ass`, optionally dropping existing subtitles |
| 오디오 트랙 제거 | drops one audio track by id |
| .backup 정리 | removes the originals left behind by remuxing |

Two track numbering schemes are in play and the info screen prints both:
`mkvpropedit` counts audio tracks (`a1`, `a2`), `mkvmerge` uses global track
ids (`id0`, `id1`). Mixing them up removes the wrong track.

Requires `mkvtoolnix`. No Python dependencies.

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

python3 -m renamer.cli "/path/to/series"
python3 -m renamer.gui
```

Seasons are found either way:

- **자동 감지** — reads the folder layout (`Season 01`, `Specials`, or the
  `S##E##` in the files) and does every season it finds
- **직접 선택** — you pick one season from TMDB

`core.py` holds the lookups and the naming rules and knows nothing about
either front end, which is why the CLI and the GUI cannot drift apart.

## Not here

The music tooling (iTunes API, tagging) still lives in `~/Downloads`
pending its own port. Windows packaging (pyinstaller, `.exe`) was dropped —
nothing here runs on Windows any more.
