/**
 * 가천대학교 건설환경공학과 동문 수첩 — 구글 시트 저장용 Apps Script
 *
 * 사용법
 *  1) 이 코드를 구글 시트의 [확장 프로그램 → Apps Script] 에 통째로 붙여 넣는다.
 *  2) 아래 TOKEN 값을 나만 아는 문자열로 바꾼다. (Streamlit Secrets 의 GSCRIPT_TOKEN 과 똑같이)
 *  3) [배포 → 새 배포 → 웹 앱], 실행 사용자: 나, 액세스 권한: 모든 사용자 로 배포한다.
 */

const TOKEN = '여기를_나만_아는_문자열로_바꾸세요';   // ← 반드시 수정
const SHEET_NAME = '동문명단';
const HEADER = ['이름', '학번', '현재분야', '소속_직무', '한줄인사', '연락처', '비밀번호', '업데이트일시'];

// 시트가 없으면 만들고, 1행 헤더를 맞춘다. 모든 칸을 '일반 텍스트'로 지정해 05학번이 5로 바뀌지 않게 한다.
function getSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) sh = ss.insertSheet(SHEET_NAME);
  sh.getRange(1, 1, sh.getMaxRows(), HEADER.length).setNumberFormat('@');
  sh.getRange(1, 1, 1, HEADER.length).setValues([HEADER]);
  return sh;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// 읽기: 전체 명단을 돌려준다.
function doGet(e) {
  if (!e || !e.parameter || e.parameter.token !== TOKEN) return json_({ ok: false, error: 'token' });
  const sh = getSheet_();
  const n = sh.getLastRow() - 1;
  const rows = n > 0 ? sh.getRange(2, 1, n, HEADER.length).getDisplayValues() : [];
  return json_({ ok: true, rows: rows });
}

// 쓰기: 받은 전체 명단으로 시트 내용을 교체한다.
function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ ok: false, error: 'bad request' });
  }
  if (body.token !== TOKEN) return json_({ ok: false, error: 'token' });

  const rows = body.rows || [];
  if (rows.some(function (r) { return r.length !== HEADER.length; })) {
    return json_({ ok: false, error: 'bad rows' });
  }

  const lock = LockService.getScriptLock();   // 동시에 저장할 때 서로 덮어쓰지 않도록 잠금
  lock.waitLock(20000);
  try {
    const sh = getSheet_();
    const last = sh.getLastRow();
    if (last > 1) sh.getRange(2, 1, last - 1, HEADER.length).clearContent();
    if (rows.length > 0) {
      const range = sh.getRange(2, 1, rows.length, HEADER.length);
      range.setNumberFormat('@');
      range.setValues(rows);
    }
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }
  return json_({ ok: true, count: rows.length });
}

// (선택) 편집기에서 이 함수를 한 번 실행하면 권한 승인과 시트·헤더 생성이 미리 끝난다.
function setup() {
  getSheet_();
}
