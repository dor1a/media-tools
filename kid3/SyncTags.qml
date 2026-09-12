/**
 * Copy Tag 2 to Tag 3 (APE) and save.
 *
 * Library MP3s use both ID3v2.4 and APE. Kid3 has no equivalent to mp3tag's
 * option to write all tag formats on save, so tags must be copied manually.
 * This action performs that copy for every selected file.
 *
 * Exclude Tag 1 (ID3v1), as decided on 2026-09-12. The tool no longer writes
 * it, and the library has had it removed; copying it would restore deleted tags.
 *
 * Register under Kid3 → Settings → Configure Kid3 → User Actions.
 */

import Kid3 1.1

Kid3Script {
  onRun: {
    // Frame.Tag_1/2/3 are 0/1/2; toTagVersion accepts a bitmask.
    // Keep the array and loop even with one target: each slot needs a
    // separate paste if more targets are added later. A combined mask
    // pasted into only the first slot when tested.
    var source = script.toTagVersion(1 << Frame.Tag_2)
    var targets = [script.toTagVersion(1 << Frame.Tag_3)]

    function doWork() {
      // Skip empty Tag 2 values to avoid spreading empty tags.
      if (app.selectionInfo.tag(Frame.Tag_2).tagFormat) {
        app.copyTags(source)
        for (var i = 0; i < targets.length; ++i) {
          app.pasteTags(targets[i])
        }
        app.saveDirectory()
      }
      if (!nextFile()) {
        Qt.quit()
      } else {
        // Defer the next file until saving finishes, following the
        // same approach as the bundled scripts.
        setTimeout(doWork, 1)
      }
    }

    firstFile()
    doWork()
  }
}
