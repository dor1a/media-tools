/**
 * Tag 2 의 내용을 Tag 3(APE)에 복사하고 저장한다.
 *
 * 라이브러리의 mp3 는 ID3v2.4 와 APE 두 슬롯을 채우는데, Kid3 에는 mp3tag
 * 처럼 "저장할 때 전부 쓰기"에 해당하는 설정이 없어서 손으로 복사해야 한다.
 * 선택한 파일 전부에 대해 그것을 대신한다.
 *
 * Tag 1(ID3v1) 은 대상이 아니다 (2026-09-12 결정). 도구가 더는 쓰지 않고
 * 라이브러리에서도 걷어냈으므로, 여기서 복사하면 지운 것을 되살리게 된다.
 *
 * Kid3 → Settings → Configure Kid3 → User Actions 에 등록해서 쓴다.
 */

import Kid3 1.1

Kid3Script {
  onRun: {
    // Frame.Tag_1/2/3 은 0/1/2 이고, toTagVersion 은 비트마스크를 받는다.
    // 대상이 하나로 줄었어도 배열과 반복은 남겨 둔다 — 슬롯을 다시 늘릴 때
    // 붙여넣기를 슬롯마다 따로 해야 한다는 것이 여기 적혀 있어야 한다.
    // 두 비트를 합친 마스크로 한 번에 부르면 앞의 하나에만 들어간다 (실측).
    var source = script.toTagVersion(1 << Frame.Tag_2)
    var targets = [script.toTagVersion(1 << Frame.Tag_3)]

    function doWork() {
      // Tag 2 가 비어 있으면 빈 값을 퍼뜨리게 되므로 건너뛴다.
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
        // 한 박자 쉬어야 저장이 끝난 뒤 다음 파일로 넘어간다. 번들 스크립트도
        // 같은 방식이다.
        setTimeout(doWork, 1)
      }
    }

    firstFile()
    doWork()
  }
}
