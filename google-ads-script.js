/**
 * ProfitDesk — copy this Google Ads account's spend into a Google Sheet.
 *
 * Paste into: Google Ads → Tools → Bulk actions → Scripts → + New script.
 * Set SHEET_URL below, click Authorize, Run once (copies the full history),
 * then set the schedule to Hourly.
 *
 * Writes one tab per ad account, named by its customer ID (123-456-7890):
 *   row 1   ProfitDesk | customer ID | account name | currency | time zone | updated
 *   row 2   date | hour | cost
 *   rows    one "day" row per date with spend, plus hour 0–23 rows for the
 *           last few days (so "today vs same time yesterday" stays fair)
 * Only spend is read. Nothing in the account is changed.
 */
var SHEET_URL = 'PASTE_YOUR_SHEET_URL_HERE';

var HISTORY_FROM = '2019-01-01'; // first run copies everything from here
var REFRESH_DAYS = 7;            // later runs re-read this many days (Google revises recent spend)
var HOURLY_DAYS = 3;             // keep hour-by-hour spend for this many days

function main() {
  var acct = AdsApp.currentAccount();
  var id = acct.getCustomerId();
  var tz = acct.getTimeZone();
  var ss = SpreadsheetApp.openByUrl(SHEET_URL);
  var tab = ss.getSheetByName(id) || ss.insertSheet(id);

  var rows = readRows(tab); // "date|hour" -> cost
  var today = dayString(0, tz);
  var from = Object.keys(rows).length ? dayString(REFRESH_DAYS, tz) : HISTORY_FROM;
  var hourlyFrom = dayString(HOURLY_DAYS, tz);

  // Forget the days being re-read, and hourly rows that are now too old.
  for (var key in rows) {
    var parts = key.split('|');
    if (parts[0] >= from || (parts[1] !== 'day' && parts[0] < hourlyFrom)) delete rows[key];
  }

  var daily = AdsApp.search(
    "SELECT segments.date, metrics.cost_micros FROM customer " +
    "WHERE segments.date BETWEEN '" + from + "' AND '" + today + "'");
  while (daily.hasNext()) {
    var r = daily.next();
    rows[r.segments.date + '|day'] = Number(r.metrics.costMicros) / 1e6;
  }

  var hourly = AdsApp.search(
    "SELECT segments.date, segments.hour, metrics.cost_micros FROM customer " +
    "WHERE segments.date BETWEEN '" + hourlyFrom + "' AND '" + today + "'");
  while (hourly.hasNext()) {
    var h = hourly.next();
    rows[h.segments.date + '|' + h.segments.hour] = Number(h.metrics.costMicros) / 1e6;
  }

  var out = Object.keys(rows).sort().map(function (k) {
    var p = k.split('|');
    return [p[0], p[1], Math.round(rows[k] * 100) / 100];
  });

  tab.clearContents();
  tab.getRange(1, 1, 1, 6).setValues([[
    'ProfitDesk', id, acct.getName(), acct.getCurrencyCode(), tz, new Date().toISOString()]]);
  tab.getRange(2, 1, 1, 3).setValues([['date', 'hour', 'cost']]);
  if (out.length) tab.getRange(3, 1, out.length, 3).setNumberFormat('@').setValues(out);
  Logger.log('ProfitDesk: wrote ' + out.length + ' rows for ' + id + ' (' + acct.getName() + ')');
}

function readRows(tab) {
  var rows = {};
  var last = tab.getLastRow();
  if (last < 3) return rows;
  var values = tab.getRange(3, 1, last - 2, 3).getValues();
  for (var i = 0; i < values.length; i++) {
    if (values[i][0]) rows[String(values[i][0]) + '|' + String(values[i][1])] = Number(values[i][2]);
  }
  return rows;
}

function dayString(daysAgo, tz) {
  return Utilities.formatDate(new Date(Date.now() - daysAgo * 86400000), tz, 'yyyy-MM-dd');
}
