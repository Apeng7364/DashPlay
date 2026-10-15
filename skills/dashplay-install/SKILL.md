---
name: dashplay-install
description: 帮完全不懂技术的用户把 DashPlay（网页 CarPlay）装到斐讯 N1、玩客云、ZTE F50 或其他 OpenWrt 路由器上，从识别硬件、下载刷机包、刷机、放入用户自己的 CarPlay 认证文件，到上车使用和排错，一步步做完；只有插拔电源、按按钮、在手机或车机上点选这类必须本人动手的事才请用户做。用户说“帮我装 DashPlay”“我买了 N1/玩客云/F50，想用网页 CarPlay”时使用。
---

# DashPlay 装机向导（给 AI 助手）

**刷机风险：会清空选定存储；F50 Root 会降低启动校验，最坏需要拆机短接。先核对型号、备份和具体写入范围，再动手。**

用户把这份文件的地址交给你，意思是：**剩下的事你来做**。你在用户的电脑上执行命令、下载、检查、排错；只在必须本人动手时请用户操作。

**先确认读到的是完整原文**：用终端 `curl -fsSL https://raw.githubusercontent.com/yizhixiaoheigou/DashPlay/main/skills/dashplay-install/SKILL.md` 取原文阅读，不用网页摘要/抓取工具（它们可能给出缓存的旧版或删节版）。原文从「开始前」一直到最后一节「排错」，中间有「斐讯 N1」「玩客云」「ZTE F50 从原厂 B16 到 OpenWrt」「放认证文件（所有设备）」「特斯拉」等章节；缺任何一节就重新获取，不凭残缺内容动手。只依据本文件和它指向的项目文件，不依据这台电脑上碰巧留下的工具、网络设置或其他项目的历史记录来推断步骤。

项目地址：
- 主仓库（玩客云、N1 刷机包，程序本身）：<https://github.com/yizhixiaoheigou/DashPlay>，发布页 `/releases/latest`（补丁版可能不带刷机包，见“下载工具与校验”）
- OpenWrt 软件包（F50 和其他 OpenWrt 路由器）：<https://github.com/yizhixiaoheigou/openwrt-dashplay>，发布页 `/releases/latest`
- 详细参考：主仓库 `docs/DashPlay玩客云固件使用说明.md`（用户手册）、`docs/DashPlay-AI安装说明.md`（认证文件与车机浏览器问题）、openwrt-dashplay 的 `README.md`

## 开始前

- 你需要能在用户电脑上执行命令（Claude Code、Codex 这类）。如果你只能聊天、不能执行命令，告诉用户换一个能执行命令的 AI 助手，或者你把每条命令写出来让他复制执行，并请他把结果贴回来。
- macOS：**不在系统里安装任何东西**（不装 Xcode、Homebrew，不改系统 Python）。下面“下载工具与校验”用 macOS 自带的 `curl`、`tar`、`shasum` 把仓库、一个独立的 Python 3.12 和预编译小工具都放进同一个工作目录，用完整个目录可删。
- Windows 上用 PowerShell：Windows 10/11 自带 `ssh`、`scp`；`.xz` 用下面的 Python 命令解压，不额外下载解压工具。先 `Get-Command python,curl.exe,tar,ssh,scp`，再 `python --version` → 全部存在、Python ≥3.11 → 缺 Python 时说明要装它，用户同意后 `winget install -e --id Python.Python.3.12`，请用户关掉再重新打开 PowerShell 后重新检查；没有 winget、安装失败或缺 `curl.exe`/`tar`（Windows 10 1803 之前）就停下说明。Windows 的下文 `python3` 一律写 `python`。
- 盒子安装通常约 30–60 分钟，F50 备份约十分钟；用户需要准备：设备的电源，N1 还要一个 8 GB 以上的 U 盘，玩客云还要一台 Windows 电脑、一根双公头 USB 线和一把螺丝刀或撬片。

## 与用户相处的规则

1. **用用户的语言，说人话。** 不解释原理，只说现在要做什么、为什么需要他。
2. **需要用户动手时**：单独一行加粗写清楚一个动作（例如“**请把 N1 的电源拔掉，再插回去**”），说明做完会看到什么，然后等他回复。一次只请他做一件事，能自己做的绝不交给他。
3. **先确认再做不可逆的事**：写 U 盘或硬盘、刷机、覆盖盒子内置存储、在用户的路由器上装软件之前，先说清楚会发生什么、会清掉什么，得到同意再做。写磁盘前必须让用户确认目标磁盘（按容量和名称），绝不猜。
4. **CarPlay 认证文件**（`identity.pk8` 和 `certificate.p7b` 两个文件）：用户有就用用户的；没有就由你从 DiPlay 的发布包里取（步骤见“放认证文件”），先用一句话告诉用户这是很多人共用的实验身份、苹果可能让它失效、只供自己用。认证文件和任何密码不要打印到屏幕、不要上传到任何地方，电脑副本留在用户指定的私有目录；未经用户授权不要删除。
5. 盒子的 SSH 默认用户名 `root`、密码 `1234`。装好后可以提议用户改密码（`passwd`），但不强制。
6. 出错先自己查日志、排查（见“排错”），不要把报错原样丢给用户。
7. **要电脑密码的命令**（如写 U 盘的 `sudo dd`）：你的命令环境通常没法输入密码。先说明这条命令做什么，用户同意后把完整命令单独给他，请他在「终端」App 里粘贴执行并输入密码，完成后你再核对结果。不要让用户把密码发给你。

## 第 0 步：问清楚

按 **Q1 → Q2 → Q3 → Q5 → Q6 → Q7 → Q4** 一次问一个，已有明确答案就沿用。先事实和需求，最后偏好；用户答不上设备就帮助辨认。回答阶段不改设备配置。先确认车机有浏览器、能连 WiFi；任一不具备就说明当前路线用不了，不继续刷机。

1. **Q1 买的是哪种设备？** N1 / 玩客云 / F50 / 已有 OpenWrt 路由器，决定下面的安装路线。
2. **Q2 什么品牌和车型？** 记下答案，用于套用已知适配，不以品牌划分网络路线。特斯拉启用专用模式：共享地址段、仅在方式三无上游时代答联网检查、显示全屏按钮；已知它不能开热点。未知车型按实际能力确认，不因为“不肯连无网 WiFi”就盲开特斯拉模式。
3. **Q3 车机能自己开热点吗？** N1 / 玩客云先问这一件事：能 → 方式一，盒子连车机热点（无线经车机中转实测较卡，建议有线；当前正式选项只提供有线）。不能、只能连 WiFi → 下一问「有没有随身 WiFi？」；有 → 方式二，盒子、车机、iPhone 都连它；没有 → 方式三，车机连盒子热点，再问 Q5。已知车型的能力直接说明并沿用，不重复追问。特斯拉的随身 WiFi 必须支持把 LAN 改到 `100.96.0.1/24`，不支持就引导方式三，不能自动改用户的路由器。方式一、二再问热点名、让用户在设置页输入密码；方式三设置盒子热点名和密码。
   **F50 / OpenWrt 改问「插了能上网的 SIM 卡吗？」** F50 插卡则用 SIM；没有则进入 Q5。普通 OpenWrt 路由器同样核实其真实 WAN 来源，不把以太网误当 SIM。F50 使用无线 CarPlay；其他路由器有线取决于 USB 主机口。
4. **Q5 连着盒子时，车机自己还要能上网吗？** 所有车在方式三无上游或 F50 无卡时都问；B 是通用功能，不以特斯拉开关为条件。
   - 不需要：特斯拉走方案 A，其他车保持普通无网热点，不代答联网检查。对特斯拉明确说「连着 DashPlay 时，特斯拉自带音乐、地图不能联网，CarPlay 正常，iPhone 用自己的流量」。特斯拉不会因此转去用 4G。
   - 需要：N1 / 玩客云可借手机热点（方案 B），让用户输入热点名、密码，再问「开热点的是跑 CarPlay 的同一部 iPhone，还是另一部手机？」。手机需一直开热点，车机消耗手机流量；找不到或掉线自动回不上网（特斯拉 A，其他车维持普通热点）。同一部 iPhone 开热点时，有线、无线 CarPlay 都可，10-15 无线在家实测通过，车机侧待复测。选有随身 WiFi则返回 Q3 方式二。
   - **F50 无卡只有「不需要上网」**：当前驱动不能同时连别人的热点和开自己的热点，不提供 B；要上网就插能用的 SIM 卡。也不能让特斯拉直接连手机热点访问 F50（手机热点私网地址被拦）。
5. **Q6 用自己的网址吗？** 特斯拉必问；其他车在打不开画面时问。推荐免费 DuckDNS 正式证书，这是特斯拉已验证的路线；说明「回家给盒子插网线，申请一次证书」，之后按期联网续签。用户愿意时按下文带他注册、自己填 token；不愿意可试 IP，但证书警告有没有「继续」尚未实车确认，不保证能用。
6. **Q7 音乐、通话、Siri 走车机蓝牙吗？** 特斯拉默认是；其他车按需。偏好不确定可选「都行」，按车的默认值，说明要把 iPhone 蓝牙连车机并选为媒体来源。
7. **Q4 iPhone 用数据线还是无线？** 最后问，默认「都行，能用就行」，只展示前面条件允许的选项：方式一只能有线；方式二、A、B 可选有线/无线/都行（B 同一 iPhone 无线在家通过，另一手机组合未测）；F50 选无线。两种都可且选都行时，插线用有线、拔线用无线。说明是哪一需求排除了无线，允许返回修改该需求。会用无线时必须添加 iPhone，只用有线可跳过。

**能力分支核对**：与教程选择图一致：浏览器 + WiFi → 设备 → F50 的 SIM，或 N1 / 玩客云的「能开热点 → 方式一；不能 → 有随身 WiFi → 方式二；没有 → 方式三 → 要不要上网」。品牌只补已知适配。未知车型另外确认是否接受无网 WiFi、浏览器能否打开局域网地址、出 P 挡是否断 WiFi；遇到失败按下面 ①～⑧ 定位，不先让用户做全套改动。维护本 Skill 时对照设计仓库 `docs/视频教程规划-20261015.md` 图 2（安装用户不需要这份内部文档）。设计 5.2a 的盒子直连热点做成并实测前，保留方式一建议有线；通过后再开放无线，不把手机热点实验当作车机热点验收。

**配置总结，确认或返回修改后才生效**：逐项列出答案及网段、盒子热点、联网检查、车机上网来源、iPhone 连接与声音；列出代价和下面的实测状态。不要把「都行」默认为跳过无线配对。总结确认不替代刷盘等具体破坏性动作的授权。

| 设备 / 组合 | 验证状态 |
|---|---|
| F50 插卡 + 特斯拉 | 2026-10-14 实车通过 |
| F50 无卡 + 特斯拉 A | 本轮已实现，待实测 |
| F50 无卡 B | 不支持：驱动 managed 与 AP 合计最多一个 |
| N1 / 玩客云 + 特斯拉 A | N1 2026-10-15 手工方案有线/无线通过；正式版本待复测，玩客云未测 |
| N1 / 玩客云 + 特斯拉 B | N1 手工方案有线实车通过，同一 iPhone 热点 + 无线在家通过；正式功能需复测，2.4 GHz 与另一手机热点 + 无线未测 |
| N1 / 玩客云 + 特斯拉方式二 | 未实车验证，照常配置并记录结果 |
| 其他车方式一 / 二 / 三 | 按该车型已有记录，无记录写未测 |

确认后再逐个核实安装前置：电脑系统（玩客云刷机需 Windows）、认证文件是否已有及私有位置。然后按设备章节执行，先用一句话说明安装步骤与需要用户插拔的动作。

## 下载工具与校验（电脑上先做）

命令中的 `<IP>`、`<日期>`、`<版本>`、`<架构>` 是占位符：用实际检测结果替换，不照着尖括号执行。每个小步骤只有“应该看到什么”满足后才能继续；不符先按该项退路处理。

```sh
INSTALL_BASE="$HOME"  # 可改成用户指定且已挂载的工作盘目录
WORK=$(mktemp -d "$INSTALL_BASE/dashplay-install.XXXXXX")
chmod 700 "$WORK"
curl -fsSL https://github.com/yizhixiaoheigou/DashPlay/archive/refs/heads/main.tar.gz | tar -xz -C "$WORK"
DP="$WORK/DashPlay-main"
```

→ `$DP/scripts/fetch-install-assets.py` 存在 → 网络失败保留目录重试；文件不存在说明拿到的公开版本尚未包含本向导，停下，不从无关仓库拼工具。不需要 `git`。

**macOS 接着执行** `sh "$DP/scripts/mac-bootstrap.sh" "$WORK"` → 最后一行 `MAC_READY`，倒数第二行是一句 `export PATH=...` → 执行这句 `export`，然后 `command -v python3` 应在 `$WORK/python/bin`。它下载固定版本的独立 Python 3.12（[python-build-standalone](https://github.com/astral-sh/python-build-standalone)，SHA-256 写在脚本里）解到 `$WORK/python`；Apple Silicon 上再按 `SHA256SUMS` 核对仓库自带的 `tools/mac-arm64/spd_dump`、`lz4`（构建方法与源码见 `scripts/build-mac-tools.sh`）并加进 PATH。下文的 `python3`、`spd_dump`、`lz4` 都指这些，不用、也不去装系统里的同名程序。输出 `STOP` 就停下：不改用 Homebrew、系统 Python 或别处下载的版本。你的命令若每次在新 shell 里执行，每条命令前都先执行这句 `export`。

Windows 不执行上面的 sh 片段，改用：

```powershell
$WORK = Join-Path $env:USERPROFILE ('dashplay-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $WORK | Out-Null
curl.exe -fsSL -o "$WORK/dashplay.tar.gz" https://github.com/yizhixiaoheigou/DashPlay/archive/refs/heads/main.tar.gz
if ($LASTEXITCODE -ne 0) { throw 'download failed' }
tar -xzf "$WORK/dashplay.tar.gz" -C "$WORK"
if ($LASTEXITCODE -ne 0) { throw 'extract failed' }
$DP = Join-Path $WORK 'DashPlay-main'
Test-Path "$DP/scripts/fetch-install-assets.py"
```

→ 最后一行 `True` → 否则停止。下文 `$WORK`/`$DP` 在 PowerShell 同样可用；不要把 sh 的 `export`、`chmod`、反引号续行或 heredoc 直接贴进 PowerShell。每条外部命令检查 `$LASTEXITCODE` 为 0 才继续。

Mac 的 N1/F50 ADB 前置：先 `command -v adb; command -v fastboot` → 两者存在；缺少时执行 `python3 "$DP/scripts/fetch-install-assets.py" adb-mac "$WORK/adb"`，再 `unzip "$WORK/adb/platform-tools-darwin.zip" -d "$WORK/adb"`、`export PATH="$WORK/adb/platform-tools:$PATH"`、`adb version`、`fastboot --version` → 摘要通过、版本命令成功 → 否则停止。Linux/Windows 原厂 N1 路线需已有可工作的 ADB/fastboot；没有则停下明确换用本段支持的 Mac，不能临时找不明驱动包。

解压镜像：把 `$IMAGE_XZ` 设为下载的 `.xz` 绝对路径，执行 `python3 -c "import lzma,shutil,sys; p=sys.argv[1]; a=lzma.open(p,'rb'); b=open(p[:-3],'xb'); shutil.copyfileobj(a,b); b.close(); a.close()" "$IMAGE_XZ"`（Windows 用 `python`）→ 退出 0、同目录出现去掉 `.xz` 后缀的镜像 → 失败保留现场，不拿部分文件刷写；目标已存在时先核对来源，不覆盖用户文件。

固定前置下载的项目作者地址与 SHA-256 在 [downloads.json](downloads.json)（与 skill 同目录）；`python3 "$DP/scripts/fetch-install-assets.py" <组名> <目录>` 下载并逐项验证，输出 `SHA256_OK` 才可执行。`n1` 是社区保存的 2.19 分区镜像，`onecloud` 是教程作者保存的 Amlogic 工具，均**不是厂商认证的官方分发**；不要隐瞒来源。哈希用于固定本次审阅的文件，不等于厂商签名。用户要求只用厂商官方镜像时停下问用户，这两项没有已核实可用的厂商官方直链。

DashPlay 的镜像/程序包从本项目官方 GitHub Release 下载，按该版本同名 `.sha256` 校验；OpenWrt 包使用同版本 `SHA256SUMS`。先选定一个版本并记录 tag、下载 URL 和完整 SHA-256，再下载，不混合 latest 在不同时间的资产。校验不一致重新下载一次；仍不一致停止。macOS/Linux：在下载目录 `shasum -a 256 -c <校验文件>` → 每个目标 `OK`；Windows：`Get-FileHash -Algorithm SHA256 <文件>` → 与官方校验文件完整 64 位相同。无校验文件/发布 API `digest` 时停下，不以自己下载后算出的摘要充当独立核对。

N1/玩客云刷机包：只改程序的补丁版不带刷机包（发布说明写明沿用哪一版），所以不要只看 latest，用这条命令找最近一个带它的正式版（玩客云把 `DashPlay_n1-` 换成 `DashPlay_onecloud-`）：
`python3 -c "import json,sys,urllib.request as u; p=sys.argv[1]; rs=[r for r in json.load(u.urlopen('https://api.github.com/repos/yizhixiaoheigou/DashPlay/releases?per_page=30',timeout=60)) if not r['draft'] and not r['prerelease']]; r=next(r for r in rs if any(a['name'].startswith(p) for a in r['assets'])); print('IMAGE', r['tag_name'], 'NEWEST', rs[0]['tag_name']); print('\n'.join(a['browser_download_url'] for a in r['assets'] if a['name'].startswith(p)))" DashPlay_n1-`
→ 第一行 `IMAGE <刷机包版本> NEWEST <最新正式版>`，下面两行是镜像和 `.sha256` 地址，记下两个版本 → 网络失败重试；没有输出地址就停下。两个版本不同时，盒子刷好、首次设置并出画后，按「升级已装好的盒子 → N1 / 玩客云」换上 NEWEST 版本的程序包（盒子是新装的，备份一样要做）。

## 升级已装好的盒子（保留首次设置、配对、认证和热点密码）

先 `ssh root@<盒子IP> 'test -f /etc/openwrt_release && cat /etc/openwrt_release || cat /etc/os-release'` → OpenWrt 走 apk，Armbian/Debian 的 N1/玩客云走程序包。**升级不要重刷整机镜像、不要再跑 install.sh、不要清空 state/identity。** 新版声明系统依赖变化时先停下核对迁移说明，不能把程序升级当系统升级。

### N1 / 玩客云

1. 优先插网线，通过以太网地址 SSH。下载同一发布版本 `dashplay-bundle-<版本>.tar` 和 `.sha256`，校验 `OK` 后将变量 `BUNDLE` 设为该 tar 的绝对路径。先在电脑备份：
   ```sh
   umask 077
   ssh root@<盒子IP> 'tar -czf - -C /opt dashplay' > "$WORK/dashplay-before-upgrade.tar.gz"
   gzip -t "$WORK/dashplay-before-upgrade.tar.gz"
   tar -tzf "$WORK/dashplay-before-upgrade.tar.gz" > "$WORK/backup-files.txt"
   ```
   → SSH/tar/gzip 均退出 0，清单包含 `dashplay/state/setup.json`、`dashplay/identity/` 和已有配对文件 → 失败停止，不能用零字节文件冒充备份。备份内有密码和认证，只保留本机私有目录。
2. 从已校验包提取升级器：`mkdir "$WORK/upgrader"`；`python3 -c "import tarfile,sys,pathlib; t=tarfile.open(sys.argv[1]); m=next(x for x in t.getmembers() if x.name.removeprefix('./')=='firmware/upgrade.py' and x.isfile()); pathlib.Path(sys.argv[2]).write_bytes(t.extractfile(m).read())" "$BUNDLE" "$WORK/upgrader/upgrade.py"`；`scp "$BUNDLE" root@<盒子IP>:/tmp/dashplay-bundle.tar`；`scp "$WORK/upgrader/upgrade.py" root@<盒子IP>:/tmp/upgrade.py` → 上传成功、远端 `sha256sum /tmp/dashplay-bundle.tar` 与发布摘要一致 → 不同停止重传。
3. `ssh root@<盒子IP> 'python3 /tmp/upgrade.py /tmp/dashplay-bundle.tar --dry-run'` → 只列程序替换/删除，不能出现 state/identity/service.json → 异常停止。实际执行：
   `ssh root@<盒子IP> 'systemd-run --unit=dashplay-upgrade --collect python3 /tmp/upgrade.py /tmp/dashplay-bundle.tar'`
   → 升级在独立 systemd 任务运行，SSH 断开不会杀掉它；WiFi 会重启。1–2 分钟后重连同一地址并看 `cat /opt/dashplay/state/upgrade.log; systemctl is-active dashplay` → `upgrade done; service up` 和 `active`，设置页显示已设置而非首次引导，原热点密码能连接。
4. 用升级前备份与升级后的文件比对：`state/setup.json`、`state/options.json`（原来存在才比）、`service.json`、identity 两文件和 `state/receiver/*pair*`。Java `.properties` 文件可能只刷新 `#` 注释中的时间；忽略注释后键值必须相同。日志、缓存不要求相同。实际让已配对 iPhone 连接并在电脑打开 `https://<盒子IP>:18443/` → 出真实画面才记“出画通过”；`sessionActive=false` 或仅设置页成功不能算出画。
5. 失败：先读 `state/upgrade.log` 和 `state/logs/<本次启动>/receiver.log`。检查失败/服务起不来时脚本会尝试自动恢复上一版程序；手动回退：`ssh root@<盒子IP> 'systemd-run --unit=dashplay-rollback --collect python3 /tmp/upgrade.py --rollback'` → 再核对服务和画面。`/opt/dashplay-previous.tar` 只保留上一版程序，不替代电脑里的完整数据备份；回退失败停止，不重新刷机覆盖数据。

### F50 / OpenWrt（apk）

1. 电脑备份：`ssh root@<路由器IP> 'tar -czf - /opt/dashplay /etc/config/dashplay /etc/config/wireless /etc/config/network /var/lib/bluetooth' > "$WORK/openwrt-before-upgrade.tar.gz"`；`gzip -t "$WORK/openwrt-before-upgrade.tar.gz"` → 成功；不存在的蓝牙目录先确认实际路径再调整列表，不能忽略 tar 失败。F50 再执行 `ssh root@<路由器IP> 'tar -czf - /opt/dashplay-bluez' > "$WORK/f50-bluez-before-upgrade.tar.gz"`、`gzip -t "$WORK/f50-bluez-before-upgrade.tar.gz"` → 均成功，否则停止。备份只留用户电脑，不打印 UCI 中的密码/token。
2. 读取 `cat /etc/apk/arch; apk info -v dashplay dashplay-jre luci-app-dashplay` → 记住版本与架构。下载**更新版本同一 Release**的 dashplay、对应架构 dashplay-jre、两个 LuCI apk 与 `SHA256SUMS`，校验全过。传到新建的 `/tmp/dashplay-upgrade/`，该目录只放本次四个包。
3. 在路由器上执行 `apk add --upgrade --simulate --allow-untrusted /tmp/dashplay-upgrade/*.apk` → 只升级上述用户态包及必要用户态依赖；出现内核、kmod、系统 BlueZ 的计划就停止。F50 不能向 5.4 系统装 6.12 模块；保留独立 BlueZ。确认计划后执行相同命令去掉 `--simulate`，再 `/etc/init.d/dashplay restart`。
4. `apk info -v dashplay luci-app-dashplay; /etc/init.d/dashplay status` → 新版本、running；LuCI 打开/刷新、设置保留、iPhone 无需重新配对、真实出画。包明确不拥有 state/identity，`/etc/config/dashplay` 是保留配置；如出现 `.apk-new`，先比较新增选项，只合并所需默认值，不覆盖现有秘密。升级失败保留日志和备份，诊断依赖或用保存的上一版 apk；不要 `apk del` 再装、不要 sysupgrade。

## 斐讯 N1

N1 有内置 WiFi 和蓝牙，iPhone 的数据线插 N1 的任意 USB 口。

1. **下载**：按“下载工具与校验”找到最近带刷机包的版本，下载 `DashPlay_n1-<版本>.img.xz` 和同名 `.sha256`，校验并解压出 `.img`。
2. **写 U 盘**（8 GB 以上，会清空）：
   **请把一个不用的 U 盘插到电脑上。**
   - macOS：`diskutil list external` 找到 U 盘（核对容量），请用户确认是 `/dev/diskN`；然后 `diskutil unmountDisk /dev/diskN` 并执行 `sudo dd if=DashPlay_n1-<日期>.img of=/dev/rdiskN bs=4m`（要电脑密码；你的终端输不了密码时按规则 7 请用户在「终端」App 执行，下面的读回核对同样处理）。`dd` 退出 0 后，把下列代码保存为 `$WORK/check-usb.py`，执行 `sudo python3 "$WORK/check-usb.py" <镜像绝对路径> /dev/rdiskN` → `USB_READBACK_OK` 才执行 `diskutil eject /dev/diskN`；读取错误或不一致停止，不拿它启动盒子。
     ```python
     import sys
     with open(sys.argv[1], 'rb') as src, open(sys.argv[2], 'rb') as disk:
         while data := src.read(4 * 1024 * 1024):
             if disk.read(len(data)) != data:
                 raise SystemExit('STOP: USB readback differs')
     print('USB_READBACK_OK')
     ```
   - Linux：`lsblk` 找到 U 盘并确认，`sudo dd if=… of=/dev/sdX bs=4M conv=fsync status=progress`。
   - Windows：没有安全的命令行方式，请用户安装 balenaEtcher（或 Rufus），你告诉他选哪个文件、哪个 U 盘。
3. **从 U 盘启动**。先读用户已经说明的起点：README 的三句补充分别对应已有 Linux、斐讯原厂 ≤2.19、斐讯原厂 >2.19，已明确就直接走对应分支，不再重复问系统类型。只说“原厂高于 2.19”但没给精确版本时，只补问版本号；两条原厂路线都注明“来自公开实践，本项目未实测”。起点没说明时才问：**现在开机看到的是 Linux（Armbian/OpenWrt）、斐讯原厂界面、安卓电视桌面，还是根本起不来？** 不把所有“刷过系统”都当成 Linux。
   - **Linux**（2026-10-14 实测：已有 Armbian、内核 6.12.111-ophub）：先记录当前可用的 SSH 地址、登录方法和网卡 MAC；不能登录就停下补齐，不能假设旧系统密码也是 1234。
     **请拔掉 N1 的电源，把 U 盘插到靠近 HDMI 的那个 USB 口，再接上电源，网线保持连接。**
     → 等待约 2 分钟。有 DashPlay 热点时按第 4 步连接；没有时用刚记录的旧系统以太网 SSH 地址和登录方法执行 `findmnt -no SOURCE /`，不要求旧系统先出现 DashPlay 热点。两种方式都不能登录则停下核对路由器租约与网线。若仍是旧 Linux 的 `/dev/mmcblk…`，先在旧系统执行 `lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT`，确认已出现与获准 U 盘容量一致的 `/dev/sd…`。U 盘不存在则停下检查插口/盘；U 盘存在则保留它，执行一次 `sync; reboot` 热重启，再按第 4 步核对根目录。仍进 eMMC 则停下保存 `findmnt`、`lsblk`、`cat /proc/cmdline`，不清空内置存储来强迫启动。
     实测这只 Cruzer Glide 冷启动先进入旧 eMMC，热重启后根目录为 `/dev/sda2`、空间自动扩展、DashPlay 热点与首次设置正常；真实 iPhone 出画待测。该结果不代表所有 U 盘都需要或都能靠热重启解决。
   - **安卓电视系统**（未实测）：记录系统名/版本，用户在系统自己的开发者设置中打开 ADB；AI 用 `adb connect <实际IP>:5555`、`adb -s <实际IP>:5555 shell getprop ro.product.model` 核对设备。ADB 不可用、型号无法确认就停下，不能套用原厂“四次点击版本号”或盲刷降级包。ADB 可用时只尝试下方原厂步骤 6 的 `reboot update` 引导；回到电视桌面、权限拒绝或 2 分钟内无 DashPlay 启动证据就停下，记录系统版本和输出。
   - **起不来/砖机**（未实测、不支持）：可能需要拆机短接恢复；本向导不执行这条路线，停下说明需要专门维修或已验证的恢复方案，不反复换包。
   - **斐讯原厂系统**（≤2.19、高于 2.19 均未实测；不能把公开教程当成本项目验收）：准备 HDMI 显示器、USB 鼠标；降级另需双公头 USB 数据线。第一次开机先不接互联网，避免自动升级。本项目 N1 已实测的是已有 Linux 的机器，以下原厂前置步骤来自公开实践，尚未用原厂 N1 实测。
     1. **请接显示器和鼠标，给 N1 供电，把首页固件版本告诉我。** → 应为 N1 原厂版本 → 型号不是 N1、显示不清或定制版，停下问用户。
     2. **请在固件版本上连续点 4 次，看到“打开 adb”后给 N1 接到电脑同一局域网。** 最好用无互联网的局域网；不要擅自改用户路由器防火墙。先从 N1 首页/网络信息读取它的局域网 IP，作为本段 `<IP>`；不能确定哪台是 N1 就停下核对屏幕，不猜地址。电脑 `adb connect <IP>:5555`，`adb -s <IP>:5555 shell getprop ro.product.model` → `connected`、N1 → 未连上先核对 IP/同网和 ADB 开关，不能直接发 fastboot 写入。
     3. 版本 ≤2.19 跳过降级；>2.19 保守按需要降级处理（早期作者记载 >2.22，2.20–2.22 若无法核对则停下问用户）。下载：`python3 "$DP/scripts/fetch-install-assets.py" n1 "$WORK/n1-downgrade"` → 三项 `SHA256_OK`。来源为 [dreamrover 的镜像和命令](https://github.com/dreamrover/phicomm-n1/tree/e9007301fcfc5beb2c2da51dab9e810cbb509322/firmware_2.19)，作者实际操作记录见 [HouCoder](https://github.com/HouCoder/blog/blob/master/hacking/install-armbian-on-phicomm-n1/README.md)。不是完整原厂线刷包，不可拿 T1 镜像代替。
     4. 获得清楚的降级授权后，**请用双公头 USB 线连接电脑与 N1 靠 HDMI 的 USB 口。** 执行 `adb -s <IP>:5555 shell reboot fastboot`，再 `fastboot devices` → 恰好一台设备标为 fastboot → 没设备先查数据线/驱动，多台先让用户断开无关设备，禁止猜序列号。
     5. 在 `$WORK/n1-downgrade` 依次执行 `fastboot flash bootloader bootloader.img` → Sending/Writing 均 OKAY；`fastboot flash boot boot.img` → 均 OKAY；`fastboot flash recovery recovery.img` → 均 OKAY；**任一步失败即停止，不继续写，不换包盲试。** 全过再 `fastboot reboot`。降级只换三个引导分区，界面版本可能不变，不以界面仍显示高版本判为失败。
     6. 原厂重新起来后，拔掉双公头线，重新 `adb connect <IP>:5555`。**请把已写好的 U 盘拿在手里，靠 HDMI 的 USB 口先留空。** 执行 `adb -s <IP>:5555 shell reboot update` 后单独提示用户立刻插 U 盘 → 出现 DashPlay WiFi 后先做下一步连网，再用 SSH 验收；不能只看斐讯 Logo。2 分钟还只有原厂后台：保留屏幕/启动状态，查 U 盘校验、端口与引导版本，不循环乱刷。
   第一次从 U 盘开机约 1–2 分钟，会出现名叫 `DashPlay` 的 WiFi（没有密码）。
4. **连上盒子**：让电脑连 WiFi `DashPlay`（macOS 可以用 `networksetup -setairportnetwork <WiFi 接口> DashPlay`，接口名用 `networksetup -listallhardwareports` 查；先问用户可不可以切换电脑的 WiFi；切换后电脑暂时不能上网，所以下载都要在这之前做完）。盒子地址固定 `192.168.78.1`：`ssh root@192.168.78.1`（密码 `1234`）。
   再执行 `ssh root@192.168.78.1 'findmnt -no SOURCE /; test -d /opt/dashplay && echo DASHPLAY_PRESENT'` → 根设备为 `/dev/sd…` 的 USB 分区、`DASHPLAY_PRESENT` → 若是 `/dev/mmcblk…` 或目录不存在，不能宣称 U 盘启动成功，先查启动来源，不运行内置安装。
5. **装进内置存储**（未完成本轮实测；以后不用插 U 盘，会清空 N1 原来的系统）：先完成 USB 路线的首次设置与真实 iPhone 出画，再执行 `findmnt -no SOURCE /; lsblk -o NAME,SIZE,MODEL,FSTYPE,MOUNTPOINT` → 根目录仍在 USB、内置 eMMC 与 USB 的名称容量明确区分 → 备份内置系统及用户数据到电脑并核对，告诉用户具体 eMMC 目标/容量/覆盖范围，取得当次同意。目标不清、备份失败或用户暂缓出画时停在这里，不提前写内置盘。
   再执行
   `ssh root@<当前已验证的盒子IP> "printf '101\n1\n' | armbian-install"`（机型 101 = Phicomm N1，文件系统 1 = ext4）。看到成功后：
   **请拔掉 N1 的电源，拔下 U 盘，再接上电源。**
   保留首次设置时的热点名称/密码及当前 SSH 地址，不能假设仍是无密码 DashPlay 或 192.168.78.1。约 1–2 分钟后连已保存的热点（方式一/二连原上游 WiFi），使用当前地址 SSH；方式三开特斯拉后地址是 100.96.0.1，方式一/二 DHCP 地址变化则从上游路由器租约中按已记录 MAC 重新确认。连不上就停下，不盲目再次安装。用户想一直从 U 盘运行也可以跳过这一步。
6. USB 验收先去“放认证文件”和“首次设置与上车”；通过后若用户选择安装 eMMC，返回第 5 步。eMMC 启动后再次 `findmnt -no SOURCE /` → `/dev/mmcblk…`，然后复验设置保留及真实出画；不以热点出现代替。

## 玩客云

玩客云要配一个 RTL8821CU 的 USB 无线网卡（WiFi 和蓝牙二合一，USB ID `0bda:c820`），**必须用 12 V 电源**（5 V 的 USB 转 DC 线会让网卡反复掉线）。刷机用 Amlogic 的 USB Burning Tool，**只能在 Windows 上运行**；用户没有 Windows 电脑时，请他借一台或用虚拟机，你远程指导。

1. **下载**：按“下载工具与校验”找到最近带刷机包的版本，下载 `DashPlay_onecloud-<版本>.burn.img.xz` 和 `.sha256`，校验并解压出 `.burn.img`。
2. **刷机**（会清空玩客云）：`python3 "$DP/scripts/fetch-install-assets.py" onecloud "$WORK/onecloud-tools"` → `SHA256_OK USB_Burning_Tool_v2.1.3.exe`。这是 [苏洋原作者实机教程](https://soulteary.com/2025/01/08/lightweight-server-under-fifty-yuan-wanke-cloud-speed-running-guide-1.html) 所附工具（下载地址与 SHA-256 在 downloads.json），不是 Amlogic 官网直链。Windows 安装此工具及附带驱动，导入 DashPlay `.burn.img`，勾选擦除 flash/bootloader（这是整机包），不要导入 N1 镜像。
   - 以前刷过 Armbian 等系统的玩客云，也照这样线刷（DashPlay 刷机包整片覆盖）。
   - 原厂机通常需要拆壳短接，已刷过可进烧录模式的机器不一定要再次短接。按上述原作者的 [整板照片](https://attachment.soulteary.com/2025/01/08/boards.jpg) 和 [短接标注图](https://attachment.soulteary.com/2025/01/08/short-circuit.jpg) 逐点核对：V1.0 是无 SD 槽旁 V1.3 标记的旧板，短接点在背面白色线框附近；V1.3 的 SD 槽旁有明显 V1.3，点在带主芯片一面 RX 标记附近未焊焊盘。只凭“附近”不能动手：AI 打开原文两幅对应板型图片，与用户主板照片核对两点。图打不开、点位不一致、丝印不是这两种或用户不会认，**停下问用户要清晰板照**；不要猜引脚，更不能让用户“自己查好再试”。
   **请用双公头 USB 线把玩客云远离网口的那个 USB 口（刷机口）接到电脑，短接主板上的两个触点，同时接上电源；工具里出现设备后松开，点「开始」。**
   → 工具必须显示 `100%: Burning successfully` → 失败保留错误码和进度，停止，不反复短接/换固件。成功后点停止，再拔 USB 与电源。
3. **第一次开机**：
   **请把 USB 无线网卡插到玩客云靠近网口的 USB 口（不是刚才刷机用的口），接上 12 V 电源。**
   第一次开机会自动扩展存储，约 20 秒后出现 WiFi `DashPlay`（没有密码），蓝灯慢闪。iPhone 的数据线以后插远离网口的 USB 口（就是刷机口，用于有线 CarPlay）。
4. 连上盒子同 N1 第 4 步（地址 `192.168.78.1`，root / `1234`）。也可以给玩客云插网线到路由器：它的网口 MAC 每次开机随机，地址会变，在路由器里找主机名 `onecloud`，或扫局域网找能用 root/1234 登录且有 `/opt/dashplay` 的设备。
5. 去“放认证文件”，再去“首次设置与上车”。

## ZTE F50 从原厂 B16 到 OpenWrt（先做，再装下节插件）

**风险先说清楚：这条路线会降低启动校验；断电、版本不符或写错可能变砖，最坏需要拆机短接。Root 阶段的 TCP ADB 实测不验证客户端身份，即使 `ro.adb.secure=1` 也不能当成已认证；只在隔离的电脑直连网络操作，结束后留在 OpenWrt。** 先取得针对这台设备、SD 卡清空及启动分区的授权。其他设备的 NV、IMEI、基带、GPT 绝不能写入。

准备：F50、可清空的 8 GB 以上 SD 卡、能传数据的 USB 线、电脑至少 15 GB 空闲空间。下面的主机命令已按 Apple Silicon Mac 整理（`uname -m` 应为 `arm64`；Intel Mac 停下问用户：预编译的 spd_dump、lz4 和固定的 Python 依赖只有 arm64 版本）；Windows/Linux 若没有已验证的 USB/编译环境，停下问用户换到 Mac，不擅自套用 Windows 一键 root 脚本。F50 有两个 USB 口，使用能在电脑上打开原厂后台的那个；另一口无枚举时先换口，不当成设备坏了。

### F0 识别与下载

开始前检查**实际备份目录所在磁盘**：`python3 -c 'import shutil,sys; n=shutil.disk_usage(sys.argv[1]).free; print("FREE_BYTES",n); sys.exit(0 if n >= 15_000_000_000 else 1)' "$WORK"` → 退出 0 → 才能做备份。退出非 0 时不要清理用户文件：用 `df -h` 找用户允许的工作盘，把 `INSTALL_BASE` 改为该盘内的目录，重新 `mktemp -d "$INSTALL_BASE/dashplay-install.XXXXXX"` 建 0700 私有 `WORK` 并重做空间检查。已有 `DP` 继续指向原来的工具目录，不搬走或覆盖既有文件；随后重新设置 `F50_WORK="$WORK/f50"`。无足够空间则停下，让用户提供备份盘。

1. 电脑通过 USB 打开 `http://192.168.0.1/`，让用户在原厂后台登录，读取型号与完整固件版本 → 必须为 **ZTE F50、F50_FLYMODEM_ZYV1.0.0B16** → U30 Air、其他运营商定制版、非 B16 都不继续本节，按下一步处理。不要用文件名代替后台版本。**不需要用户在后台打开 USB 调试（`#usb_port` 等页面）**：本路线备份和 Root 都走断电重接的下载模式，ADB 由写入的 Root 镜像在启动后自己打开；不要让用户去开，也不要因为它关着就停下或改走别的路线。
2. 非 B16 的调查结论（2026-10-14）：[Xio-Shark 原作者](https://github.com/Xio-Shark/f50-b16) 明确只支持 B16；[zte-f50-toolkit](https://github.com/dikeckaan/zte-f50-toolkit) 提供的是 **B09** 路线，不是已验证的“任意版本降到 B16”，且包含改基带/设备标识工具。当前未找到可核实、覆盖具体起始版本的 B16 降级路线。**不要试 B09 包、不要猜降级。** 记录用户完整版本后，可以只做调查：在网上查有没有针对这个具体版本的路线，交给用户一份报告，逐条写来源链接、作者、是否有作者或他人的实机记录（覆盖哪个起始版本）、要写哪些分区（是否碰基带、NV/IMEI、GPT）、能否先完整备份、出错后怎么恢复；没找到就如实说没找到。**调查阶段不下载其中的刷机包或工具、不运行其中的命令、不连 F50 写入**；报告交给用户后停下，由用户决定。本向导不执行报告里的任何路线；在它被本项目实机验证并写进本节之前，可以建议用户换 B16 的 F50，或改用 N1 / 玩客云。
3. 在电脑建独立目录，获取本仓库工具（已有本仓库时直接把 `DP` 设成其绝对路径）：
   ```sh
   F50_WORK="$WORK/f50"
   python3 "$DP/scripts/fetch-install-assets.py" f50 "$F50_WORK"
   ```
   → 四项均 `SHA256_OK` → 下载失败只重试网络；`HASH_MISMATCH` 停下，不改清单去迁就下载。下载来源和全部 SHA-256 在本 skill 的 [downloads.json](downloads.json)；这是对应**项目作者发布地址**，不是中兴官方刷机服务，也不把社区镜像称为厂商官方。
4. 确认已执行“下载工具与校验”里的 `mac-bootstrap.sh` 和它给出的 `export PATH=...`：`python3 --version; command -v python3 spd_dump lz4` → Python 3.12.x，三个路径都在 `$WORK/python/bin` 或 `$DP/tools/mac-arm64` → 不是就回到那一步，不用系统或 Homebrew 的同名程序。Python 库用项目环境：`python3 -m venv "$WORK/venv"`，执行 `"$WORK/venv/bin/python" -m pip install --only-binary=:all: --require-hashes -r "$DP/skills/dashplay-install/requirements-f50.txt"` → 固定版本及官方 PyPI wheel 摘要全部通过；失败停止，不去掉摘要校验或改共享环境；然后执行 `export PATH="$WORK/venv/bin:$PATH"`、`python3 -c "import sys,cryptography; print(sys.executable)"` → 显示 `$WORK/venv/bin/python3` 且导入成功；后续命令都在这个终端运行。导入失败或路径不是此 venv 则停止，不能换用共享 Python。ADB 若未安装：`python3 "$DP/scripts/fetch-install-assets.py" adb-mac "$WORK/adb"`，`unzip "$WORK/adb/platform-tools-darwin.zip" -d "$WORK/adb"`，本次 PATH 加 `$WORK/adb/platform-tools` → `adb version` 正常。Google 的 latest 地址若已更新而摘要不符，停下审核新版，不能绕过摘要。
5. 使用仓库自带的只读原始分区读取版 spd_dump（不执行上游一键脚本）：
   ```sh
   SPD="$DP/tools/mac-arm64/spd_dump"
   "$SPD" --help 2>&1 | head -1
   ```
   → 输出 `branch:pinned-raw-read, sha1:d24c21a0c93a545c0130668caa1826d59ceebe48` → 不是就停下，不能换成其他 fork 或自己下载的版本。它由 `scripts/build-mac-tools.sh` 从固定提交的 [spreadtrum_flash](https://github.com/TomKing062/spreadtrum_flash) 源码、`patches/spreadtrum-read-user-area.patch` 和 libusb 1.0.29 编出，`mac-bootstrap.sh` 已核对摘要；该补丁只增加 `user_partition` 原始读取，不改变写入。

### F1 下载模式完整备份

备份按分区表的原始偏移读取主副 GPT 和 75 个分区中的 69 个（约 3.3 GiB，几分钟），包括 NV/IMEI、基带、全部启动分区；不用 `r all`/`all_lite`，命名 NV 读取可能替换分区、去头。跳过的是安卓用户数据与日志：userdata、cache、blackbox、fulldumpdb、sysdumpdb、uboot_log（约 27 GiB，要多读四十分钟）。本路线从不写这些分区，2026-10-14 恢复原厂 B16 也只写回 boot/trustos/misc，再用后台“恢复出厂设置”重建了用户数据；所以开始前请用户记下原厂后台里改过的设置（WiFi 名称和密码等），以后要回原厂时这些需要重新设置。eMMC boot0/boot1/RPMB 不在用户区内，下载通路未覆盖，不能声称可恢复所有硬件故障。SD 卡如有数据先用读卡器另备份，不能把“授权清空”当成已经备份。

1. `mkdir "$F50_WORK/discovery"; cd "$F50_WORK/discovery"`；先运行 `"$SPD" --wait 600 --kickto 2 exec path . partition_list partition_list.xml reset`，然后单独请用户：**断开 F50 全部电源，再用已确认的数据口接回电脑。** → 应看到 FDL2、分区表、`pgpt.bin`、reset ACK，退出 0。若模式切换报告 `LIBUSB_ERROR_IO`：只在系统 USB 枚举已变成 `1782:4d00` 时，等枚举稳定后执行一次 `"$SPD" --wait 10 exec path . partition_list partition_list.xml reset`（标准 CHECK_BAUD；不用 `--reconnect`）。仍失败就停止，保留日志，不轮试 kick0/kick1 或短接。
2. `python3 "$DP/scripts/f50-backup.py" plan "$F50_WORK/discovery/pgpt.bin" "$F50_WORK/backup" "$SPD"` → `PLAN_READY 69 of 75 partitions`，跳过的正好是上面 6 个，检查计划所需空间 → CRC/布局不符或数量不同停下问用户，不手改 GPT，不改跳过名单。
3. `sh "$F50_WORK/backup/backup.sh" > "$F50_WORK/backup/download.log" 2>&1`，再次只请用户断电重接 → 命令退出 0、后台恢复安卓 → 读取失败保留目录，不能进入 root。
4. `python3 "$DP/scripts/f50-backup.py" verify "$F50_WORK/backup"` → **`BACKUP_VERIFIED 69 of 75 partitions`**，生成 `verified.json`，主副 GPT 相互验证、boot/trustos/misc 重复读相同 → 少一个文件、长度不对或哈希不一致都停止。备份只留用户电脑，目录 0700，不上传、不放公开仓库。

### F2 用本机备份制作 Root，逐步写后读回

1. `adb start-server` → 存在 `$HOME/.android/adbkey.pub` → 没有就 `mkdir -p "$HOME/.android" && adb keygen "$HOME/.android/adbkey"` 生成这台电脑自己的密钥，再确认公钥存在；仍没有就停下排查 ADB，不能拿别人公钥。离线制作：
   ```sh
   python3 "$DP/scripts/prepare-f50-root-candidate.py" "$F50_WORK/magisk.img" "$F50_WORK/Magisk_28103.apk" "$HOME/.android/adbkey.pub" "$F50_WORK/boot-root.img"
   python3 "$DP/scripts/prepare-f50-trustos.py" "$F50_WORK/backup/trustos_b.bin" "$F50_WORK/backup/sml_b.bin" "$F50_WORK/trustos-root.bin"
   python3 "$DP/scripts/f50-root-plan.py" prepare "$F50_WORK/backup" "$F50_WORK/boot-root.img" "$F50_WORK/trustos-root.bin" "$F50_WORK/root-session"
   ```
   → ramdisk 往返通过、`packaging_matches_device_backups=true`、签名验证通过、`ROOT_PLAN_READY` → 任一失败都停止。TrustOS 必须来自**这台设备自己的** trustos/sml；不下载别人 tos.bin。补丁将两个已审核的 AVB 错误返回改为成功；离线验证不等于这台设备已实测可启动。
2. **写入范围必须包括可能影响 A/B 两槽的 boot 和 trustos**。此下载固件存在槽映射/复制现象，`w boot_b` 不保证只变 B；因此计划读取物理 GPT 偏移、同时检查 A/B，禁止宣称“未改 A”。保持同一个交互 FDL 会话：`cd "$F50_WORK/root-session"; "$SPD" --wait 600 --kickto 2 exec`，请用户断电重接 → 等 `FDL2 >`。失败按 F1 的单次标准握手退路；没有 FDL2 就不发写命令。
3. 在这个 FDL 终端逐行输入 `stage-before.txt` 的命令（**这些是 spd_dump 命令，不是 shell 脚本**；用另一主机终端查看文本）。每行结束等 `FDL2 >`。再在另一终端执行 `python3 "$DP/scripts/f50-root-plan.py" check "$F50_WORK/root-session" before` → `READBACK_OK before` → 不符即停止，不写。
4. 同样逐行输入 `stage-boot.txt`，它只下发一次 `w boot_b root-boot.img`，随后完整读回 A/B boot、A/B trustos、misc、主副 GPT；主机执行 `python3 "$DP/scripts/f50-root-plan.py" check "$F50_WORK/root-session" boot`→ `READBACK_OK boot` → 不符即停，**不再写、不 reset、不拔线硬试**，保存现场问用户。
5. 同样输入 `stage-trustos.txt`，主机执行 `python3 "$DP/scripts/f50-root-plan.py" check "$F50_WORK/root-session" trustos` → `READBACK_OK trustos` → 不符同上。只有两阶段都通过，才在 FDL 终端输入 `reset`，等 ACK/退出。
6. 安卓启动后 `adb connect 192.168.0.1:5555`，`adb -s 192.168.0.1:5555 shell 'id; getprop sys.boot_completed; getprop ro.boot.slot_suffix; getprop ro.build.display.id'` → uid=0、1、`_b`、B16 → 连接失败、非 root、非 B16、非 B 槽都停止，不改槽假装满足前置。再次提醒当前 TCP ADB 无可靠认证，勿接共享 WiFi。

### F3 SD 安装 OpenWrt，保留 Android B

1. `python3 "$DP/scripts/prepare-mu300-panel-installer.py" "$F50_WORK/openwrt-k5.4.zip" "$F50_WORK/openwrt-luci-rootfs.tar.gz" "$F50_WORK/mu300-panel.zip"` → 输出派生包 SHA-256，ZIP 校验通过 → 不匹配停止。此派生器只切面板 rootfs，保留上游 root/槽位/SD 防护；不是执行早期会缩 userdata 的 PC 安装器。
2. 请用户插入已授权清空的 SD 卡。`adb -s 192.168.0.1:5555 shell 'cat /proc/partitions; readlink -f /dev/block/by-name/boot_a; readlink -f /dev/block/by-name/boot_b'` → `mmcblk1p1` 容量与用户 SD 卡相符，A/B 对应不同块设备 → 不符停下，不用 eMMC 代替 SD。
3. `adb -s 192.168.0.1:5555 push "$F50_WORK/mu300-panel.zip" /data/local/tmp/mu300-panel.zip`。在电脑保存以下 **非秘密** 配置为 `$F50_WORK/mu300-install.conf`：
   ```text
   MU300_STORAGE=sd
   MU300_SD_ERASE=yes
   MU300_BOOT_OS=openwrt-luci
   MU300_BOOT=android
   MU300_BOOT_ATTEMPTS=5
   MU300_HOTSPOT=yes
   MU300_DRY_RUN=1
   ```
   `adb -s 192.168.0.1:5555 push "$F50_WORK/mu300-install.conf" /data/adb/mu300-install.conf`；`adb -s 192.168.0.1:5555 shell 'chown 0:0 /data/adb/mu300-install.conf; chmod 600 /data/adb/mu300-install.conf; /data/adb/magisk/magisk --install-module /data/local/tmp/mu300-panel.zip'`。
   → 显示 Android B、Linux A、SD 卡、只写 SD/boot_a/misc，最后 `Dry run: nothing was written`（Magisk 外层可非零退出，按文本确认）→ 若要写内部 userdata/GPT、不是 SD 或槽位不同，停止，不删保护检查。
4. 备份 root 后的 boot_a/b、misc 到电脑：每个使用 `adb -s 192.168.0.1:5555 exec-out cat /dev/block/by-name/<分区> > "$F50_WORK/preinstall-<分区>.bin"`，三个文件大小分别应为 64 MiB、64 MiB、1 MiB。准备已确认，删除配置文件中 **仅** `MU300_DRY_RUN=1` 这一行，重新 push/chown/chmod，执行相同 `magisk --install-module`。→ 完成安装并输出密码保存位置 → 出错停止，不立刻 reboot。登录密码从输出提示的本机私有位置读取，不贴聊天。
5. `adb -s 192.168.0.1:5555 exec-out cat /dev/block/by-name/boot_b` 保存为 `postinstall-boot_b.bin`，用 `cmp "$F50_WORK/preinstall-boot_b.bin" "$F50_WORK/postinstall-boot_b.bin"` → 无输出且退出 0，安装器的 boot_a/misc 校验通过 → 任何校验失败不重启。执行 `adb -s 192.168.0.1:5555 shell sh /data/adb/modules/mu300_linux_switch/switch.sh` → 设备重启到 OpenWrt；如果模块目录不存在，不猜另一个路径，先 `adb -s 192.168.0.1:5555 shell ls /data/adb/modules` 核对安装是否成功。
6. SSH 到 `192.168.77.1`（root，使用刚才生成的密码），运行 `cat /etc/openwrt_release; uname -r; df -h /; cat /run/mu300/linux-slot` → OpenWrt 25.12.x、5.4、SD 根、a；后台能登录。`fw4 check` → 正常；若明确是 5.4 的 flowtable 错误，`uci set firewall.@defaults[0].flow_offloading=0; uci commit firewall; /etc/init.d/firewall restart`，再检查；不能关闭整个防火墙。`mu300-next-boot linux` → `default boot: linux`：安装和调试期间断电或重启仍回到 OpenWrt，连续 5 次启动失败自动回 Android；不执行 lock。以后开机默认进哪个系统，装好并真实出画后在「收尾检查」问用户再定。
7. 进入下一节安装 DashPlay 插件，再放认证文件、配对；特斯拉直接完成「特斯拉」一节。保留备份和 SD，不宣称未做的冷启动/上车验收已经通过。

## 已有 OpenWrt：安装 DashPlay 插件（F50 / 路由器）

DashPlay 在 OpenWrt 上是插件。设备必须已经运行 OpenWrt 25.12（用 `apk` 装包）；原厂 F50 先完成上一节。其他路由器的刷机按该型号官方文档处理，未知型号停下问用户。

1. **确认环境**（SSH 登录路由器，地址和密码问用户；F50 用 USB 线连电脑时一般是 `192.168.77.1`）：
   `cat /etc/openwrt_release; cat /etc/apk/arch; df -h /; free -m`
   需要 `DISTRIB_RELEASE` 为 25.12.x、架构是 `aarch64_*` 或 `x86_64`、约 230 MB 空闲存储、内存 512 MB 以上。不满足就说明原因并停下。
2. **下载并安装**（先说明会往路由器装 Python、Java 等约 225 MB，得到同意）：从 openwrt-dashplay 发布页下载 `dashplay-*.apk`、与 `/etc/apk/arch` 一致的 `dashplay-jre-*_<架构>.apk`、`luci-app-dashplay-*.apk`、`luci-i18n-dashplay-zh-cn-*.apk` 和 `SHA256SUMS`，校验后传到路由器 `/tmp`，然后：
   `apk update && apk add --allow-untrusted /tmp/dashplay-jre-*_$(cat /etc/apk/arch).apk /tmp/dashplay-2*.apk /tmp/luci-*.apk`
   （包没有签名，所以要 `--allow-untrusted`。）
3. **蓝牙**（无线 CarPlay 需要）：
   - F50：**不要**装系统的 `bluez-daemon`（会带入和 F50 内核不匹配的模块）。依次执行：`python3 /opt/dashplay/board_zte_f50.py --setup`；`uci set dashplay.main.bluetooth_service='/usr/bin/python3 /opt/dashplay/board_zte_f50.py'`；`uci set dashplay.main.dbus_address='unix:path=/var/run/dashplay/bluetooth/system_bus_socket'`；`uci commit dashplay && /etc/init.d/dashplay restart`。→ 设置页能添加 iPhone、日志没有 Bluetooth adapter did not power on → 失败查 `/var/run/dashplay/supervisor.log`，不改装系统 BlueZ。
   - 其他路由器：`apk add bluez-daemon`；用 USB 蓝牙棒再加 `kmod-btusb`。没有蓝牙就只能有线。
   - 有线（数据线）需要路由器的 USB 口能当主机：`apk add kmod-tun`。F50 不支持有线。
4. **WiFi**：在路由器后台「服务 → DashPlay」选交给 iPhone 的 WiFi（默认自动选第一个 5 GHz 热点）。网络本身用户怎么设就怎么用，不要改他的网络配置。
5. 去“放认证文件”（路径相同；OpenWrt 上看日志用 `cat /var/run/dashplay/supervisor.log`、`tail /var/run/dashplay/receiver.log`，服务用 `/etc/init.d/dashplay restart`），然后：
   - 设置页 `http://<路由器地址>:18080/`（添加 iPhone）；
   - 车机连路由器的 WiFi，浏览器打开 `https://dashplay.lan:18443/`（或 `https://<路由器地址>:18443/`）；打不开时见下面「车机打不开画面时」。

## 放认证文件（所有设备）

认证副本只留用户电脑私有目录，不能提交或发 issue。已有文件时跳到第 2 步；没有时先说明共用实验身份可能失效，再执行第 1 步。

1. 将下面代码保存为 `$WORK/get-identity.py`，用 `python3 "$WORK/get-identity.py" "$WORK/identity-private"` 执行（Windows 用 `python`）。它从 [DiPlay 官方发布列表](https://github.com/shihabal3amri/DiPlay/releases) 选择最新含身份的 APK，包含预发布；不会打印认证内容，也不固定版本。

   ```python
   import hashlib, io, json, os, pathlib, sys, urllib.request, zipfile
   os.umask(0o077)
   out = pathlib.Path(sys.argv[1])
   out.mkdir(mode=0o700)  # 已存在就停，避免覆盖用户文件
   def get(url):
       req = urllib.request.Request(url, headers={'User-Agent': 'DashPlay-install'})
       with urllib.request.urlopen(req, timeout=120) as r:
           return r.read()
   releases = json.loads(get('https://api.github.com/repos/shihabal3amri/DiPlay/releases'))
   for release in sorted(releases, key=lambda x: x.get('published_at') or '', reverse=True):
       if release.get('draft'):
           continue
       for asset in release['assets']:
           if not (asset['name'].startswith('DiPlay-') and asset['name'].endswith('.apk')):
               continue
           digest = asset.get('digest') or ''
           if not digest.startswith('sha256:'):
               raise SystemExit('STOP: upstream asset lacks SHA-256; review release checksums')
           data = get(asset['browser_download_url'])
           if 'sha256:' + hashlib.sha256(data).hexdigest() != digest:
               raise SystemExit('STOP: APK SHA-256 mismatch')
           with zipfile.ZipFile(io.BytesIO(data)) as z:
               names = ['identity.pk8', 'certificate.p7b']
               if not all('assets/offline-mfi/' + n in z.namelist() for n in names):
                   continue
               for n in names:
                   with (out / n).open('xb') as f:
                       f.write(z.read('assets/offline-mfi/' + n))
           (out / 'source.json').write_text(json.dumps({'tag': release['tag_name'], 'url': asset['browser_download_url'], 'sha256': digest[7:]}) + '\n')
           print('IDENTITY_READY: two files; source and SHA-256 recorded privately')
           raise SystemExit(0)
   raise SystemExit('STOP: no matching identity in published APKs')
   ```

   → `IDENTITY_READY` → 失败停止，读明确错误；不从其他站点找身份。Windows 确认目录在本人用户目录内且未共享；文件不得放公共桌面/共享盘。
2. 将 `$ID_DIR` 设成两个文件所在目录。先 `ssh root@<盒子地址> 'umask 077; mkdir -p /opt/dashplay/identity/offline-mfi; chmod 700 /opt/dashplay/identity/offline-mfi'`，再 `scp "$ID_DIR/identity.pk8" "$ID_DIR/certificate.p7b" root@<盒子地址>:/opt/dashplay/identity/offline-mfi/` → 均成功 → 失败排查 SSH/路径，不替换成空文件。若盒子原来已有身份，先按升级章节做完整备份，不能直接覆盖。
3. 将下列内容保存为 `$WORK/check-identity.sh`，`scp "$WORK/check-identity.sh" root@<盒子地址>:/tmp/check-identity.sh`，然后 `ssh root@<盒子地址> 'sh /tmp/check-identity.sh'`（这些引号内和文件内均为**远端 shell**，Windows 也相同）：

   ```sh
   set -eu
   umask 077
   cd /opt/dashplay/identity/offline-mfi
   chmod 600 identity.pk8 certificate.p7b
   tmp=$(mktemp -d)
   trap 'rm -rf "$tmp"' EXIT
   openssl pkey -inform DER -in identity.pk8 -pubout -outform DER -out "$tmp/key.pub"
   openssl pkcs7 -inform DER -in certificate.p7b -print_certs -out "$tmp/certs.pem"
   openssl x509 -in "$tmp/certs.pem" -pubkey -noout > "$tmp/cert.pub.pem"
   openssl pkey -pubin -in "$tmp/cert.pub.pem" -outform DER -out "$tmp/cert.pub"
   test -s "$tmp/key.pub"
   test -s "$tmp/cert.pub"
   cmp -s "$tmp/key.pub" "$tmp/cert.pub"
   echo IDENTITY_MATCH
   ```

   → 退出 0、`IDENTITY_MATCH` → 解析失败、非零或没显示 MATCH 都停止，说明文件不是有效的一套；不能把两个失败命令的空输出当成匹配。放好后盒子自动重新加载，不用重刷。继续下节；只有真实出画才算安装验收通过。

## 首次设置与上车（玩客云 / N1）

首次设置在**手机**上做最简单，而且方式一、二要在**车上**做（盒子要连上车机热点或随身 WiFi 才能进行下一步）。你把步骤讲给用户，按用户手册第 2 节：

1. **请给盒子通电，手机连 WiFi「DashPlay」，打开自动弹出的设置页。** 没弹出可用 `http://dashplay.local/` 或新装机的 `http://192.168.78.1/`。
2. 按第 0 步已确认的需求逐页填写：车 → 网络 → 适用时车机上网 → 声音 → iPhone → 配置总结。总结前只保存草稿，返回可修改；确认后约 1 分钟生效，CarPlay 会断一下。方式一、二要在能找到上游热点的地方做。
3. 会用无线时按页面添加 iPhone：iPhone 停在「设置 → 蓝牙」，另一台设备或车机打开设置页，核对六位数字后两边确认。只用有线可跳过，插能传数据的线，首次在 iPhone 点信任。
4. 车机浏览器打开设置页列出的地址。特斯拉按下节配置网址，IP 证书能否继续仍未验证；其他车有继续选项时继续。完成页会提醒特斯拉回家插网线设置自己的网址。

以后分组为「车、网络、车机上网、iPhone、声音、自己的网址、远程调试」。改网络方式可保留已添加的手机；借同一 iPhone 热点也可保留无线偏好。密码只写不读，不让用户把凭据发到聊天。

## 特斯拉（第 0 步答了特斯拉时，装好后直接做）

特斯拉模式是已有网络方式上的开关：F50/OpenWrt 自己提供热点和上网；N1/玩客云用方式二或方式三，方式一不适用。开启时默认音乐、通话与 Siri 走车机蓝牙，并显示全屏按钮；N1/玩客云可在「声音」单独调整。F50 插卡已实测；N1 方式三的 A、B 手工方案已于 2026-10-15 实测，正式版本待复测，玩客云与特斯拉方式二未测。特斯拉能访问的地址必须实际核对，不能只看开关或把网关地址当盒子地址。

1. **按设备与方式启用**，先告诉用户本分支会改变哪个网络、需要重连哪个热点：
   - **F50 / OpenWrt**：在当前 SSH 会话执行 `uci set dashplay.main.tesla_mode=1 && uci commit dashplay && /etc/init.d/dashplay restart`。**插了能上网的 SIM 卡时**，同一条命令前先加 `uci set dashplay.main.answer_tesla_checks=0 &&`（关闭「替特斯拉回答联网检查」：有卡就有真网，不需要代答；2026.10.15 的程序只用 1.1.1.1 判断上游，有的运营商挡这个地址，会误判成没网并关掉上游 DNS，证书申请随之失败）；没插卡保持默认 `auto`。重启后执行 `nslookup duckdns.org 127.0.0.1` → 返回地址 → 否则先查 `uci get dashplay.main.answer_tesla_checks` 与 WAN，不继续申请证书。约半分钟后局域网地址变为 `100.96.0.1`，SSH 可能断开；电脑重连原热点后用 `ssh root@100.96.0.1`。执行 `ip -4 addr show br-lan` → 有 `100.96.0.1/24`；设置页无网段冲突红字 → 否则停下保存不含密码的网络状态，不改运营商网口。`uci show wireless | grep -E "country|channel"` 核对 WiFi；已有实车记录中 CN/149 可见、信道 36 不可见。仅在用户实际位于 CN、当地允许该频段且同意时，按输出中实际 radio 名执行 `uci set wireless.<radio>.country=CN; uci set wireless.<radio>.channel=149; uci commit wireless; wifi reload`，重连并再次核对 country/channel 与车机热点列表；其他地区按该地区官方频谱要求核对，不硬套 CN。
   - **N1 / 玩客云方式三**：首次设置选“车机连盒子热点”，在当前设置页“车”勾选“特斯拉模式”并保存。约 1 分钟后电脑重连已保存名称/密码的盒子热点，按设置页实际地址 SSH；执行 `ip -4 addr` → 热点网卡默认 `100.96.0.1/24`，冲突时为提示的共享空间备用 /24 → 不符停下看设置页提示，不重复切网段。
   - **N1 / 玩客云方式二**：先查看随身 WiFi 的型号官方手册和后台 LAN 设置，确认允许修改 LAN/DHCP；没有此功能即停止本路线，告知用户可改选方式三，不能声称 DashPlay 会代改随身 WiFi。经用户同意，在其后台将 LAN 设为 `100.96.0.1/24`、DHCP 池设为 `100.96.0.100–100.96.0.200`，确认该段不与其他接口冲突再保存；字段/含义不匹配时停下核对该型号文档。电脑、盒子和车机都重连随身 WiFi。由后台租约按已记录的盒子 MAC 找到**盒子的新 IP**，用该 IP SSH；网关 `100.96.0.1` 是随身 WiFi，不能拿来当 DashPlay 地址。在盒子设置页勾选“特斯拉模式”保存。随后 SSH 到该盒子执行：
     `python3 -c "import json,pathlib,ipaddress; a=json.loads(pathlib.Path('/opt/dashplay/state/car-lease.json').read_text())['address']; assert ipaddress.ip_address(a) in ipaddress.ip_network('100.64.0.0/10'); print(a)"`
     再 `ip -4 addr` → 输出的同一个地址确实在盒子的无线网卡上、设置页无地址警告 → 不符停止检查上游 DHCP，不用静态猜测的地址继续。
2. **核对接收端**（三种盒子相同）：`for p in $(pgrep -f run-receiver.py); do tr '\0' ' ' < /proc/$p/cmdline; echo; done | grep -c -- '--tesla'` → 至少 1（启动程序带 `--tesla`；Java 进程本身不带这个参数，只带 `--car-voice`，不要拿 Java 进程判断）；再 `grep -o '"tesla": true' /opt/dashplay/playport/web/dist/receiver-access.json` → 输出 `"tesla": true`（网页的特斯拉设置；该文件含访问 token，只 grep 这一项，不整份打印）→ 任一不符则查看本次 receiver/supervisor 日志，不把 grep 自己当成功。记录本次车机 URL：F50/OpenWrt、方式三用设置页实际盒子地址（默认 `https://100.96.0.1:18443/`）；方式二用 `https://<上一步核验的盒子IP>:18443/`。电脑先连同一热点打开此 URL → 接收端页面可达 → 失败先排网络，不能记出画通过。
3. **设置自己的域名（按 Q6 的选择）**：先告诉用户原因——目前特斯拉实车只验证过「自己的域名 + 正式证书」这条路（F50 10-14、N1 10-15）；直接打开 `100.96.0.1` 会有证书警告，特斯拉上有没有「继续」按钮还没实车确认，没有就打不开。所以特斯拉推荐按下面「车机打不开画面时」里「自己的域名（DuckDNS）」的说明，讲清为什么、带用户注册并自己填 token。证书要盒子能上网才能申请：F50 / OpenWrt 有 SIM 卡上网即可；N1 / 玩客云方式二经随身 WiFi 上网；**方式三推荐先在家插网线开机**等证书装好（设置页显示有效期）再上车。证书在**下次开机**启用：装好后重启一次，电脑连同一热点打开 `https://子域名.duckdns.org:18443/` → 无证书警告、出接收端页面 → 才记为可用；还没好就先用第 2 步的 URL 上车，不反复重存硬试（每周 5 张限额）。用户明确不想注册时，说明只能试第 2 步的 IP 地址、可能被证书警告挡住，并记为「未用域名」。
4. **请用户在车上做**（一次一件）：
   - **特斯拉连上路由器 / 盒子的 WiFi，在这个 WiFi 的设置里打开“行驶中保持连接”（Remain connected in Drive）。**
   - 选择蓝牙声音时，**iPhone 用蓝牙连上特斯拉，并在特斯拉里把媒体来源选成这部 iPhone。**
   - **特斯拉浏览器打开 `https://子域名.duckdns.org:18443/`**（第 3 步验证可用的）；域名还没好时打开第 2 步核验的 URL，证书警告有继续选项就继续，没有就回第 3 步把域名做好，不把别的模式地址硬套过来。成功后存为书签。
   - **全屏前先确认车机浏览器能打开腾讯视频、1905 的外网页面。** 方案 A 下特斯拉所有 DNS 只问盒子，不会改走 4G；外网不能用。B 或 F50 有 SIM 时才有上游。外网不可达就保留普通浏览器画面，记“全屏未验收”，不要求反复点全屏。外网可达才点右上角全屏，经腾讯视频、1905 跳回本页（腾讯页点“前往 电影网”）→ 地址栏消失、CarPlay 图像恢复 → 失败回书签并保留错误页面/时间。
5. 验收：A 要特斯拉连接无「无互联网」提示、域名页面出画、触控正常；B 另加网易云能打开。A/B 各拔电重启一次，核对热点 MAC 不变，记录日志时间；F50 无卡 A 单独记录未测或实际结果。实际连接 iPhone，车机看到 CarPlay 图像并能点按；按“收尾检查”确认本次日志 `CarPlay is live`，不能用设置页可打开代替出画。驻车播放音乐，在 iPhone 控制中心确认输出为特斯拉，方向盘切歌成功、媒体卡片曲名随之变化；拨打一通测试电话、唤起 Siri，确认声音均从车机蓝牙且双方可听清。任一失败记录时间、具体音频输出与本次日志，保留为未通过，不只凭 `--tesla` 参数判成功。
6. 偶尔放歌没声音时，在 iPhone 控制中心把音频输出选成特斯拉；Siri 用完音乐不会自动继续，按一下播放。

### 按连接环节排错（所有车型，特斯拉套用已知适配）

先确定卡在哪：**连不上看 ①～③，连上了打不开看 ④⑤，用着不对看 ⑥～⑧**。未知车型按实际能力和现象排查；下面只有已验证的特斯拉行为才使用特斯拉适配。

| 环节 / 现象 | 核对与处理 |
|---|---|
| ① 分地址：找不到 WiFi、拿不到地址或地址不可用 | 确认热点已开、名称密码与租约；玩客云检查 12 V。已知拒绝私网地址的特斯拉检查实际盒子 IP 在共享地址段，不把随身 WiFi 网关当盒子。 |
| ② 联网检查：弹「无互联网连接」，只有重试 / 取消 | 先查实际上游。仅特斯拉 + 盒子热点 + 无上游才代答；Debian 查 `state/diag/tesla-dns.log` 的四个检查名与 HTTP 200、X-ConnMan-Status online。F50 查 WAN 判断与离线状态。有真网的 B、F50 插卡、随身 WiFi 都用真实检查，不能伪造。未知车型先记录其检查行为。 |
| ③ 网关 MAC：已连接但什么都打不开 | 比较 ap0 与日常热点 MAC（通常 permaddr），日志确认是否改变；正式共享功能固定 MAC。特斯拉留了旧记录时忘记 WiFi 后重连，不能靠免费 ARP。 |
| ④ DNS：自带音乐 / 地图不能联网 | 特斯拉 A 的正常代价，DNS 不回落 4G；要用这些应用，N1 / 玩客云借手机热点，F50 插 SIM。B/F50 恢复上游后须正常转发所有名字，不能继续本地代答。 |
| ⑤ 网址与证书：连上但浏览器打不开 | 核对实际盒子地址、域名解析、证书与手机同网访问。A 由盒子答自己的域名；有上游时走正常 DNS。证书没有继续选项按下节设置自己的网址，不能把 IP 警告说成已验收。 |
| ⑥ 显示：全屏失败、刷新慢或切应用卡 | 特斯拉全屏跳转需要外网；A 只提示。刷新慢保留 viewer stats 分段计时与 receiver 日志，已修关键帧请求阻塞 WebSocket，实车那次 10 秒仍待复测。切应用卡先重启 iPhone；重启前若能，先跑下面诊断。 |
| ⑦ 声音：音乐、电话、Siri 或方向盘不对 | 核对 iPhone 蓝牙已连车机、媒体来源与实际音频输出，以及音乐/通话两个独立设置；驻车分别验收，不只看程序参数。 |
| ⑧ 离开浏览器或出 P 挡断线 | 确认车机有无「行驶中保持连接」并打开；回来时看网页重连与 receiver 日志。未知车型按实际菜单确认。 |

CarPlay 链路另查：有线 `no IPv6 link-local` 要用 JVM 启动前启用 lo IPv6 的版本，无网线冷启动验收；无线重启后要重配时查控制器身份与旧配对目录，升级固定蓝牙身份的版本，N1 / 玩客云各冷启动三次免重配才通过。远程调试想恢复：给盒子拔电再插上，或等 30 分钟自动结束；日常借热点和限时调试分开。

诊断在已授权的盒子上执行 `python3 /opt/dashplay/carplay-timeline.py --help`：按实际连接填写 iPhone 地址、盒子视频端口、车机地址与网页端口，并提供接收端日志；每 0.1 秒对比入站/出站字节及触摸时间。只保存计数，抓包仍按私人诊断数据保管。iPhone 未发送、盒子延迟转发与车机显示慢要分别判断，不把模拟测试说成实车结果。

## 车机打不开画面时（玩客云 / N1 / OpenWrt 都适用）

一般用不到。先问用户**车机屏幕上看到的是什么**（能拍照最好），再只做对应的那一步，不要一上来就全开：

| 车机看到的 | 做什么 |
|---|---|
| 证书不安全，有「高级 → 继续前往」 | 点继续，正常现象，什么都不用设 |
| 证书警告，没有继续选项（如 `NET::ERR_CERT_AUTHORITY_INVALID`） | 先试装盒子 CA 证书；车机不让装，再设置「自己的域名」 |
| 手机能打开同一地址，车机说无法访问或被拦截，地址是 `192.168.x.x` | 车机不让打开局域网地址：设置「自己的域名」并勾选「同时登记公网 IPv6」（玩客云 / N1 方式一、二）；OpenWrt 把「访问名称指向公网 IPv6」选「同时」 |
| 特斯拉 | 按上面「特斯拉」一节（特斯拉模式没开、没打开「行驶中保持连接」、搜不到 WiFi 都在那里） |
| 别的车：局域网地址、名称都打不开，也没有 IPv6 | OpenWrt 勾选「车机不让开任何局域网地址」，同样改网段 |
| 想用车机原生的方向盘切歌 / 媒体卡片，或网页通话有回声 | 车机能用蓝牙连 iPhone 时，勾选「音乐走车机蓝牙」和 / 或「通话与 Siri 走车机蓝牙」（玩客云 / N1 设置页「声音」分组；OpenWrt 声音页），iPhone 用蓝牙连车机并选为媒体来源；见安装说明「声音走车机蓝牙」 |
| 手机也打不开 | 地址变了或没连同一个热点，见安装说明第 4 节「地址变了」 |

- 这些选项都在设置页「自己的网址」分组（OpenWrt：后台「服务 → DashPlay → 自己的网址」标签），每一项都写了它解决什么问题；详细步骤和怎么确认生效，照主仓库 `docs/DashPlay-AI安装说明.md` 第 4 节做。
- **「自己的域名」（DuckDNS）在特斯拉（上节第 3 步）或上表指到它时才用**，开始前先用大白话告诉用户为什么：
  > 车机浏览器不信任盒子自己签的证书，有的车（没有「继续」按钮）就直接拦住；有的车还不让打开 `192.168.x.x` 这类局域网地址。给盒子一个免费的网址（`你起的名字.duckdns.org`），盒子就能自动申请一张正规的证书，车机把它当普通网站打开，不再有警告。DuckDNS 是免费的域名服务，盒子需要你在那里的一串密钥（token）来证明这个网址是你的，证书才能申请下来。

  然后请用户按步骤做（一次一步，等他说做完）：
  1. **电脑或手机浏览器打开 https://www.duckdns.org ，用页面上方列出的账号（如 Google、GitHub）登录。** 免费，不用填卡。
  2. **在 `sub domain` 框里起一个名字（只用小写字母、数字和 `-`，如 `mycar-2026`），点 `add domain`。** 提示被占用就换一个。名字会公开，别用真名、车牌或手机号。
  3. **告诉我你起的名字**（名字可以说，token 不要发给我）。页面顶部 `token` 那一串留着，下一步自己填。
  4. **打开盒子设置页「自己的网址」，填名字和 token，保存。** F50 / OpenWrt：后台「服务 → DashPlay → 自己的网址」填「自己的域名」和「DuckDNS token」，保存并应用。IP 不用在 DuckDNS 页面填，盒子自己登记、地址变了自己改。

  **token 让用户自己输入**，不要让他发给你，也不要写进命令、文件或日志；用户误发了就提醒他回 DuckDNS 页面重新生成 token 再重填。申请证书要盒子能上网（方式三车上没网，在家插网线开机申请）。证书由 Let's Encrypt 签发，同一个名字每周最多 5 张：失败先看设置页提示和 `duckdns ... OK`、`certificate for ... installed` 日志，**不要反复重存或重启去硬试**。
- 「同时登记公网 IPv6」在玩客云 / N1 上会把盒子的公网 IPv6 地址公开登记到 DuckDNS，盒子同时开防火墙只让同一个 WiFi 的设备进来；告诉用户这一点再勾选。方式三（车机连盒子热点）没有公网地址，用不了。
- 设置后正式证书要几分钟申请、**下次开机**启用；之后车机打开 `https://子域名.duckdns.org:18443/`。
- 「车机不让开任何局域网地址」会改掉路由器的局域网地址（改成 `100.96.0.1`），勾选前告诉用户：改完连着它的设备要重连 WiFi，后台地址变成 `http://100.96.0.1/`，你自己的 SSH 也要改连这个地址。

## 收尾检查

- 玩客云 / N1：`ssh root@<盒子地址> 'systemctl is-active dashplay; tail -20 /run/dashplay/receiver.log'`，iPhone 连上后日志里有 `CarPlay is live`。
- OpenWrt：`/etc/init.d/dashplay status`，`grep -E "CarPlay is live|session active" /var/run/dashplay/receiver.log`。
- **F50：问用户开机默认进哪个系统**（真实出画、特斯拉一节也做完之后再问；用户的回答决定下面做 A 还是 B，没回答前保持安装时的默认 OpenWrt）。原话问：
  > 以后 F50 一通电，默认进哪个系统？
  > A（推荐，车上用选这个）：默认 OpenWrt。上车通电后自动进 OpenWrt、DashPlay 自动启动，iPhone 自动连上 CarPlay，不用电脑。要用原厂后台时需要用电脑切回去；OpenWrt 连续 5 次起不来会自动回原厂系统。
  > B：默认原厂系统。平时和原厂随身 WiFi 一样；每次要用 CarPlay，得先用电脑把它切到 OpenWrt，车上没电脑就用不了。原厂系统里还留着本次 Root 打开的调试端口，连上 F50 热点的任何人都能完全控制它。
  - **选 A**：`ssh root@<当前地址> 'mu300-next-boot linux; mu300-next-boot status'` → `default boot: linux`。然后请用户**拔掉 F50 电源再接上**（冷启动），约 2 分钟后连回热点，用当前地址（特斯拉模式是 100.96.0.1）SSH：`mu300-next-boot status; /etc/init.d/dashplay status`，`ls -t /opt/dashplay/state/logs | head -1` 是本次开机的新目录 → `default boot: linux`、running，iPhone 不用重新配对就再次出画、新日志里有 `CarPlay is live` → 才算完成；没回到 OpenWrt 或没出画按「排错」查，不改选 B 交差。不执行 `mu300-next-boot lock`（锁定后 OpenWrt 起不来也不会自动回原厂）；用户明确要求锁定时先讲清这一点，同意后再执行。告诉用户以后要回原厂：`ssh root@<地址> 'mu300-next-boot android && reboot'`。
  - **选 B**：先再说一次调试端口的风险，用户仍选 B 再执行 `ssh root@<当前地址> 'mu300-next-boot android; mu300-next-boot status'` → `default boot: android`；不用立刻重启，下次断电再通电就回原厂。把切回 OpenWrt 的方法写给用户并留在电脑上：F50 用数据线接电脑、原厂系统起来后 `adb connect 192.168.0.1:5555`，再 `adb -s 192.168.0.1:5555 shell sh /data/adb/modules/mu300_linux_switch/switch.sh`（只切这一次，下次通电又回原厂；想以后都默认 OpenWrt，回到 OpenWrt 后执行选 A 的命令）。提醒用户：原厂系统下不要让陌生设备连 F50 热点。
- 告诉用户以后怎么用：上车通电、等一会儿，车机浏览器打开同一个地址；设置页在哪里；指示灯的意思（用户手册“指示灯”一节）。

## 排错

车机连接与使用问题先按上面的 ①～⑧ 顺序定位；下面补充安装与 CarPlay 链路问题。

| 现象 | 先查 |
|---|---|
| 设置页顶部提示缺认证文件 / 日志有 `MFi identity missing` | 文件路径、文件名、权限；重新核对配对 |
| 找不到 WiFi `DashPlay` | 等 2 分钟；玩客云查 12 V 电源和网卡是否插好；N1 从 U 盘启动要插靠 HDMI 的口 |
| 方式一、二蓝灯（N1 快闪）一直闪 | 盒子没找到车机热点或随身 WiFi：确认它开着、名称密码对；设置页「立即重试」 |
| iPhone 无线连不上或几秒就断 | 同一部 iPhone 是否正连着别的 CarPlay（另一台盒子、车）；蓝牙和 WiFi 都开着；随身 WiFi 开了设备隔离时只能有线 |
| 车机浏览器打不开、地址变了、证书被拦截、不让打开局域网地址 | 上面「车机打不开画面时」，细节见 `docs/DashPlay-AI安装说明.md` 第 4 节 |
| 画面卡、断线 | 设置页「高级 → 下载诊断记录」；完整日志在盒子 `/opt/dashplay/state/logs/`（每次开机一个目录，断电不丢） |

查不出原因时，把诊断记录和日志里相关的几十行整理好，告诉用户可以到主仓库提 issue（不要附带认证文件、密码、令牌）。
