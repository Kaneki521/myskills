#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Back up Chromium-based browser profile data without closing the browser.

Exports:
  - Tampermonkey userscripts (individually as .user.js + one zip)
  - Automa workflows (one JSON per workflow + merged backup)
  - Bookmarks (Netscape HTML, importable by Edge/Chrome)
  - Extension inventory with enabled/disabled state and counts
  - A human-readable report

Key trick: LevelDB files are locked while the browser runs, so copying them to a
snapshot directory and reading the copy avoids having to close the browser.

Usage:
  python backup.py --out D:/backup --browser auto
  python backup.py --out D:/backup --browser edge --profile Default
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- constants

LOCATION_NAMES = {
    1: "商店安装",
    2: "外部(偏好)",
    3: "外部(注册表)",
    4: "未打包(开发者)",
    5: "内置组件",
    6: "外部下载",
    7: "策略下载",
    8: "命令行",
    9: "策略",
    10: "外部组件",
}

# disable_reasons bit meanings (Chromium): 1 = user disabled, 2 = permissions increase
DISABLE_USER = 1
DISABLE_PERMISSIONS = 2

TM_NAME_HINTS = ("tampermonkey", "篡改猴")
AUTOMA_NAME_HINTS = ("automa",)
KNOWN_TM_IDS = {"fcmfnpggmnlmfebfghbfnillijihnkoh", "iikmkjmpaadaobahmlepeloendndfphd"}
KNOWN_AUTOMA_IDS = {"infppggnoaenmfagbfknfkancpbljcca"}

PROFILE_RE = re.compile(r"^(Default|Profile \d+)$")
SKIP_PROFILES = {"System Profile", "Guest Profile"}

INVALID_FS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def log(msg=""):
    print(msg, flush=True)


def safe_name(name, limit=120):
    cleaned = INVALID_FS.sub("_", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned[:limit] or "untitled")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------- discovery

def candidate_user_data_dirs(browser):
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    edges = [
        Path(local) / "Microsoft" / "Edge" / "User Data",
        Path(roaming) / "Microsoft" / "Edge" / "User Data",
    ]
    chromes = [
        Path(local) / "Google" / "Chrome" / "User Data",
        Path(roaming) / "Google" / "Chrome" / "User Data",
    ]
    if browser == "edge":
        return [p for p in edges if p.is_dir()]
    if browser == "chrome":
        return [p for p in chromes if p.is_dir()]
    return [p for p in edges + chromes if p.is_dir()]


def detect_profiles(user_data):
    out = []
    for entry in sorted(user_data.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in SKIP_PROFILES:
            continue
        if PROFILE_RE.match(entry.name):
            out.append(entry)
    return out


def profile_label(profile_dir):
    prefs = read_json(profile_dir / "Preferences") or {}
    return prefs.get("profile", {}).get("name") or profile_dir.name


# ---------------------------------------------------------------- extensions

def resolve_localized_name(name, profile_dir, ext_id, info):
    """Resolve __MSG_key__ placeholders against the extension's _locales."""
    m = re.match(r"^__MSG_(.+)__$", name or "")
    if not m:
        return name
    key = m.group(1)
    raw_path = info.get("path") or ""
    candidates = []
    p = Path(raw_path)
    if p.is_absolute():
        candidates.append(p)
    else:
        ext_root = profile_dir / "Extensions" / ext_id
        if ext_root.is_dir():
            candidates.extend(sorted(ext_root.iterdir(), reverse=True))
    for cand in candidates:
        for loc in ("zh_CN", "en", "en_US"):
            msg = read_json(cand / "_locales" / loc / "messages.json")
            if msg and key in msg:
                return msg[key].get("message", name)
    return name


def extension_records(profile_dir):
    """Return list of extension dicts for one profile."""
    secure = read_json(profile_dir / "Secure Preferences") or {}
    settings = secure.get("extensions", {}).get("settings", {}) or {}
    records = []
    for ext_id, info in settings.items():
        if not isinstance(info, dict):
            continue
        # Skip empty placeholder entries that carry no real extension metadata.
        if "path" not in info and len(info) < 10:
            continue
        manifest = info.get("manifest") or {}
        name = manifest.get("name") or ""
        version = manifest.get("version") or ""
        location = info.get("location")
        reasons = info.get("disable_reasons") or []
        if not isinstance(reasons, list):
            reasons = [reasons]

        disabled = DISABLE_USER in reasons or DISABLE_PERMISSIONS in reasons
        other_reasons = [r for r in reasons if r not in (DISABLE_USER, DISABLE_PERMISSIONS)]

        if not name:
            base = Path(info.get("path") or "").name
            if base and base.lower() != "unpackedextensions":
                name = base + " (未打包)"
            else:
                name = "(空名扩展 %s)" % ext_id[:8]
        else:
            name = resolve_localized_name(name, profile_dir, ext_id, info)

        records.append({
            "id": ext_id,
            "name": name,
            "version": version,
            "location": location,
            "type": LOCATION_NAMES.get(location, "其他(%s)" % location),
            "enabled": not disabled,
            "status": "已停用" if disabled else "已启用",
            "note": "外部/受限(%s)" % ",".join(map(str, other_reasons)) if other_reasons else "",
            "raw_name": manifest.get("name") or "",
        })
    return records


def classify_extension(records, hints, known_ids):
    for r in records:
        lowered = r["name"].lower()
        if any(h in lowered for h in hints):
            return r
    for r in records:
        if r["id"] in known_ids:
            return r
    return None


# ---------------------------------------------------------------- leveldb

def snapshot_copy(src, dst):
    """Copy a LevelDB directory file by file, skipping the locked LOCK file."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name == "LOCK":
            continue  # held exclusively by the browser; empty and not needed
        try:
            if item.is_file():
                shutil.copy2(item, dst / item.name)
        except OSError:
            pass  # in-flight file; skip rather than abort


def ensure_node_env(tools_dir):
    node_modules = tools_dir / "node_modules"
    if (node_modules / "classic-level").is_dir():
        return node_modules

    tools_dir.mkdir(parents=True, exist_ok=True)
    pkg = tools_dir / "package.json"
    if not pkg.exists():
        pkg.write_text('{"name":"edge-data-backup-env","private":true}', encoding="utf-8")

    log("  · 首次运行，安装 classic-level（约数秒）…")
    cmd = ["npm", "install", "classic-level", "--no-audit", "--no-fund", "--loglevel=error"]
    try:
        subprocess.run(cmd, cwd=str(tools_dir), check=True,
                       shell=(os.name == "nt"), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=300)
    except Exception as e:
        raise RuntimeError("npm install classic-level 失败: %s" % e)
    if not (node_modules / "classic-level").is_dir():
        raise RuntimeError("classic-level 安装后未找到目录")
    return node_modules


def dump_leveldb(db_dir, out_json, tools_dir, script_dir):
    node_modules = ensure_node_env(tools_dir)
    env = dict(os.environ)
    env["NODE_PATH"] = str(node_modules)
    proc = subprocess.run(
        ["node", str(script_dir / "dump_leveldb.js"), str(db_dir), str(out_json)],
        env=env, capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "node dump failed").strip())
    with open(out_json, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- exporters

def export_tampermonkey(entries, out_dir, zip_path):
    from pathlib import Path as _P
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = [e for e in entries if e["key"].startswith("!extdb.@source#")]
    used, exported = set(), []
    for entry in sources:
        try:
            obj = json.loads(entry["value"])
        except Exception:
            continue
        code = obj.get("value") if isinstance(obj, dict) else None
        if not isinstance(code, str) or "==UserScript==" not in code:
            continue
        m = re.search(r"//\s*@name(?::[^\s]+)?\s+([^\r\n]+)", code)
        name = m.group(1).strip() if m else "untitled"
        filename = safe_name(name) + ".user.js"
        i = 2
        while filename.lower() in used:
            filename = "%s (%d).user.js" % (safe_name(name), i)
            i += 1
        used.add(filename.lower())
        (_P(out_dir) / filename).write_text("\ufeff" + code, encoding="utf-8")
        exported.append({"name": name, "file": filename, "chars": len(code)})

    if zip_path and exported:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in sorted(out_dir.glob("*.user.js")):
                zf.write(item, arcname=item.name)

    exported.sort(key=lambda x: x["name"].lower())
    return exported


def export_automa(entries, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = next((e for e in entries if e["key"] == "workflows"), None)
    if not raw:
        return []
    try:
        workflows = json.loads(raw["value"])
    except Exception:
        return []
    if not isinstance(workflows, dict):
        return []

    used, exported = set(), []
    for wf_id, wf in workflows.items():
        if not isinstance(wf, dict):
            continue
        data = dict(wf)
        data.setdefault("id", wf_id)
        name = data.get("name") or wf_id
        filename = safe_name(name) + ".json"
        i = 2
        while filename.lower() in used:
            filename = "%s (%d).json" % (safe_name(name), i)
            i += 1
        used.add(filename.lower())
        (out_dir / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        exported.append({
            "name": name,
            "file": filename,
            "trigger": (data.get("trigger") or {}).get("type") if isinstance(data.get("trigger"), dict) else None,
        })

    (out_dir / "_全部工作流(合并).json").write_text(
        json.dumps(list(workflows.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    return exported


def to_int(value):
    """Coerce a JSON number-or-string field to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def esc_html(value):
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def export_bookmarks(profile_dir, out_dir, label):
    bookmarks_file = profile_dir / "Bookmarks"
    data = read_json(bookmarks_file)
    if not data:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        "<!-- Generated by edge-data-backup. Import via edge://favorites -->",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        "<H1>Bookmarks (%s)</H1>" % esc_html(label),
        "<DL><p>",
    ]
    counter = {"n": 0}

    def walk(node, depth):
        pad = "    " * depth
        ntype = node.get("type")
        if ntype == "url":
            lines.append('%s<DT><A HREF="%s" ADD_DATE="%d">%s</A>' % (
                pad, esc_html(node.get("url")),
                to_int(node.get("date_added")) // 1_000_000, esc_html(node.get("name"))))
            counter["n"] += 1
        elif ntype == "folder" and node.get("children"):
            lines.append('%s<DT><H3 ADD_DATE="%d" LAST_MODIFIED="%d">%s</H3>' % (
                pad, to_int(node.get("date_added")) // 1_000_000,
                to_int(node.get("date_modified")) // 1_000_000, esc_html(node.get("name"))))
            lines.append(pad + "<DL><p>")
            for child in node["children"]:
                walk(child, depth + 1)
            lines.append(pad + "</DL><p>")

    roots = data.get("roots") or {}
    for key in ("bookmark_bar", "other", "synced"):
        node = roots.get(key)
        if isinstance(node, dict) and node.get("children"):
            walk(node, 1)
    lines.append("</DL><p>")

    filename = "%s_收藏夹.html" % safe_name(label)
    (out_dir / filename).write_text("\ufeff" + "\n".join(lines), encoding="utf-8")
    return {"profile": label, "count": counter["n"], "file": filename}


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Back up browser profile data without closing the browser")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--browser", default="auto", choices=["auto", "edge", "chrome"])
    parser.add_argument("--profile", default="all", help="'all' or a profile dir name like Default")
    parser.add_argument("--user-data", default=None, help="override the User Data directory")
    parser.add_argument("--tools-dir", default=None, help="cache dir for the node dependency")
    parser.add_argument("--no-zip", action="store_true", help="skip the userscript zip")
    args = parser.parse_args()

    out_root = Path(args.out).resolve()
    script_dir = Path(__file__).resolve().parent
    tools_dir = Path(args.tools_dir) if args.tools_dir else \
        (Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "edge-data-backup-cache")

    if args.user_data:
        user_dirs = [Path(args.user_data)]
    else:
        user_dirs = candidate_user_data_dirs(args.browser)
    if not user_dirs:
        log("未找到浏览器用户数据目录。若浏览器正在运行或安装位置特殊，请用 --user-data 指定。")
        return 1

    log("输出目录: %s" % out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    result = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "profiles": [], "summaries": [], "tampermonkey": [], "automa": []}
    all_ext_records = []

    for user_data in user_dirs:
        log("\n浏览数据目录: %s" % user_data)
        profiles = detect_profiles(user_data)
        if args.profile != "all":
            profiles = [p for p in profiles if p.name == args.profile]
        if not profiles:
            log("  未找到匹配的配置文件")
            continue

        for profile_dir in profiles:
            label = profile_dir.name
            display = profile_label(profile_dir)
            log("\n[%s]%s" % (label, "" if display == label else " / " + display))
            records = extension_records(profile_dir)
            for r in records:
                r["profile"] = label
            all_ext_records.extend(records)

            users = [r for r in records if r["type"] != "内置组件"]
            comps = [r for r in records if r["type"] == "内置组件"]
            result["summaries"].append({
                "profile": label,
                "display": display,
                "user_total": len(users),
                "user_active": sum(1 for r in users if r["enabled"]),
                "comp_total": len(comps),
                "comp_active": sum(1 for r in comps if r["enabled"]),
            })
            log("  扩展：用户 %d/%d 启用，内置 %d/%d" % (
                sum(1 for r in users if r["enabled"]), len(users),
                sum(1 for r in comps if r["enabled"]), len(comps)))

            profile_out = out_root / safe_name(label, 60)
            profile_out.mkdir(parents=True, exist_ok=True)

            # --- bookmarks
            bm = export_bookmarks(profile_dir, profile_out / "收藏夹", label)
            if bm:
                log("  收藏夹：%d 个书签 -> %s" % (bm["count"], bm["file"]))
            else:
                log("  收藏夹：无 Bookmarks 文件，跳过")

            # --- Tampermonkey + Automa via LevelDB snapshot
            jobs = [
                ("篡改猴", classify_extension(records, TM_NAME_HINTS, KNOWN_TM_IDS)),
                ("Automa", classify_extension(records, AUTOMA_NAME_HINTS, KNOWN_AUTOMA_IDS)),
            ]
            for kind, ext in jobs:
                if not ext:
                    log("  %s：未安装，跳过" % kind)
                    continue
                db_dir = profile_dir / "Local Extension Settings" / ext["id"]
                if not db_dir.is_dir():
                    log("  %s：无本地数据目录，跳过（可能从未使用）" % kind)
                    continue

                snap = Path(tempfile.mkdtemp(prefix="edgebk_"))
                try:
                    snap_db = snap / "db"
                    snapshot_copy(db_dir, snap_db)
                    entries = dump_leveldb(snap_db, snap / "dump.json", tools_dir, script_dir)

                    if kind == "篡改猴":
                        target = profile_out / "篡改猴脚本"
                        zip_path = None if args.no_zip else (profile_out / "篡改猴脚本备份.zip")
                        exported = export_tampermonkey(entries, target, zip_path)
                        log("  %s：%d 个脚本%s" % (kind, len(exported), "，已打包 zip" if zip_path else ""))
                        for item in exported:
                            item["profile"] = label
                        result["tampermonkey"].extend(exported)
                    else:
                        target = profile_out / "Automa工作流"
                        exported = export_automa(entries, target)
                        log("  %s：%d 个工作流" % (kind, len(exported)))
                        for item in exported:
                            item["profile"] = label
                        result["automa"].extend(exported)
                except Exception as e:
                    log("  %s：导出失败 - %s" % (kind, e))
                finally:
                    # snapshot may contain session tokens; never leave it behind
                    shutil.rmtree(snap, ignore_errors=True)

            result["profiles"].append({"label": label, "dir": profile_dir.name})

    # --- inventory (all profiles together)
    if all_ext_records:
        _write_inventory(all_ext_records, result["summaries"], out_root)

    result["counts"] = {
        "profiles": len(result["profiles"]),
        "tampermonkey": len(result["tampermonkey"]),
        "automa": len(result["automa"]),
        "extensions_user": sum(s["user_total"] for s in result["summaries"]),
        "extensions_user_active": sum(s["user_active"] for s in result["summaries"]),
    }

    (out_root / "备份清单.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    log("\n" + "=" * 60)
    log("完成：%d 个配置文件 / %d 个篡改猴脚本 / %d 个 Automa 工作流" % (
        result["counts"]["profiles"], result["counts"]["tampermonkey"], result["counts"]["automa"]))
    log("用户扩展：激活 %d / 总计 %d" % (
        result["counts"]["extensions_user_active"], result["counts"]["extensions_user"]))
    log("输出：%s" % out_root)
    return 0


def _write_inventory(records, summaries, out_root):
    """Write 扩展清单.txt and .csv at the output root."""
    out_root.mkdir(parents=True, exist_ok=True)
    with open(out_root / "扩展清单.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["配置文件", "扩展名称", "状态", "版本", "类型", "扩展ID", "备注"])
        writer.writeheader()
        for r in records:
            writer.writerow({"配置文件": r["profile"], "扩展名称": r["name"], "状态": r["status"],
                             "版本": r["version"], "类型": r["type"], "扩展ID": r["id"], "备注": r["note"]})

    L = ["浏览器扩展清单", "导出时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"), "=" * 66]

    def title(summary):
        display = summary.get("display") or summary["profile"]
        return summary["profile"] if display == summary["profile"] else "%s（%s）" % (summary["profile"], display)

    for s in summaries:
        L += ["", "【%s】" % title(s),
              "  用户扩展：  激活 %d / 总计 %d" % (s["user_active"], s["user_total"]),
              "  内置组件：  激活 %d / 总计 %d" % (s["comp_active"], s["comp_total"])]
    L += ["", "-" * 66,
          "合计  用户扩展：激活 %d / 总计 %d   内置组件：激活 %d / 总计 %d" % (
              sum(s["user_active"] for s in summaries), sum(s["user_total"] for s in summaries),
              sum(s["comp_active"] for s in summaries), sum(s["comp_total"] for s in summaries)),
          "-" * 66]
    for s in summaries:
        profile = s["profile"]
        users = [x for x in records if x["profile"] == profile and x["type"] != "内置组件"]
        comps = [x for x in records if x["profile"] == profile and x["type"] == "内置组件"]
        if not users and not comps:
            continue
        L += ["", "=" * 66, "### %s —— 用户扩展" % title(s), "=" * 66,
              "%-6s %-4s %-40s %-16s %s" % ("状态", "序号", "扩展名称", "版本", "类型"), "-" * 100]
        for i, x in enumerate(users, 1):
            L.append("%-6s %-4d %-40s %-16s %s" % (x["status"], i, x["name"][:38], x["version"], x["type"]))
        if comps:
            L += ["", "--- %s 内置组件（浏览器自带，一般不参与迁移）---" % profile]
            for x in comps:
                L.append("  %-4s %-40s %-16s %s" % (x["status"], x["name"][:38], x["version"], x["type"]))
    (out_root / "扩展清单.txt").write_text("\n".join(L), encoding="utf-8-sig")


if __name__ == "__main__":
    sys.exit(main())
