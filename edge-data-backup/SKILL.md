---
name: edge-data-backup
description: Backs up Chromium browser (Edge/Chrome) profile data without closing the browser — Tampermonkey userscripts, Automa workflows, bookmarks, and a full extension inventory with enabled/total counts. This skill should be used when the user asks to 备份浏览器数据, 导出篡改猴/油猴脚本, 备份收藏夹/书签, 导出 Automa 工作流, 统计扩展清单或扩展启用状态, or to migrate browser extensions and settings to another machine.
agent_created: true
---

# Browser Data Backup (Edge / Chrome)

## Purpose

Extract user-owned data from a Chromium browser profile into portable files, and report which
extensions are installed and how many are actually enabled. The browser stays open the whole time —
LevelDB files are locked while the browser runs, so this skill copies each database into a temporary
snapshot and reads the copy instead of demanding the user quit the browser.

Exports Tampermonkey userscripts, Automa workflows, bookmarks, extension inventory, and a JSON
manifest. Supports Edge and Chrome on Windows; also accepts an offline `User Data` folder.

## When to use

- 用户要备份浏览器数据 / 导出扩展数据 / 迁移到另一台电脑
- 用户要导出篡改猴（油猴、Tampermonkey）脚本，或导出 Automa 工作流
- 用户要备份收藏夹/书签，或要"扩展清单、哪些扩展启用了、激活/总计多少个"
- 用户要对某个离线 `User Data` 目录做提取（配合 `--user-data`）

Not for: Firefox (different storage format), passwords, cookies, or history — those are not exported.

## Quick start

Run the orchestrator, then report the counts it prints:

```bash
python <skill>/scripts/backup.py --out "D:/99.归档/edge数据备份"
```

Common variations:

```bash
# only Edge, only the Default profile
python <skill>/scripts/backup.py --out D:/backup --browser edge --profile Default

# extract from an offline backup / custom location
python <skill>/scripts/backup.py --out D:/backup --user-data "D:/99.归档/User Data"

# skip the userscript zip
python <skill>/scripts/backup.py --out D:/backup --no-zip
```

After it finishes, relay to the user: per-profile counts (userscripts, workflows, bookmarks),
extension totals, and the output path. Then point them at `扩展清单.txt` for the extension table.

## Output layout

```
<out>/
├── Default/                      # one directory per profile, named after the profile dir
│   ├── 篡改猴脚本/                # *.user.js, one file per userscript
│   ├── 篡改猴脚本备份.zip          # importable whole-backup archive
│   ├── Automa工作流/              # *.json per workflow + _全部工作流(合并).json
│   └── 收藏夹/                    # <profile>_收藏夹.html (Netscape format)
├── Profile 2/ ...
├── 扩展清单.txt                   # readable table, enabled/disabled + counts
├── 扩展清单.csv                   # same data, for Excel
└── 备份清单.json                  # machine-readable manifest of everything produced
```

## Importing the results (tell the user this)

- **Tampermonkey**: panel → 实用工具 → 备份 → 导入 → pick the `.zip`. Or drag `.user.js` files
  into the panel. Userscript enabled/disabled state is not carried over.
- **Automa**: workflow list → top-right menu → Import → pick each `.json`.
- **Bookmarks**: `edge://favorites` → "..." → 导入收藏夹 → HTML file. Import appends, never deletes.

## Critical implementation notes

- **Never close the browser.** `snapshot_copy()` skips the exclusive `LOCK` file and copies the rest
  of the LevelDB directory; reading the copy works while the browser keeps writing. Opening the live
  directory fails with `Database failed to open`, and `readOnly: true` does **not** help.
- **Extension enabled state** comes from `disable_reasons` in `Secure Preferences`
  (`extensions.settings`). There is no `state` field, and the `Preferences` file's
  `extensions.settings` is empty. Reasons `1` (user disabled) or `2` (permissions increase) mean
  disabled. Other values such as `134217728` flag an external/restricted source, not a disable.
- **Skip placeholder entries**: ignore settings objects without a `path` key and with fewer than
  10 keys.
- **Unpacked extensions** (`location: 4`) often have an empty `manifest.name`; fall back to the
  basename of `path`.
- **Snapshot cleanup is mandatory** — the Automa `session` key holds a live `access_token` JWT. The
  script deletes every snapshot in a `finally` block; do not remove that.
- Requires Node.js for the LevelDB read. On first run the script runs `npm install classic-level`
  into `%LOCALAPPDATA%\edge-data-backup-cache` (override with `--tools-dir`). If npm is unavailable,
  stop and tell the user rather than silently skipping userscripts.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `未找到浏览器用户数据目录` | Non-standard install; pass `--user-data` pointing at the `User Data` folder |
| `MISSING_DEPENDENCY: classic-level` | Node env not installed; rerun, or `npm install classic-level` in `--tools-dir` |
| `DUMP_ERROR: Database failed to open` | Snapshot taken mid-compaction; rerun. If it persists, the database is corrupt |
| Userscripts missing but extension installed | Some Tampermonkey builds keep scripts in IndexedDB rather than `Local Extension Settings`; see `references/edge-internals.md` |

## References

- `references/edge-internals.md` — LevelDB key schema for Tampermonkey and Automa, the
  `disable_reasons` bitmask, the `location` enum, profile discovery rules, and version compatibility
  notes. Read this when a build behaves differently or the extraction needs extending.
