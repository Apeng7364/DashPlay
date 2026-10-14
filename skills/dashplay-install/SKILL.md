---
name: dashplay-install
description: 帮完全不懂技术的用户把 DashPlay（网页 CarPlay）装到斐讯 N1、玩客云、ZTE F50 或其他 OpenWrt 路由器上，从识别硬件、下载刷机包、刷机、放入用户自己的 CarPlay 认证文件，到上车使用和排错，一步步做完；只有插拔电源、按按钮、在手机或车机上点选这类必须本人动手的事才请用户做。用户说“帮我装 DashPlay”“我买了 N1/玩客云/F50，想用网页 CarPlay”时使用。
---

# DashPlay 装机向导（给 AI 助手）

**刷机风险：会清空选定存储；F50 Root 会降低启动校验，最坏需要拆机短接。先核对型号、备份和具体写入范围，再动手。**

用户把这份文件的地址交给你，意思是：**剩下的事你来做**。你在用户的电脑上执行命令、下载、检查、排错；只在必须本人动手时请用户操作。

项目地址：
- 主仓库（玩客云、N1 刷机包，程序本身）：<https://github.com/yizhixiaoheigou/DashPlay>，发布页 `/releases/latest`
- OpenWrt 软件包（F50 和其他 OpenWrt 路由器）：<https://github.com/yizhixiaoheigou/openwrt-dashplay>，发布页 `/releases/latest`
- 详细参考：主仓库 `docs/DashPlay玩客云固件使用说明.md`（用户手册）、`docs/DashPlay-AI安装说明.md`（认证文件与车机浏览器问题）、openwrt-dashplay 的 `README.md`

## 开始前

- 你需要能在用户电脑上执行命令（Claude Code、Codex 这类）。如果你只能聊天、不能执行命令，告诉用户换一个能执行命令的 AI 助手，或者你把每条命令写出来让他复制执行，并请他把结果贴回来。
- Windows 上用 PowerShell：Windows 10/11 自带 `ssh`、`scp`；`.xz` 用下面的 Python 命令解压，不额外下载解压工具。先 `Get-Command python,git,ssh,scp`，再 `python --version` → 全部存在、Python ≥3.11 → 缺项先停下说明需要安装哪一项，不盲装全局依赖。Windows 的下文 `python3` 一律写 `python`。
- 盒子安装通常约 30–60 分钟，F50 全量备份可能数小时；用户需要准备：设备的电源，N1 还要一个 8 GB 以上的 U 盘，玩客云还要一台 Windows 电脑、一根双公头 USB 线和一把螺丝刀或撬片。

## 与用户相处的规则

1. **用用户的语言，说人话。** 不解释原理，只说现在要做什么、为什么需要他。
2. **需要用户动手时**：单独一行加粗写清楚一个动作（例如“**请把 N1 的电源拔掉，再插回去**”），说明做完会看到什么，然后等他回复。一次只请他做一件事，能自己做的绝不交给他。
3. **先确认再做不可逆的事**：写 U 盘或硬盘、刷机、覆盖盒子内置存储、在用户的路由器上装软件之前，先说清楚会发生什么、会清掉什么，得到同意再做。写磁盘前必须让用户确认目标磁盘（按容量和名称），绝不猜。
4. **CarPlay 认证文件**（`identity.pk8` 和 `certificate.p7b` 两个文件）：用户有就用用户的；没有就由你从 DiPlay 的发布包里取（步骤见“放认证文件”），先用一句话告诉用户这是很多人共用的实验身份、苹果可能让它失效、只供自己用。认证文件和任何密码不要打印到屏幕、不要上传到任何地方，电脑副本留在用户指定的私有目录；未经用户授权不要删除。
5. 盒子的 SSH 默认用户名 `root`、密码 `1234`。装好后可以提议用户改密码（`passwd`），但不强制。
6. 出错先自己查日志、排查（见“排错”），不要把报错原样丢给用户。

## 第 0 步：问清楚

一上来先问前两项（用户答不上来就帮他判断，例如请他拍照或描述外观），再问其余：

1. **买的是哪种设备？** 斐讯 N1 / 玩客云 / ZTE F50 随身 WiFi / 其他已经装了 OpenWrt 的路由器。
2. **什么车？** 品牌和车型。**是特斯拉就按「特斯拉」一节全部配置好**（特斯拉模式、网段、声音走特斯拉蓝牙、全屏），不用等用户遇到打不开再处理；别的车照常装，「车机打不开画面时」按需处理。
3. **电脑是什么系统？** macOS / Windows / Linux。（玩客云刷机必须用 Windows 电脑，见下文。）
4. **车机的情况**：车机能不能开热点、车上有没有随身 WiFi；iPhone 打算插数据线（有线）还是无线。用来在首次设置里选使用方式（特斯拉不能开热点，见「特斯拉」一节）：
   - 方式一「车机热点」：盒子连车机热点，iPhone 只能插线；
   - 方式二「随身 WiFi」：盒子、车机、iPhone 都连随身 WiFi，插线有线、不插无线；
   - 方式三「车机连盒子热点」：盒子开热点，车机和 iPhone 连它，车机此时不能上网。
5. **有没有 CarPlay 认证文件**（两个文件）以及放在电脑哪里。没有也没关系，最后一步你从 DiPlay 发布包里取。

然后按设备走对应章节。动手前先说一句总计划（例如“我会先下载刷机包、帮你写进 U 盘，中间需要你插拔两次电源”）。

## 下载工具与校验（电脑上先做）

命令中的 `<IP>`、`<日期>`、`<架构>` 是占位符：用实际检测结果替换，不照着尖括号执行。每个小步骤只有“应该看到什么”满足后才能继续；不符先按该项退路处理。

```sh
INSTALL_BASE="$HOME"  # 可改成用户指定且已挂载的工作盘目录
WORK=$(mktemp -d "$INSTALL_BASE/dashplay-install.XXXXXX")
chmod 700 "$WORK"
git clone --depth 1 https://github.com/yizhixiaoheigou/DashPlay.git "$WORK/DashPlay"
DP="$WORK/DashPlay"
```

→ `$DP/scripts/fetch-install-assets.py` 存在 → 网络失败保留目录重试；文件不存在说明拿到的公开版本尚未包含本向导，停下，不从无关仓库拼工具。Windows 不执行上面的 sh 片段，改用：

```powershell
$WORK = Join-Path $env:USERPROFILE ('dashplay-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $WORK | Out-Null
git clone --depth 1 https://github.com/yizhixiaoheigou/DashPlay.git "$WORK/DashPlay"
if ($LASTEXITCODE -ne 0) { throw 'clone failed' }
$DP = Join-Path $WORK 'DashPlay'
Test-Path "$DP/scripts/fetch-install-assets.py"
```

→ 最后一行 `True` → 否则停止。下文 `$WORK`/`$DP` 在 PowerShell 同样可用；不要把 sh 的 `export`、`chmod`、反引号续行或 heredoc 直接贴进 PowerShell。每条外部命令检查 `$LASTEXITCODE` 为 0 才继续。

Mac 的 N1/F50 ADB 前置：先 `command -v adb; command -v fastboot` → 两者存在；缺少时执行 `python3 "$DP/scripts/fetch-install-assets.py" adb-mac "$WORK/adb"`，再 `unzip "$WORK/adb/platform-tools-darwin.zip" -d "$WORK/adb"`、`export PATH="$WORK/adb/platform-tools:$PATH"`、`adb version`、`fastboot --version` → 摘要通过、版本命令成功 → 否则停止。Linux/Windows 原厂 N1 路线需已有可工作的 ADB/fastboot；没有则停下明确换用本段支持的 Mac，不能临时找不明驱动包。

解压镜像：把 `$IMAGE_XZ` 设为下载的 `.xz` 绝对路径，执行 `python3 -c "import lzma,shutil,sys; p=sys.argv[1]; a=lzma.open(p,'rb'); b=open(p[:-3],'xb'); shutil.copyfileobj(a,b); b.close(); a.close()" "$IMAGE_XZ"`（Windows 用 `python`）→ 退出 0、同目录出现去掉 `.xz` 后缀的镜像 → 失败保留现场，不拿部分文件刷写；目标已存在时先核对来源，不覆盖用户文件。

固定前置下载的项目作者地址与 SHA-256 在 [downloads.json](downloads.json)（与 skill 同目录）；`python3 "$DP/scripts/fetch-install-assets.py" <组名> <目录>` 下载并逐项验证，输出 `SHA256_OK` 才可执行。`n1` 是社区保存的 2.19 分区镜像，`onecloud` 是教程作者保存的 Amlogic 工具，均**不是厂商认证的官方分发**；不要隐瞒来源。哈希用于固定本次审阅的文件，不等于厂商签名。用户要求只用厂商官方镜像时停下问用户，这两项没有已核实可用的厂商官方直链。

DashPlay 的镜像/程序包从本项目官方 GitHub Release 下载，按该版本同名 `.sha256` 校验；OpenWrt 包使用同版本 `SHA256SUMS`。先选定一个版本并记录 tag、下载 URL 和完整 SHA-256，再下载，不混合 latest 在不同时间的资产。校验不一致重新下载一次；仍不一致停止。macOS/Linux：在下载目录 `shasum -a 256 -c <校验文件>` → 每个目标 `OK`；Windows：`Get-FileHash -Algorithm SHA256 <文件>` → 与官方校验文件完整 64 位相同。无校验文件/发布 API `digest` 时停下，不以自己下载后算出的摘要充当独立核对。

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

1. **下载**：从主仓库发布页下载 `DashPlay_n1-<日期>.img.xz` 和同名 `.sha256`，按“下载工具与校验”校验并解压出 `.img`。
2. **写 U 盘**（8 GB 以上，会清空）：
   **请把一个不用的 U 盘插到电脑上。**
   - macOS：`diskutil list external` 找到 U 盘（核对容量），请用户确认是 `/dev/diskN`；然后 `diskutil unmountDisk /dev/diskN` 并执行 `sudo dd if=DashPlay_n1-<日期>.img of=/dev/rdiskN bs=4m`（会要用户输入电脑密码）。`dd` 退出 0 后，把下列代码保存为 `$WORK/check-usb.py`，执行 `sudo python3 "$WORK/check-usb.py" <镜像绝对路径> /dev/rdiskN` → `USB_READBACK_OK` 才执行 `diskutil eject /dev/diskN`；读取错误或不一致停止，不拿它启动盒子。
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
3. **从 U 盘启动**。先问：**这台 N1 以前刷过 Armbian/OpenWrt 等系统吗，还是买来就是斐讯原厂系统？**
   - **刷过别的系统**（大多数二手 N1）：一般直接能从 U 盘启动。
     **请拔掉 N1 的电源，把 U 盘插到靠近 HDMI 的那个 USB 口，再接上电源。**
   - **原厂系统**：准备 HDMI 显示器、USB 鼠标；降级另需双公头 USB 数据线。第一次开机先不接互联网，避免自动升级。本项目 N1 已实测的是已有 Linux 的机器，以下原厂前置步骤来自公开实践，尚未用原厂 N1 实测。
     1. **请接显示器和鼠标，给 N1 供电，把首页固件版本告诉我。** → 应为 N1 原厂版本 → 型号不是 N1、显示不清或定制版，停下问用户。
     2. **请在固件版本上连续点 4 次，看到“打开 adb”后给 N1 接到电脑同一局域网。** 最好用无互联网的局域网；不要擅自改用户路由器防火墙。先从 N1 首页/网络信息读取它的局域网 IP，作为本段 `<IP>`；不能确定哪台是 N1 就停下核对屏幕，不猜地址。电脑 `adb connect <IP>:5555`，`adb -s <IP>:5555 shell getprop ro.product.model` → `connected`、N1 → 未连上先核对 IP/同网和 ADB 开关，不能直接发 fastboot 写入。
     3. 版本 ≤2.19 跳过降级；>2.19 保守按需要降级处理（早期作者记载 >2.22，2.20–2.22 若无法核对则停下问用户）。下载：`python3 "$DP/scripts/fetch-install-assets.py" n1 "$WORK/n1-downgrade"` → 三项 `SHA256_OK`。来源为 [dreamrover 的镜像和命令](https://github.com/dreamrover/phicomm-n1/tree/e9007301fcfc5beb2c2da51dab9e810cbb509322/firmware_2.19)，作者实际操作记录见 [HouCoder](https://github.com/HouCoder/blog/blob/master/hacking/install-armbian-on-phicomm-n1/README.md)。不是完整系统 PAC，不可拿 T1 镜像代替。
     4. 获得清楚的降级授权后，**请用双公头 USB 线连接电脑与 N1 靠 HDMI 的 USB 口。** 执行 `adb -s <IP>:5555 shell reboot fastboot`，再 `fastboot devices` → 恰好一台设备标为 fastboot → 没设备先查数据线/驱动，多台先让用户断开无关设备，禁止猜序列号。
     5. 在 `$WORK/n1-downgrade` 依次执行 `fastboot flash bootloader bootloader.img` → Sending/Writing 均 OKAY；`fastboot flash boot boot.img` → 均 OKAY；`fastboot flash recovery recovery.img` → 均 OKAY；**任一步失败即停止，不继续写，不换包盲试。** 全过再 `fastboot reboot`。降级只换三个引导分区，界面版本可能不变，不以界面仍显示高版本判为失败。
     6. 原厂重新起来后，拔掉双公头线，重新 `adb connect <IP>:5555`。**请把已写好的 U 盘拿在手里，靠 HDMI 的 USB 口先留空。** 执行 `adb -s <IP>:5555 shell reboot update` 后单独提示用户立刻插 U 盘 → 出现 DashPlay WiFi 后先做下一步连网，再用 SSH 验收；不能只看斐讯 Logo。2 分钟还只有原厂后台：保留屏幕/启动状态，查 U 盘校验、端口与引导版本，不循环乱刷。
   第一次从 U 盘开机约 1–2 分钟，会出现名叫 `DashPlay` 的 WiFi（没有密码）。
4. **连上盒子**：让电脑连 WiFi `DashPlay`（macOS 可以用 `networksetup -setairportnetwork <WiFi 接口> DashPlay`，接口名用 `networksetup -listallhardwareports` 查；先问用户可不可以切换电脑的 WiFi；切换后电脑暂时不能上网，所以下载都要在这之前做完）。盒子地址固定 `192.168.78.1`：`ssh root@192.168.78.1`（密码 `1234`）。
   再执行 `ssh root@192.168.78.1 'findmnt -no SOURCE /; test -d /opt/dashplay && echo DASHPLAY_PRESENT'` → 根设备为 `/dev/sd…` 的 USB 分区、`DASHPLAY_PRESENT` → 若是 `/dev/mmcblk…` 或目录不存在，不能宣称 U 盘启动成功，先查启动来源，不运行内置安装。
5. **装进内置存储**（推荐，以后不用插 U 盘；会清空 N1 原来的系统，先确认）：
   `ssh root@192.168.78.1 "printf '101\n1\n' | armbian-install"`（机型 101 = Phicomm N1，文件系统 1 = ext4）。看到成功后：
   **请拔掉 N1 的电源，拔下 U 盘，再接上电源。**
   约 30 秒后 `DashPlay` WiFi 重新出现。用户想一直从 U 盘运行也可以跳过这一步。
6. 去“放认证文件”，再去“首次设置与上车”。

## 玩客云

玩客云要配一个 RTL8821CU 的 USB 无线网卡（WiFi 和蓝牙二合一，USB ID `0bda:c820`），**必须用 12 V 电源**（5 V 的 USB 转 DC 线会让网卡反复掉线）。刷机用 Amlogic 的 USB Burning Tool，**只能在 Windows 上运行**；用户没有 Windows 电脑时，请他借一台或用虚拟机，你远程指导。

1. **下载**：从主仓库发布页下载 `DashPlay_onecloud-<日期>.burn.img.xz` 和 `.sha256`，校验并解压出 `.burn.img`。
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

准备：F50、可清空的 8 GB 以上 SD 卡、能传数据的 USB 线、电脑至少 70 GB 空闲空间。下面的主机命令已按 Apple Silicon Mac 整理；Windows/Linux 若没有已验证的 USB/编译环境，停下问用户换到 Mac，不擅自套用 Windows 一键 root 脚本。F50 有两个 USB 口，使用能在电脑上打开原厂后台的那个；另一口无枚举时先换口，不当成设备坏了。

### F0 识别与下载

开始前检查**实际备份目录所在磁盘**：`python3 -c 'import shutil,sys; n=shutil.disk_usage(sys.argv[1]).free; print("FREE_BYTES",n); sys.exit(0 if n >= 70_000_000_000 else 1)' "$WORK"` → 退出 0 → 才能做全量备份。退出非 0 时不要清理用户文件：用 `df -h` 找用户允许的工作盘，把 `INSTALL_BASE` 改为该盘内的目录，重新 `mktemp -d "$INSTALL_BASE/dashplay-install.XXXXXX"` 建 0700 私有 `WORK` 并重做空间检查。已有 `DP` 继续指向原来的工具目录，不搬走或覆盖既有文件；随后重新设置 `F50_WORK="$WORK/f50"`。无足够空间则停下，让用户提供备份盘。

1. 电脑通过 USB 打开 `http://192.168.0.1/`，让用户在原厂后台登录，读取型号与完整固件版本 → 必须为 **ZTE F50、F50_FLYMODEM_ZYV1.0.0B16** → U30 Air、其他运营商定制版、非 B16 都停下问用户。不要用文件名代替后台版本。
2. 非 B16 的调查结论（2026-10-14）：[Xio-Shark 原作者](https://github.com/Xio-Shark/f50-b16) 明确只支持 B16；[zte-f50-toolkit](https://github.com/dikeckaan/zte-f50-toolkit) 提供的是 **B09** 路线，不是已验证的“任意版本降到 B16”，且包含改基带/设备标识工具。当前未找到可核实、覆盖具体起始版本的 B16 降级路线。**不要试 B09 包、不要猜降级；记录用户完整版本并停下问用户。**
3. 在电脑建独立目录，获取本仓库工具（已有本仓库时直接把 `DP` 设成其绝对路径）：
   ```sh
   F50_WORK="$WORK/f50"
   python3 "$DP/scripts/fetch-install-assets.py" f50 "$F50_WORK"
   ```
   → 五项均 `SHA256_OK` → 下载失败只重试网络；`HASH_MISMATCH` 停下，不改清单去迁就下载。下载来源和全部 SHA-256 在本 skill 的 [downloads.json](downloads.json)；这是对应**项目作者发布地址**，不是中兴官方刷机服务，也不把社区镜像称为厂商官方。
4. `python3 --version; command -v cc; command -v lz4; command -v adb; brew --prefix libusb` → Python ≥3.11，工具均存在。缺依赖先说明具体安装项；不能改共享环境而不告知。Python 库用项目环境：`python3 -m venv "$WORK/venv"`，执行 `"$WORK/venv/bin/python" -m pip install --only-binary=:all: --require-hashes -r "$DP/skills/dashplay-install/requirements-f50.txt"` → 固定版本及官方 PyPI wheel 摘要全部通过；失败停止，不去掉摘要校验或改共享环境；然后执行 `export PATH="$WORK/venv/bin:$PATH"`、`python3 -c "import sys,cryptography; print(sys.executable)"` → 显示 `$WORK/venv/bin/python3` 且导入成功；后续命令都在这个终端运行。导入失败或路径不是此 venv 则停止，不能换用共享 Python。`lz4`、`libusb`、编译器缺失时停下申请安装具体依赖。ADB 若未安装：`python3 "$DP/scripts/fetch-install-assets.py" adb-mac "$WORK/adb"`，`unzip "$WORK/adb/platform-tools-darwin.zip" -d "$WORK/adb"`，本次 PATH 加 `$WORK/adb/platform-tools` → `adb version` 正常。Google 的 latest 地址若已更新而摘要不符，停下审核新版，不能绕过摘要。
5. 编译只读原始分区读取版（不执行上游一键脚本）：
   ```sh
   tar -xzf "$F50_WORK/spreadtrum-source.tar.gz" -C "$F50_WORK"
   SPD_SRC="$F50_WORK/spreadtrum_flash-d24c21a0c93a545c0130668caa1826d59ceebe48"
   cd "$SPD_SRC"
   git apply --unidiff-zero "$DP/patches/spreadtrum-read-user-area.patch"
   printf '#define GIT_VER "pinned-raw-read"\n#define GIT_SHA1 "d24c21a0c93a545c0130668caa1826d59ceebe48"\n' > GITVER.h
   cc -O2 -DUSE_LIBUSB=1 -I"$(brew --prefix libusb)/include" -L"$(brew --prefix libusb)/lib" spd_dump.c common.c -lusb-1.0 -lpthread -lm -o spd_dump
   SPD="$SPD_SRC/spd_dump"
   "$SPD" --help
   ```
   → 帮助正常退出 → 编译/补丁不符停下，不能换成其他 fork 的写入命令。该补丁只增加 `user_partition` 原始读取，不改变写入。

### F1 下载模式完整备份

备份覆盖 **GPT 内全部分区（包括 userdata/cache）和主副 GPT**，不是 `r all`/`all_lite`；后两者会跳过数据区，且命名 NV 读取可能替换分区、去头。eMMC boot0/boot1/RPMB 不在用户区内，下载通路未覆盖，不能声称可恢复所有硬件故障。SD 卡如有数据先用读卡器另备份，不能把“授权清空”当成已经备份。

1. `mkdir "$F50_WORK/discovery"; cd "$F50_WORK/discovery"`；先运行 `"$SPD" --wait 600 --kickto 2 exec path . partition_list partition_list.xml reset`，然后单独请用户：**断开 F50 全部电源，再用已确认的数据口接回电脑。** → 应看到 FDL2、分区表、`pgpt.bin`、reset ACK，退出 0。若模式切换报告 `LIBUSB_ERROR_IO`：只在系统 USB 枚举已变成 `1782:4d00` 时，等枚举稳定后执行一次 `"$SPD" --wait 10 exec path . partition_list partition_list.xml reset`（标准 CHECK_BAUD；不用 `--reconnect`）。仍失败就停止，保留日志，不轮试 kick0/kick1 或短接。
2. `python3 "$DP/scripts/f50-backup.py" plan "$F50_WORK/discovery/pgpt.bin" "$F50_WORK/backup" "$SPD"` → `PLAN_READY`、75 个分区，检查计划所需空间 → CRC/布局不符或数量不是 75 停下问用户，不手改 GPT。
3. `sh "$F50_WORK/backup/backup.sh" > "$F50_WORK/backup/download.log" 2>&1`，再次只请用户断电重接 → 命令退出 0、后台恢复安卓 → 读取失败保留目录，不能进入 root。
4. `python3 "$DP/scripts/f50-backup.py" verify "$F50_WORK/backup"` → **`BACKUP_VERIFIED 75 partitions`**，生成 `verified.json`，主副 GPT 相互验证、boot/trustos/misc 重复读相同 → 少一个文件、长度不对或哈希不一致都停止。备份只留用户电脑，目录 0700，不上传、不放公开仓库。

### F2 用本机备份制作 Root，逐步写后读回

1. `adb start-server` → 存在 `$HOME/.android/adbkey.pub`；无公钥则先排查 ADB，不能拿别人公钥。离线制作：
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
6. SSH 到 `192.168.77.1`（root，使用刚才生成的密码），运行 `cat /etc/openwrt_release; uname -r; df -h /; cat /run/mu300/linux-slot` → OpenWrt 25.12.x、5.4、SD 根、a；后台能登录。`fw4 check` → 正常；若明确是 5.4 的 flowtable 错误，`uci set firewall.@defaults[0].flow_offloading=0; uci commit firewall; /etc/init.d/firewall restart`，再检查；不能关闭整个防火墙。`mu300-next-boot linux` → 默认 Linux，保留失败回退 Android，不执行 lock。
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

1. **请在车上给盒子通电，打开车机热点（或随身 WiFi）；手机连上 WiFi「DashPlay」，会自动弹出设置页。**（没弹出就用浏览器打开 `http://192.168.78.1/`。）
2. 选第 0 步商定的使用方式 → 在列表里选车机热点或随身 WiFi 并输入密码（同时给盒子自己的热点设个密码）；方式三是设置盒子热点名称和密码。
3. 连接 iPhone：插数据线的，第一次在 iPhone 上点「信任」并输入锁屏密码；要无线的，在车机屏幕上点「添加 iPhone」，iPhone 打开「设置 → 蓝牙」停在那一页，核对六位数字后两边确认。
4. 车机浏览器打开设置页上列出的 CarPlay 地址（方式一、二是 `https://<盒子地址>:18443/`，方式三固定 `https://192.168.78.1:18443/`，特斯拉模式下是 `https://100.96.0.1:18443/`），提示证书不安全时点「继续访问」。

用户手边有笔记本电脑并且在车上时，你也可以让电脑连 `DashPlay`，读 `http://192.168.78.1/` 页面里的表单代他提交；选择方式和输入热点密码仍然要问用户。

## 特斯拉（第 0 步答了特斯拉时，装好后直接做）

特斯拉浏览器不让打开 `192.168.x.x` 这类局域网地址（用名称也不行），也不走 IPv6；车机的音乐、方向盘、媒体卡片要走特斯拉自己的蓝牙才顺。「特斯拉模式」一次解决：网段改成 `100.96.0.1/24`、音乐 / 通话 / Siri 走特斯拉蓝牙、导航播报放大、网页全屏按钮可用。实车测过的是 F50（OpenWrt）；玩客云 / N1 的方式三照同样原理做好，还没在特斯拉上实测，先告诉用户这一点。

1. **打开特斯拉模式**（你自己做，先说一句「路由器 / 盒子的地址会变成 100.96.0.1，连着它的设备要重连一次 WiFi」）：
   - F50 / OpenWrt：`uci set dashplay.main.tesla_mode=1 && uci commit dashplay && /etc/init.d/dashplay restart`。约半分钟后路由器局域网变成 `100.96.0.1`，你的 SSH 会断：电脑重连 WiFi 后改连 `ssh root@100.96.0.1`。再看 WiFi：`uci show wireless | grep -E "country|channel"`，实测特斯拉搜不到信道 36 的 5G，若不是地区 CN、信道 149，征得同意后 `uci set wireless.<radio>.country=CN; uci set wireless.<radio>.channel=149; uci commit wireless; wifi reload`（DashPlay 自动跟上新信道）。设置页红字说别的网口已在用这段地址时，照它说的处理。
   - 玩客云 / N1：首次设置选**方式三「车机连盒子热点」**（特斯拉不能开热点；方式二要随身 WiFi 后台允许把局域网改成 `100.96.0.1`，不一定能改）。首次设置做完后：
     `ssh root@192.168.78.1 "python3 -c \"import json,pathlib;p=pathlib.Path('/opt/dashplay/state/options.json');d=json.loads(p.read_text()) if p.exists() else {};d['tesla']=True;p.write_text(json.dumps(d));p.chmod(0o600)\" && systemctl restart dashplay"`
     （也可以请用户在设置页「特斯拉与车机蓝牙」勾选「特斯拉模式」并保存。）约 1 分钟后盒子热点变成 `100.96.0.1`：电脑重连盒子热点后用 `ssh root@100.96.0.1`。
2. **核对**：OpenWrt `ip -4 addr show br-lan`（局域网网卡，通常是它）有 `100.96.0.1`、`ps w | grep -- --tesla` 有接收端；玩客云 / N1 `ip -4 addr` 里盒子热点网卡是 `100.96.0.1`、`ps -ef | grep -- --tesla` 有接收端。
3. **请用户在车上做**（一次一件）：
   - **特斯拉连上路由器 / 盒子的 WiFi，在这个 WiFi 的设置里打开「行驶中保持连接」（Remain connected in Drive）。** 不开的话一出 P 挡（倒车、开车）特斯拉就断开 WiFi，画面停住。
   - **iPhone 用蓝牙连上特斯拉，并在特斯拉里把媒体来源选成这部 iPhone。**
   - **特斯拉浏览器打开 `https://100.96.0.1:18443/`**（设置了自己的域名就用 `https://子域名.duckdns.org:18443/`），提示证书不安全时点继续；没有继续选项就设置「自己的域名」（下一节，token 让用户自己填）。存为书签。
   - **点网页右上角的全屏**：会经腾讯视频、1905 的跳转页回到本页并去掉地址栏（腾讯那页点「前往 电影网」；这一步要特斯拉能上网）。
4. 验收：实际连接 iPhone，车机看到 CarPlay 图像并能点按；按“收尾检查”确认本次日志 `CarPlay is live`，不能用设置页可打开代替出画。驻车播放音乐，在 iPhone 控制中心确认输出为特斯拉，方向盘切歌成功、媒体卡片曲名随之变化；拨打一通测试电话、唤起 Siri，确认声音均从车机蓝牙且双方可听清。任一失败记录时间、具体音频输出与本次日志，保留为未通过，不只凭 `--tesla` 参数判成功。
5. 偶尔放歌没声音时，在 iPhone 控制中心把音频输出选成特斯拉；Siri 用完音乐不会自动继续，按一下播放。

## 车机打不开画面时（玩客云 / N1 / OpenWrt 都适用）

一般用不到。先问用户**车机屏幕上看到的是什么**（能拍照最好），再只做对应的那一步，不要一上来就全开：

| 车机看到的 | 做什么 |
|---|---|
| 证书不安全，有「高级 → 继续前往」 | 点继续，正常现象，什么都不用设 |
| 证书警告，没有继续选项（如 `NET::ERR_CERT_AUTHORITY_INVALID`） | 先试装盒子 CA 证书；车机不让装，再设置「自己的域名」 |
| 手机能打开同一地址，车机说无法访问或被拦截，地址是 `192.168.x.x` | 车机不让打开局域网地址：设置「自己的域名」并勾选「同时登记公网 IPv6」（玩客云 / N1 方式一、二）；OpenWrt 把「访问名称指向公网 IPv6」选「同时」 |
| 特斯拉 | 按上面「特斯拉」一节（特斯拉模式没开、没打开「行驶中保持连接」、搜不到 WiFi 都在那里） |
| 别的车：局域网地址、名称都打不开，也没有 IPv6 | OpenWrt 勾选「车机不让开任何局域网地址」，同样改网段 |
| 想用车机原生的方向盘切歌 / 媒体卡片，或网页通话有回声 | 车机能用蓝牙连 iPhone 时，勾选「音乐走车机蓝牙」和 / 或「通话与 Siri 走车机蓝牙」（玩客云 / N1 设置页「特斯拉与车机蓝牙」分组；OpenWrt 常规页），iPhone 用蓝牙连车机并选为媒体来源；见安装说明「声音走车机蓝牙」 |
| 手机也打不开 | 地址变了或没连同一个热点，见安装说明第 4 节「地址变了」 |

- 这些选项都在设置页「车机打不开画面时」分组（OpenWrt：后台「服务 → DashPlay → 车机打不开画面时」标签），每一项都写了它解决什么问题；详细步骤和怎么确认生效，照主仓库 `docs/DashPlay-AI安装说明.md` 第 4 节做。
- 「自己的域名」要用户先在 duckdns.org 免费注册子域名。**token 让用户自己在设置页输入**，不要让他发给你，也不要写进命令、文件或日志。
- 「同时登记公网 IPv6」在玩客云 / N1 上会把盒子的公网 IPv6 地址公开登记到 DuckDNS，盒子同时开防火墙只让同一个 WiFi 的设备进来；告诉用户这一点再勾选。方式三（车机连盒子热点）没有公网地址，用不了。
- 设置后正式证书要几分钟申请、**下次开机**启用；之后车机打开 `https://子域名.duckdns.org:18443/`。
- 「车机不让开任何局域网地址」会改掉路由器的局域网地址（改成 `100.96.0.1`），勾选前告诉用户：改完连着它的设备要重连 WiFi，后台地址变成 `http://100.96.0.1/`，你自己的 SSH 也要改连这个地址。

## 收尾检查

- 玩客云 / N1：`ssh root@<盒子地址> 'systemctl is-active dashplay; tail -20 /run/dashplay/receiver.log'`，iPhone 连上后日志里有 `CarPlay is live`。
- OpenWrt：`/etc/init.d/dashplay status`，`grep -E "CarPlay is live|session active" /var/run/dashplay/receiver.log`。
- 告诉用户以后怎么用：上车通电、等一会儿，车机浏览器打开同一个地址；设置页在哪里；指示灯的意思（用户手册“指示灯”一节）。

## 排错

| 现象 | 先查 |
|---|---|
| 设置页顶部提示缺认证文件 / 日志有 `MFi identity missing` | 文件路径、文件名、权限；重新核对配对 |
| 找不到 WiFi `DashPlay` | 等 2 分钟；玩客云查 12 V 电源和网卡是否插好；N1 从 U 盘启动要插靠 HDMI 的口 |
| 方式一、二蓝灯（N1 快闪）一直闪 | 盒子没找到车机热点或随身 WiFi：确认它开着、名称密码对；设置页「立即重试」 |
| iPhone 无线连不上或几秒就断 | 同一部 iPhone 是否正连着别的 CarPlay（另一台盒子、车）；蓝牙和 WiFi 都开着；随身 WiFi 开了设备隔离时只能有线 |
| 车机浏览器打不开、地址变了、证书被拦截、不让打开局域网地址 | 上面「车机打不开画面时」，细节见 `docs/DashPlay-AI安装说明.md` 第 4 节 |
| 画面卡、断线 | 设置页「高级 → 下载诊断记录」；完整日志在盒子 `/opt/dashplay/state/logs/`（每次开机一个目录，断电不丢） |

查不出原因时，把诊断记录和日志里相关的几十行整理好，告诉用户可以到主仓库提 issue（不要附带认证文件、密码、令牌）。
