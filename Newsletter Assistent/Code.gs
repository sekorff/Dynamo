/**
 * Newsletter-Assistent für Gmail (Google Apps Script, an ein Google Sheet gebunden).
 *
 * 1. Scannt das Postfach nach Newslettern (Mails mit List-Unsubscribe-Header)
 * 2. Listet sie als Tabelle mit Checkboxen auf
 * 3. Meldet die ausgewählten ab und verschiebt alle bisherigen Mails in den Papierkorb
 */

const SHEET_NAME = 'Newsletter';
const SEARCH_QUERY =
  '-in:chats -in:sent -in:drafts (category:promotions OR category:updates OR category:forums ' +
  'OR unsubscribe OR abmelden OR abbestellen OR newsletter)';
const PAGE_SIZE = 50;
const TIME_BUDGET_MS = 4.5 * 60 * 1000; // Apps Script bricht nach 6 Minuten ab
const OFFSET_KEY = 'scanOffset';
const HEADERS = [
  'Auswählen', 'Absender', 'E-Mail', 'Konversationen', 'Letzte Mail', 'Letzter Betreff',
  'Abmeldung', 'Status', 'Abmelde-Link', 'Abmelde-Adresse',
];
const COL = { PICK: 0, NAME: 1, EMAIL: 2, COUNT: 3, LAST: 4, SUBJECT: 5, METHOD: 6, STATUS: 7, URL: 8, MAILTO: 9 };

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Newsletter-Assistent')
    .addItem('1. Newsletter scannen (neu)', 'scanNeu')
    .addItem('   Scan fortsetzen', 'scanFortsetzen')
    .addSeparator()
    .addItem('2. Ausgewählte abmelden + alte Mails löschen', 'abmeldenUndLoeschen')
    .addItem('   Ausgewählte nur abmelden', 'nurAbmelden')
    .addItem('   Ausgewählte nur löschen', 'nurLoeschen')
    .addToUi();
}

// ---------------------------------------------------------------- Scan

function scanNeu() {
  scan_(0, {});
}

function scanFortsetzen() {
  const offset = PropertiesService.getDocumentProperties().getProperty(OFFSET_KEY);
  if (offset === null) {
    SpreadsheetApp.getUi().alert('Der letzte Scan ist vollständig. Starte bei Bedarf einen neuen Scan.');
    return;
  }
  scan_(Number(offset), readSenders_());
}

function scan_(offset, senders) {
  const start = Date.now();
  const me = (Session.getActiveUser().getEmail() || '').toLowerCase();
  const keinNewsletter = {};
  let fertig = false;

  while (Date.now() - start < TIME_BUDGET_MS) {
    const threads = GmailApp.search(SEARCH_QUERY, offset, PAGE_SIZE);
    if (threads.length === 0) { fertig = true; break; }

    GmailApp.getMessagesForThreads(threads).forEach(function (msgs) {
      const m = msgs[msgs.length - 1];
      const from = parseFrom_(m.getFrom());
      if (!from.email || from.email === me || keinNewsletter[from.email]) return;

      let s = senders[from.email];
      if (!s) {
        const u = parseUnsubscribe_(header_(m, 'List-Unsubscribe'), header_(m, 'List-Unsubscribe-Post'));
        if (!u.url && !u.mailto) { keinNewsletter[from.email] = true; return; }
        s = senders[from.email] = {
          pick: false, name: from.name, email: from.email, count: 0, last: null, subject: '',
          method: u.oneClick ? 'One-Click' : (u.mailto ? 'E-Mail' : 'Link'),
          status: '', url: u.url, mailto: u.mailto,
        };
      }
      s.count++;
      const d = m.getDate();
      if (!s.last || d > s.last) { s.last = d; s.subject = m.getSubject(); }
    });

    offset += threads.length;
    if (threads.length < PAGE_SIZE) { fertig = true; break; }
  }

  writeSenders_(senders);
  const props = PropertiesService.getDocumentProperties();
  const n = Object.keys(senders).length;
  if (fertig) {
    props.deleteProperty(OFFSET_KEY);
    SpreadsheetApp.getUi().alert('Scan abgeschlossen: ' + n + ' Newsletter gefunden.\n\n' +
      'Setze Haken in Spalte A und wähle dann im Menü "Ausgewählte abmelden + alte Mails löschen".');
  } else {
    props.setProperty(OFFSET_KEY, String(offset));
    SpreadsheetApp.getUi().alert('Zeitlimit erreicht: bisher ' + n + ' Newsletter aus ' + offset +
      ' Konversationen.\n\nWähle "Scan fortsetzen", um weiterzusuchen.');
  }
}

function header_(message, name) {
  try { return message.getHeader(name) || ''; } catch (e) { return ''; }
}

function parseFrom_(from) {
  const m = /^\s*"?([^"<]*)"?\s*<([^>]+)>\s*$/.exec(from || '');
  const email = (m ? m[2] : (from || '')).trim().toLowerCase();
  if (!/^[^\s@()]+@[^\s@()]+$/.test(email)) return { name: '', email: '' };
  return { name: (m && m[1].trim()) || email, email: email };
}

function parseUnsubscribe_(listUnsubscribe, listUnsubscribePost) {
  const res = { url: '', mailto: '', oneClick: false };
  const re = /<([^>]+)>/g;
  let m;
  while ((m = re.exec(listUnsubscribe || '')) !== null) {
    const v = m[1].trim();
    if (/^https?:\/\//i.test(v) && !res.url) res.url = v;
    else if (/^mailto:/i.test(v) && !res.mailto) res.mailto = v;
  }
  res.oneClick = !!res.url && /^https:/i.test(res.url) && /One-Click/i.test(listUnsubscribePost || '');
  return res;
}

// ---------------------------------------------------------------- Tabelle

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
}

function writeSenders_(senders) {
  const sh = sheet_();
  sh.clear();
  sh.getDataRange().clearDataValidations();
  sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold');
  sh.setFrozenRows(1);

  const rows = Object.keys(senders)
    .map(function (k) { return senders[k]; })
    .sort(function (a, b) { return b.count - a.count; })
    .map(function (s) {
      return [s.pick, s.name, s.email, s.count, s.last || '', s.subject, s.method, s.status, s.url, s.mailto];
    });
  if (rows.length === 0) return;
  sh.getRange(2, 1, rows.length, HEADERS.length).setValues(rows);
  sh.getRange(2, 1, rows.length, 1).insertCheckboxes();
  sh.getRange(2, COL.LAST + 1, rows.length, 1).setNumberFormat('dd.MM.yyyy');
  sh.autoResizeColumns(1, COL.STATUS + 1);
  sh.setColumnWidth(COL.SUBJECT + 1, 320);
}

function readRows_() {
  const sh = sheet_();
  const last = sh.getLastRow();
  return last < 2 ? [] : sh.getRange(2, 1, last - 1, HEADERS.length).getValues();
}

function readSenders_() {
  const senders = {};
  readRows_().forEach(function (r) {
    if (!r[COL.EMAIL]) return;
    senders[r[COL.EMAIL]] = {
      pick: r[COL.PICK] === true, name: r[COL.NAME], email: r[COL.EMAIL], count: Number(r[COL.COUNT]) || 0,
      last: r[COL.LAST] instanceof Date ? r[COL.LAST] : null, subject: r[COL.SUBJECT],
      method: r[COL.METHOD], status: r[COL.STATUS], url: r[COL.URL], mailto: r[COL.MAILTO],
    };
  });
  return senders;
}

// ---------------------------------------------------------------- Abmelden & Löschen

function abmeldenUndLoeschen() { run_(true, true); }
function nurAbmelden() { run_(true, false); }
function nurLoeschen() { run_(false, true); }

function run_(doUnsub, doDelete) {
  const ui = SpreadsheetApp.getUi();
  const sh = sheet_();
  const rows = readRows_();
  const picks = [];
  rows.forEach(function (r, i) { if (r[COL.PICK] === true && r[COL.EMAIL]) picks.push(i); });
  if (picks.length === 0) {
    ui.alert('Nichts ausgewählt. Setze zuerst Haken in Spalte A.');
    return;
  }

  const was = [doUnsub ? 'abmelden' : '', doDelete ? 'alle bisherigen Mails in den Papierkorb verschieben' : '']
    .filter(String).join(' und ');
  const ok = ui.alert('Bitte bestätigen',
    picks.length + ' Newsletter ' + was + '?\n\n' +
    (doDelete ? 'Mails im Papierkorb lassen sich 30 Tage lang wiederherstellen.' : ''),
    ui.ButtonSet.YES_NO);
  if (ok !== ui.Button.YES) return;

  const start = Date.now();
  let erledigt = 0;
  let abgebrochen = false;

  for (let p = 0; p < picks.length; p++) {
    if (Date.now() - start > TIME_BUDGET_MS) { abgebrochen = true; break; }
    const i = picks[p];
    const r = rows[i];
    const alt = String(r[COL.STATUS] || '');
    const teile = [];
    let komplett = true;

    if (doUnsub) {
      // Bereits erfolgreich abgemeldet (z. B. nach Zeitlimit-Abbruch)? Dann nicht doppelt abmelden.
      teile.push(alt.indexOf('✔') === 0 ? alt.split(' | ')[0] : unsubscribe_(r));
    }
    if (doDelete) {
      const t = trashAll_(r[COL.EMAIL], start);
      teile.push(t.count + ' Konversationen gelöscht' + (t.done ? '' : ' (unvollständig, bitte erneut ausführen)'));
      if (!t.done) komplett = false;
    }

    sh.getRange(i + 2, COL.STATUS + 1).setValue(teile.join(' | '));
    if (komplett) { sh.getRange(i + 2, COL.PICK + 1).setValue(false); erledigt++; }
    SpreadsheetApp.flush();
  }

  ui.alert(erledigt + ' von ' + picks.length + ' Newslettern erledigt.' +
    (abgebrochen || erledigt < picks.length
      ? '\n\nZeitlimit erreicht. Die übrigen sind noch angehakt, führe den Menüpunkt einfach erneut aus.'
      : '') +
    '\n\nDetails stehen in der Spalte "Status".');
}

function unsubscribe_(r) {
  const url = String(r[COL.URL] || '');
  const mailto = String(r[COL.MAILTO] || '');
  try {
    if (r[COL.METHOD] === 'One-Click' && url) {
      const res = UrlFetchApp.fetch(url, {
        method: 'post', payload: 'List-Unsubscribe=One-Click', muteHttpExceptions: true,
      });
      if (res.getResponseCode() < 400) return '✔ Abgemeldet (One-Click)';
      if (!mailto) return '✘ Abmeldung fehlgeschlagen (HTTP ' + res.getResponseCode() + '), Link manuell öffnen';
    }
    if (mailto) {
      const mt = parseMailto_(mailto);
      if (mt.to) {
        GmailApp.sendEmail(mt.to, mt.subject || 'unsubscribe', mt.body || 'unsubscribe');
        return '✔ Abmelde-Mail gesendet an ' + mt.to;
      }
    }
    if (url) {
      const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
      return res.getResponseCode() < 400
        ? '⚠ Abmelde-Link aufgerufen, ggf. im Browser bestätigen (Spalte I)'
        : '✘ Abmelde-Link fehlgeschlagen (HTTP ' + res.getResponseCode() + '), manuell öffnen';
    }
    return '✘ Keine Abmeldemöglichkeit gefunden';
  } catch (e) {
    return '✘ Fehler bei Abmeldung: ' + e.message;
  }
}

function parseMailto_(mailto) {
  const s = String(mailto).replace(/^mailto:/i, '');
  const q = s.indexOf('?');
  const out = { to: decodeURIComponent(q < 0 ? s : s.slice(0, q)).trim(), subject: '', body: '' };
  if (q >= 0) {
    s.slice(q + 1).split('&').forEach(function (kv) {
      const eq = kv.indexOf('=');
      const k = (eq < 0 ? kv : kv.slice(0, eq)).toLowerCase();
      let v = eq < 0 ? '' : kv.slice(eq + 1);
      try { v = decodeURIComponent(v.replace(/\+/g, ' ')); } catch (e) { /* Rohwert behalten */ }
      if (k === 'subject') out.subject = v;
      if (k === 'body') out.body = v;
    });
  }
  if (!/^[^\s@,]+@[^\s@,]+$/.test(out.to)) out.to = '';
  return out;
}

function trashAll_(email, start) {
  const query = 'from:(' + email + ') -in:chats';
  let count = 0;
  for (let runde = 0; runde < 200; runde++) {
    if (Date.now() - start > TIME_BUDGET_MS) return { count: count, done: false };
    const threads = GmailApp.search(query, 0, 100);
    if (threads.length === 0) return { count: count, done: true };
    GmailApp.moveThreadsToTrash(threads);
    count += threads.length;
  }
  return { count: count, done: false };
}
