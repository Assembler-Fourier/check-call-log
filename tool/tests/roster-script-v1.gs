// NORTHWIND Security - Check Call Log: turns ONE name cell on this roster green (92D050) when the
// controller has confirmed that person, or back to white. It can do nothing else: it never writes any text, it
// only colours a cell that still shows the very name the window asked about, and only under today's date (or
// yesterday's, for a night shift past midnight) - never a future or an older day.
// How to switch it on: see "How to switch it on.txt" next to this file on the control-room PC.
// [kept in tests\ as a record: the FIRST version, pasted into the roster and deployed on 2026-10-23 at 21:42.
//  t_script.py runs it as well, so the stand-in for it (fakescript.Script(version=1)) keeps to what it really does]
/** @OnlyCurrentDoc */                   // Google's permission then covers this roster only, no other spreadsheet
var KEY = '%(key)s';
var GREEN = '#92d050';
var WHITE = '#ffffff';

var MAY_CHANGE = {'#ffffff': 1, '#ffff00': 1, '#92d050': 1};    // white, yellow, green: any other colour is left alone

function dayOf(sheet, column) {          // the date in row 2 of a column, as yyyy-MM-dd ('' if there is none)
  var v = sheet.getRange(2, column).getValue();
  if (typeof v === 'number' && v > 30000 && v < 80000) {          // a date kept as a plain number: days since 1899-12-30
    return Utilities.formatDate(new Date(Date.UTC(1899, 11, 30) + Math.round(v) * 86400000), 'UTC', 'yyyy-MM-dd');
  }
  if (v instanceof Date) return Utilities.formatDate(v, sheet.getParent().getSpreadsheetTimeZone(), 'yyyy-MM-dd');
  return '';
}

function todayOrYesterday(day, tz) {
  var now = new Date();
  return day === Utilities.formatDate(now, tz, 'yyyy-MM-dd') ||
         day === Utilities.formatDate(new Date(now.getTime() - 86400000), tz, 'yyyy-MM-dd');
}

function doPost(e) {
  var out;
  try {
    var q = JSON.parse(e.postData.contents);
    if (q.key !== KEY) throw new Error('wrong key');
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    if (q.action === 'ping') {
      out = {ok: true, sheet: sheet.getName()};
    } else {
      if (!/^[A-Z]{1,3}[1-9][0-9]{0,5}$/.test(String(q.cell))) throw new Error('not a cell: ' + q.cell);
      if (Number(String(q.cell).replace(/^[A-Z]+/, '')) < 3) throw new Error('not a cell with a name: ' + q.cell);
      if (q.action !== 'green' && q.action !== 'white' && q.action !== 'check') throw new Error('unknown action');
      var cell = sheet.getRange(String(q.cell));
      var shown = String(cell.getDisplayValue()).replace(/\s+/g, ' ').replace(/^ | $/g, '');
      var want = String(q.name || '').replace(/\s+/g, ' ').replace(/^ | $/g, '');
      var before = String(cell.getBackground()).toLowerCase();
      var day = dayOf(sheet, cell.getColumn());
      if (!want || shown !== want) {
        out = {ok: false, why: 'moved', shown: shown};
      } else if (/^[0-9]/.test(shown)) {             // a time or a date, never a person
        out = {ok: false, why: 'not a name'};
      } else if (!todayOrYesterday(day, sheet.getParent().getSpreadsheetTimeZone())) {
        out = {ok: false, why: 'not today', day: day};
      } else if (!MAY_CHANGE.hasOwnProperty(before)) {
        out = {ok: false, why: 'coloured', before: before};
      } else if (q.action === 'green' || q.action === 'white' || q.action === 'check') {
        var to = q.action === 'white' ? WHITE : GREEN;
        if (q.action !== 'check' && before !== to) cell.setBackground(to);
        out = {ok: true, before: before, after: q.action === 'check' ? before : to};
      } else {
        throw new Error('unknown action');
      }
    }
  } catch (err) {
    out = {ok: false, why: String((err && err.message) || err)};
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}
