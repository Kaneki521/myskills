// Dump a Chromium LevelDB directory (Local Extension Settings) to a JSON array of {key, value}.
// Usage: node dump_leveldb.js <db_dir> <out_json>
// Requires the `classic-level` package, resolved via NODE_PATH.

const fs = require('fs');

(async () => {
  const [dbPath, outFile] = process.argv.slice(2);
  if (!dbPath || !outFile) {
    console.error('Usage: node dump_leveldb.js <db_dir> <out_json>');
    process.exit(2);
  }

  let ClassicLevel;
  try {
    ({ ClassicLevel } = require('classic-level'));
  } catch (e) {
    console.error('MISSING_DEPENDENCY: classic-level not found. Run npm install classic-level first.');
    process.exit(3);
  }

  const db = new ClassicLevel(dbPath, { keyEncoding: 'buffer', valueEncoding: 'buffer' });
  try {
    await db.open();
    const entries = [];
    for await (const [key, value] of db.iterator()) {
      entries.push({
        key: Buffer.from(key).toString('utf8'),
        value: Buffer.from(value).toString('utf8'),
      });
    }
    await db.close();
    fs.writeFileSync(outFile, JSON.stringify(entries), 'utf8');
    console.log('DUMP_OK ' + entries.length);
  } catch (e) {
    try { await db.close(); } catch (_) { /* already closed */ }
    console.error('DUMP_ERROR: ' + e.message);
    process.exit(1);
  }
})();
