// Extracts the REAL batching code and calendar/tooltip date code from the patched firepulse.html and checks them.
const fs = require('fs');
const html = fs.readFileSync(__dirname + '/firepulse.html', 'utf8');
const a = html.indexOf('  const batches = [];'), b = html.indexOf('  const totalRequests');
const batchCode = html.slice(a, b);
const makeBatches = new Function('startDate', 'endDate', batchCode + '\n return batches;');
const total = (s, e) => Math.round((Date.parse(e) - Date.parse(s)) / 864e5) + 1;
let bad = 0;
const cases = [['2026-03-01','2026-03-31'], ['2026-03-05','2026-03-20'], ['2026-10-20','2026-11-15'], ['2026-11-01','2026-11-01'], ['2026-10-25','2026-11-10']];
for (const [s, e] of cases) {
  const bs = makeBatches(new Date(s), new Date(e));
  const covered = bs.reduce((x, y) => x + y.days, 0);
  // also: batches must be contiguous, non-overlapping
  let next = Date.parse(s), contiguous = true;
  for (const x of bs) { if (Date.parse(x.date) !== next) contiguous = false; next += x.days * 864e5; }
  const ok = covered === total(s, e) && contiguous && bs.every(x => x.days >= 1 && x.days <= 10);
  if (!ok) bad++;
  console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${s}..${e}: ${covered}/${total(s, e)} days, ${bs.length} batches`);
}
// Source-level checks on the HTML itself (these do not depend on my replacement code):
const localCalls = html.match(/\.(getDay|getMonth|getDate|setDate|getFullYear)\(/g) || [];
const okLocal = localCalls.length === 0;
if (!okLocal) bad++;
console.log(`  ${okLocal ? 'OK  ' : 'FAIL'} no local-time Date calls remain (found ${localCalls.length})`);
const fmtCalls = html.match(/toLocaleDateString\([^)]*\)/g) || [];
const okFmt = fmtCalls.length === 3 && fmtCalls.every(c => c.includes("timeZone: 'UTC'"));
if (!okFmt) bad++;
console.log(`  ${okFmt ? 'OK  ' : 'FAIL'} all ${fmtCalls.length} toLocaleDateString calls pin timeZone: 'UTC'`);
process.exit(bad ? 1 : 0);
