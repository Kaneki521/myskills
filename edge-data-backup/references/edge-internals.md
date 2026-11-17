# Chromium Profile Internals — Reference

Verified against Edge 150 / Chromium on Windows, Tampermonkey 5.5.1 & 5.6.6242, Automa 1.30.2.
Read this when an extraction returns nothing, a build behaves unusually, or support must be extended.

## 1. Profile discovery

`User Data/` contains profile directories plus machine-level service folders.

- Valid profile dirs match `^(Default|Profile \d+)$`.
- Ignore `System Profile`, `Guest Profile`, and all service folders (`Crashpad`, `ShaderCache`, …).
- Map dir → human name via `Preferences` → `profile.name`. That display name is localized
  ("用户配置 1") and must **not** be used as a filesystem name.
- `Bookmarks`, `Secure Preferences`, `Local Extension Settings/`, `IndexedDB/` all live inside the
  profile directory.

## 2. Extension inventory (`Secure Preferences`)

Path: `<profile>/Secure Preferences` → JSON → `extensions.settings.<id>`.

Each value carries `manifest` (name, version), `path`, `location`, `disable_reasons`.

### Enabled/disabled — the trap

There is **no `state` field**. The `Preferences` file also has an `extensions.settings` object that is
**empty**, so it cannot be used. Enabled state must be derived from `disable_reasons` (a list):

| Value | Meaning | Treat as |
|---|---|---|
| absent / `[]` | no disable reasons | enabled |
| `1` | `DISABLE_USER_ACTION` — user turned it off | **disabled** |
| `2` | `DISABLE_PERMISSIONS_INCREASE` — disabled pending permission approval | **disabled** |
| `134217728` (2²⁷) | external/sideload restriction flag, not a disable | enabled (note it) |

Combinations like `[1, 2]` are common. Cross-check: a disabled extension usually has no
`Local Extension Settings/<id>` directory, which is a useful sanity check.

### `location` enum (`Manifest::Location`)

| Value | Constant | Meaning |
|---|---|---|
| 1 | INTERNAL | installed from the store |
| 2 | EXTERNAL_PREF | external, via preferences |
| 3 | EXTERNAL_REGISTRY | external, via registry |
| 4 | UNPACKED | developer "load unpacked" |
| 5 | COMPONENT | browser built-in (exclude from user inventory) |
| 6 | EXTERNAL_PREF_DOWNLOAD | pre-installed and downloaded |
| 7 | POLICY_DOWNLOAD | policy |
| 8 | COMMAND_LINE | command line |
| 9 | POLICY | enterprise policy |
| 10 | EXTERNAL_COMPONENT | external component |

### Name resolution

- `manifest.name` is normally a literal string.
- Placeholder form `__MSG_<key>__` must be resolved from `<extension>/_locales/<locale>/messages.json`.
- Empty name + `location == 4` → derive from the basename of `path`.
- Entries with no `path` key **and** fewer than 10 keys are empty placeholders — skip them.

## 3. Tampermonkey (`Local Extension Settings/<id>`) — the normal case

Well-known IDs: `fcmfnpggmnlmfebfghbfnillijihnkoh` (篡改猴测试版 / beta),
`iikmkjmpaadaobahmlepeloendndfphd` (篡改猴 / stable). Prefer resolving by `manifest.name`
containing `Tampermonkey` or `篡改猴`, falling back to these IDs.

Storage is a LevelDB (chrome.storage.local). Every value is a JSON string wrapped as
`{"origin":"normal","value":<payload>}`. Key prefixes:

| Key | Payload |
|---|---|
| `!extdb.@source#<uuid>` | **the full userscript text**, including the `// ==UserScript==` header |
| `!extdb.@meta#<uuid>` | metadata object (author, description, grant, enabled, …) |
| `!extdb.@uid#<uuid>` | the script's display name |
| `!extdb.@re#<uuid>` | match/include/exclude rules |
| `!extdb.@st#<uuid>` | per-script runtime state |
| `!extdb.@ext#<uuid>:<hash>` | `@require` resources, **base64-encoded** |
| `!extdb.#config`, `#version`, `#schema` | global config and app version |

To export usable scripts, read `@source#` entries, `JSON.parse` the wrapper, take `.value`, and
verify it contains `==UserScript==`. Parse the name from the header:

```
// @name         My Script
// @name:zh-CN   中文名
```

`@require` dependencies are stored base64 separately and are **not** inlined; reinstalled scripts
fetch them over the network on first run.

**Alternate layout**: some builds/versions keep scripts in IndexedDB instead
(`IndexedDB/chrome-extension_<id>_0.indexeddb.leveldb`). If `Local Extension Settings/<id>` is absent
or has no `@source#` keys, dump that IndexedDB LevelDB the same way and look for an object store
holding the script text.

## 4. Automa (`Local Extension Settings/infppggnoaenmfagbfknfkancpbljcca`)

Resolve by `manifest.name == "Automa"` first; the ID above is the store build.

- Key `workflows` → JSON object `{ "<workflowId>": { …workflow… } }`.
- Each workflow carries `name`, `drawflow` (nodes/edges), `trigger`, `settings`, `version`, `id`.
- Export one JSON file per workflow, plus `_全部工作流(合并).json` containing the array of all
  workflows. Automa imports a single workflow object per file.
- Key `session` holds `access_token` (a JWT) and `user` info — **sensitive**, must never be left in a
  temporary snapshot.
- Empty `workflows` → `{}` or the key is absent; the profile simply has no workflows.
- Automa's IndexedDB (`chrome-extension_<id>_0.indexeddb.leveldb`) may hold run logs/tables; workflow
  definitions live in the storage key above and do not require IndexedDB.

## 5. Bookmarks (`<profile>/Bookmarks`)

Plain JSON. Structure:

```
roots.bookmark_bar | roots.other | roots.synced
  { type: "folder", name, date_added, date_modified, children: [...] }
  { type: "url", name, url, date_added }
```

`date_added` / `date_modified` are Chromium microseconds since 1601, and arrive as **numbers or
strings** depending on the build — always coerce. Netscape export uses seconds: divide by 1e6, and
that value is `ADD_DATE`.

Convert to Netscape bookmark HTML (`<!DOCTYPE NETSCAPE-Bookmark-file-1>`), which both Edge and Chrome
import with appending semantics.

## 6. Reading a locked LevelDB

While the browser runs, the directory's `LOCK` file is held exclusively:

- Opening the live directory → `Database failed to open` (via `classic-level`, Node).
- `readOnly: true` does **not** bypass it — the lock is at open time.
- Copying the directory with `cp -r` reports `LOCK: Device or resource busy` — expected and harmless;
  `LOCK` is a zero-byte marker that LevelDB recreates.

Correct approach: copy every other file (`.log`, `.ldb`, `CURRENT`, `MANIFEST-*`) into a temp dir,
then open the copy. A snapshot taken mid-compaction is occasionally unreadable → retry once.

Python cannot read LevelDB natively here (`plyvel` has no Windows wheel for Python 3.13 and fails to
build). Use Node with `classic-level`, resolved through `NODE_PATH`, which is what
`scripts/dump_leveldb.js` does.

## 7. Scope boundaries

Deliberately not exported: cookies, saved passwords, history, session tabs, extension preferences,
and the enabled/disabled state of individual userscripts. Mention these gaps explicitly so the user
does not assume a full profile clone.
