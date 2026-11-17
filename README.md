# myskills

我的 WorkBuddy 技能集合。这里收录日常使用的自定义技能，每个技能一个独立目录，可直接复制到
`~/.workbuddy/skills/` 下安装。

## 技能列表

| 技能 | 目录 | 说明 |
|------|------|------|
| `edge-data-backup` | [`edge-data-backup/`](./edge-data-backup/) | 免关闭浏览器备份 Edge/Chrome 的篡改猴脚本、Automa 工作流、收藏夹和扩展清单 |

---

## edge-data-backup

### 功能

从 Chromium 系浏览器（Edge / Chrome）的配置文件中提取用户数据，导出为可迁移的通用格式：

- **篡改猴（Tampermonkey）脚本** — 每个脚本导出为 `.user.js`，并打包为可导入的 zip
- **Automa 工作流** — 每个工作流导出为独立 JSON，另附合并版
- **收藏夹** — 导出为 Netscape HTML，Edge/Chrome 可直接导入
- **扩展清单** — 名称、版本、类型、启用/停用状态，以及「激活/总计」统计
- **备份清单** — 机器可读的 `备份清单.json`

全过程**不需要关闭浏览器**。LevelDB 在浏览器运行时被独占锁定，技能通过复制数据快照再读取
副本绕过该限制。

### 安装

把 `edge-data-backup/` 整个目录复制到 WorkBuddy 用户技能目录：

```bash
# Windows
xcopy /E /I edge-data-backup "%USERPROFILE%\.workbuddy\skills\edge-data-backup"

# macOS / Linux
cp -r edge-data-backup ~/.workbuddy/skills/
```

安装后**重新打开会话**，技能才会出现在技能列表中。

### 使用

最直接的方式是用自然语言触发，例如：

- 「备份一下本机 Edge 数据」
- 「导出篡改猴脚本和收藏夹」
- 「看看我装了哪些扩展，多少是启用的」

也可以直接运行编排脚本：

```bash
python scripts/backup.py --out "D:/backup"

# 只备份 Edge 的 Default 配置
python scripts/backup.py --out D:/backup --browser edge --profile Default

# 从离线备份目录提取
python scripts/backup.py --out D:/backup --user-data "D:/99.归档/User Data"

# 不生成 zip
python scripts/backup.py --out D:/backup --no-zip
```

首次运行会自动安装 Node 依赖 `classic-level`（缓存到 `%LOCALAPPDATA%\edge-data-backup-cache`），
需要本机有 Node.js 和 npm。

### 输出结构

```
<out>/
├── Default/                      # 每个配置文件一个目录
│   ├── 篡改猴脚本/
│   ├── 篡改猴脚本备份.zip
│   ├── Automa工作流/
│   └── 收藏夹/
├── Profile 2/ ...
├── 扩展清单.txt
├── 扩展清单.csv
└── 备份清单.json
```

### 导入到新设备

- **篡改猴**：管理面板 → 实用工具 → 备份 → 导入 → 选择 zip；或把 `.user.js` 拖入面板
- **Automa**：工作流列表 → 右上角菜单 → Import → 选择 JSON
- **收藏夹**：`edge://favorites` → `...` → 导入收藏夹 → 选择 HTML（追加，不覆盖）

### 实现要点

- **免关浏览器**：跳过 `LOCK` 文件复制整个 LevelDB 目录，读副本而非原目录；`readOnly: true`
  无法绕过打开时的独占锁
- **启用状态判定**：`Secure Preferences` 中没有 `state` 字段，必须解析 `disable_reasons`；
  含 `1`（用户停用）或 `2`（权限增加）即停用，`134217728` 是外部来源标记不算停用
- **敏感数据清理**：Automa 的 `session` 含 access_token，快照在 `finally` 中强制删除
- 更多内部格式细节见 [`references/edge-internals.md`](./edge-data-backup/references/edge-internals.md)

### 范围与限制

支持 Windows 上的 Edge 与 Chrome。不导出 Cookie、密码、历史记录、会话标签页、扩展设置项，
也不保留单个用户脚本的启用/禁用状态。Firefox 使用不同存储格式，不支持。

---

## 目录结构

```
.
├── README.md                      本说明
└── edge-data-backup/
    ├── SKILL.md                   技能元数据与工作流
    ├── scripts/
    │   ├── backup.py              编排器：探测、快照、导出、报告
    │   └── dump_leveldb.js        Node 读取 LevelDB
    └── references/
        └── edge-internals.md      数据格式与实现参考
```
