"""Run the reviewed tag pass and verify each original file's UID, GID and mode."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

import unify_tags


def metadata(root):
    result, errors = {}, []
    for path in unify_tags.walk_mp3(root, errors=errors):
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError('Expected a regular MP3 file: ' + path)
        result[os.path.relpath(path, root)] = {
            'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode),
        }
    if errors:
        raise RuntimeError('\n'.join(errors))
    return result


def save(path, data):
    with open(path, 'x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--backup-dir', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = os.path.realpath(args.root)
    backup = os.path.realpath(args.backup_dir)
    if os.path.commonpath((root, backup)) == root:
        raise RuntimeError('Backup directory must be outside the library')
    if not args.apply:
        sys.argv = ['unify_tags.py', root, '--backup-dir', backup]
        return unify_tags.main()

    os.umask(0o077)
    before = metadata(root)
    os.makedirs(backup, mode=0o700, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('unify-%Y%m%d-%H%M%S-'), dir=backup))
    save(run / 'ownership-before.json', {'root': root, 'files': before})
    print('BACKUP_DIRECTORY:', run, flush=True)
    sys.argv = ['unify_tags.py', root, '--backup-dir', str(run), '--apply']
    exit_code = 1
    try:
        exit_code = unify_tags.main()
    except (Exception, SystemExit) as error:
        print('UNIFY_ERROR:', error, file=sys.stderr, flush=True)
    finally:
        after = metadata(root)
        save(run / 'ownership-after.json', {'root': root, 'files': after})
        changed = [path for path in sorted(set(before) | set(after))
                   if before.get(path) != after.get(path)]
        save(run / 'result.json', {'unify_exit_code': exit_code,
                                  'ownership_changes': changed,
                                  'files_checked': len(before)})
        if changed:
            for path in changed:
                print('OWNERSHIP_OR_MODE_CHANGED:', path, file=sys.stderr)
            exit_code = 1
        else:
            print('UID_GID_MODE_OK:', len(before), 'files', flush=True)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
