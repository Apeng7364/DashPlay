---
name: dashplay-install
description: 帮完全不懂技术的用户把 DashPlay（网页 CarPlay）装到斐讯 N1、玩客云、ZTE F50 或其他 OpenWrt 路由器上，从识别硬件、下载刷机包、刷机、放入用户自己的 CarPlay 认证文件，到上车使用和排错，一步步做完；只有插拔电源、按按钮、在手机或车机上点选这类必须本人动手的事才请用户做。用户说“帮我装 DashPlay”“我买了 N1/玩客云/F50，想用网页 CarPlay”时使用。
---

# DashPlay 装机向导（给 AI 助手）

用户把这份文件的地址交给你，意思是：**剩下的事你来做**。你在用户的电脑上执行命令、下载、检查、排错；只在必须本人动手时请用户操作。

项目地址：
- 主仓库（玩客云、N1 刷机包，程序本身）：<https://github.com/yizhixiaoheigou/DashPlay>，发布页 `/releases/latest`
- OpenWrt 软件包（F50 和其他 OpenWrt 路由器）：<https://github.com/yizhixiaoheigou/openwrt-dashplay>，发布页 `/releases/latest`
- 详细参考：主仓库 `docs/DashPlay玩客云固件使用说明.md`（用户手册）、`docs/DashPlay-AI安装说明.md`（认证文件与车机浏览器问题）、openwrt-dashplay 的 `README.md`

## 开始前

- 你需要能在用户电脑上执行命令（Claude Code、Codex 这类）。如果你只能聊天、不能执行命令，告诉用户换一个能执行命令的 AI 助手，或者你把每条命令写出来让他复制执行，并请他把结果贴回来。
- Windows 上用 PowerShell：Windows 10/11 自带 `ssh`、`scp`；解压 `.xz` 用 7-Zip；校验用 `certutil -hashfile 文件 SHA256`。
- 全程大约 30–60 分钟；用户需要准备：设备的电源，N1 还要一个 8 GB 以上的 U 盘，玩客云还要一台 Windows 电脑、一根双公头 USB 线和一把螺丝刀或撬片。

## 与用户相处的规则

1. **用用户的语言，说人话。** 不解释原理，只说现在要做什么、为什么需要他。
2. **需要用户动手时**：单独一行加粗写清楚一个动作（例如“**请把 N1 的电源拔掉，再插回去**”），说明做完会看到什么，然后等他回复。一次只请他做一件事，能自己做的绝不交给他。
3. **先确认再做不可逆的事**：写 U 盘或硬盘、刷机、覆盖盒子内置存储、在用户的路由器上装软件之前，先说清楚会发生什么、会清掉什么，得到同意再做。写磁盘前必须让用户确认目标磁盘（按容量和名称），绝不猜。
4. **CarPlay 认证文件只能由用户自己提供**（`identity.pk8` 和 `certificate.p7b` 两个文件）。不要替用户搜索、下载、从别的软件里提取认证身份，也不要推荐来源；用户没有时，把能做的都做完，告诉他缺这两个文件，停在这一步。认证文件和任何密码不要打印到屏幕、不要上传到任何地方。
5. 盒子的 SSH 默认用户名 `root`、密码 `1234`。装好后可以提议用户改密码（`passwd`），但不强制。
6. 出错先自己查日志、排查（见“排错”），不要把报错原样丢给用户。

## 第 0 步：问清楚

依次问（用户答不上来就帮他判断，例如请他拍照或描述外观）：

1. **买的是哪种设备？** 斐讯 N1 / 玩客云 / ZTE F50 随身 WiFi / 其他已经装了 OpenWrt 的路由器。
2. **电脑是什么系统？** macOS / Windows / Linux。（玩客云刷机必须用 Windows 电脑，见下文。）
3. **车机的情况**：车机能不能开热点、车上有没有随身 WiFi；iPhone 打算插数据线（有线）还是无线。用来在首次设置里选使用方式：
   - 方式一「车机热点」：盒子连车机热点，iPhone 只能插线；
   - 方式二「随身 WiFi」：盒子、车机、iPhone 都连随身 WiFi，插线有线、不插无线；
   - 方式三「车机连盒子热点」：盒子开热点，车机和 iPhone 连它，车机此时不能上网。
4. **有没有 CarPlay 认证文件**（两个文件）以及放在电脑哪里。没有也可以先装，最后一步再放。

然后按设备走对应章节。动手前先说一句总计划（例如“我会先下载刷机包、帮你写进 U 盘，中间需要你插拔两次电源”）。

## 斐讯 N1

N1 有内置 WiFi 和蓝牙，iPhone 的数据线插 N1 的任意 USB 口。

1. **下载**：从主仓库发布页下载 `DashPlay_n1-<日期>.img.xz` 和同名 `.sha256`，校验（macOS/Linux `shasum -a 256 -c`，Windows `certutil -hashfile 文件 SHA256` 对比），再解压出 `.img`（`xz -dk`；Windows 用 7-Zip）。
2. **写 U 盘**（8 GB 以上，会清空）：
   **请把一个不用的 U 盘插到电脑上。**
   - macOS：`diskutil list external` 找到 U 盘（核对容量），请用户确认是 `/dev/diskN`；然后 `diskutil unmountDisk /dev/diskN` 并执行 `sudo dd if=DashPlay_n1-<日期>.img of=/dev/rdiskN bs=4m`（会要用户输入电脑密码），完成后 `diskutil eject /dev/diskN`。
   - Linux：`lsblk` 找到 U 盘并确认，`sudo dd if=… of=/dev/sdX bs=4M conv=fsync status=progress`。
   - Windows：没有安全的命令行方式，请用户安装 balenaEtcher（或 Rufus），你告诉他选哪个文件、哪个 U 盘。
3. **从 U 盘启动**。先问：**这台 N1 以前刷过 Armbian/OpenWrt 等系统吗，还是买来就是斐讯原厂系统？**
   - **刷过别的系统**（大多数二手 N1）：一般直接能从 U 盘启动。
     **请拔掉 N1 的电源，把 U 盘插到靠近 HDMI 的那个 USB 口，再接上电源。**
   - **原厂系统**（以下是社区通行做法，DashPlay 自己只在刷过系统的 N1 上走过；遇到和描述不符的情况先停下问用户）：不用拆机，但要一台显示器（HDMI）和一个 USB 鼠标，电脑要装 ADB（macOS `brew install android-platform-tools`，Windows 下载 Google 的 platform-tools）。**第一次开机前先别让 N1 联网**：原厂系统联网可能自动升级，升级后更难刷。
     1. **请把 N1 接上显示器和鼠标，先不要插网线，接电开机；进入「设置 → 关于」看「固件版本」，把版本号告诉我。**
     2. 版本号高于 **V2.19** 时要先降级（用 fastboot 刷入 V2.19 的 boot、bootloader、recovery 三个分区，有变砖风险）。这一步本向导不提供镜像，按社区的 N1 降级教程做，先向用户说明风险并得到同意；不确定就停下。版本 ≤ V2.19 跳过。
     3. **请用鼠标在「固件版本」上连点 4 次，屏幕下方出现「打开 adb」后告诉我；然后给 N1 插上网线（接到电脑所在的路由器），把屏幕上显示的 IP 地址告诉我。**（这时它能上网了，尽快做完下一步；用户会操作路由器的话，可以先在路由器里禁止 N1 上外网。）
     4. 电脑执行 `adb connect <IP>:5555`，确认连上后，**请把写好的 U 盘准备在手里，插在靠近 HDMI 的那个 USB 口旁边，我说「插」就马上插进去。** 你执行 `adb shell reboot update`，立刻请用户插入 U 盘。屏幕上斐讯标志常亮（不闪）就是从 U 盘启动了；标志一直闪说明回到了原厂系统，重新写一次 U 盘再试。
   第一次从 U 盘开机约 1–2 分钟，会出现名叫 `DashPlay` 的 WiFi（没有密码）。
4. **连上盒子**：让电脑连 WiFi `DashPlay`（macOS 可以用 `networksetup -setairportnetwork <WiFi 接口> DashPlay`，接口名用 `networksetup -listallhardwareports` 查；先问用户可不可以切换电脑的 WiFi；切换后电脑暂时不能上网，所以下载都要在这之前做完）。盒子地址固定 `192.168.78.1`：`ssh root@192.168.78.1`（密码 `1234`）。
5. **装进内置存储**（推荐，以后不用插 U 盘；会清空 N1 原来的系统，先确认）：
   `ssh root@192.168.78.1 "printf '101\n1\n' | armbian-install"`（机型 101 = Phicomm N1，文件系统 1 = ext4）。看到成功后：
   **请拔掉 N1 的电源，拔下 U 盘，再接上电源。**
   约 30 秒后 `DashPlay` WiFi 重新出现。用户想一直从 U 盘运行也可以跳过这一步。
6. 去“放认证文件”，再去“首次设置与上车”。

## 玩客云

玩客云要配一个 RTL8821CU 的 USB 无线网卡（WiFi 和蓝牙二合一，USB ID `0bda:c820`），**必须用 12 V 电源**（5 V 的 USB 转 DC 线会让网卡反复掉线）。刷机用 Amlogic 的 USB Burning Tool，**只能在 Windows 上运行**；用户没有 Windows 电脑时，请他借一台或用虚拟机，你远程指导。

1. **下载**：从主仓库发布页下载 `DashPlay_onecloud-<日期>.burn.img.xz` 和 `.sha256`，校验并解压出 `.burn.img`。
2. **刷机**（会清空玩客云）：在 Windows 上安装 Amlogic USB Burning Tool（安装时一并装上它的 USB 驱动），导入 `.burn.img`。
   - 以前刷过 Armbian 等系统的玩客云，也照这样线刷（DashPlay 刷机包整片覆盖）。
   - 玩客云要**拆开外壳、短接主板上的两个触点**才能进入刷机模式，没有不拆机的办法。先告诉用户这一点。不同板子（如 V1.3 等）短接点不同：请用户拍主板照片或说出板子丝印上的版本，按该版本的公开资料指出位置；不确定就让用户自己查好，不要瞎指。
   **请用双公头 USB 线把玩客云远离网口的那个 USB 口（刷机口）接到电脑，短接主板上的两个触点，同时接上电源；工具里出现设备后松开，点「开始」。**
   刷完拔线、断电。
3. **第一次开机**：
   **请把 USB 无线网卡插到玩客云靠近网口的 USB 口（不是刚才刷机用的口），接上 12 V 电源。**
   第一次开机会自动扩展存储，约 20 秒后出现 WiFi `DashPlay`（没有密码），蓝灯慢闪。iPhone 的数据线以后插远离网口的 USB 口（就是刷机口，用于有线 CarPlay）。
4. 连上盒子同 N1 第 4 步（地址 `192.168.78.1`，root / `1234`）。也可以给玩客云插网线到路由器：它的网口 MAC 每次开机随机，地址会变，在路由器里找主机名 `onecloud`，或扫局域网找能用 root/1234 登录且有 `/opt/dashplay` 的设备。
5. 去“放认证文件”，再去“首次设置与上车”。

## ZTE F50 与其他 OpenWrt 路由器

DashPlay 在 OpenWrt 上是插件，**不提供刷 OpenWrt 的工具**。设备必须已经运行 OpenWrt 25.12（用 `apk` 装包）。F50 出厂是安卓系统，用户没有自己刷过 OpenWrt 的话，告诉他这超出本向导，停下。

1. **确认环境**（SSH 登录路由器，地址和密码问用户；F50 用 USB 线连电脑时一般是 `192.168.77.1`）：
   `cat /etc/openwrt_release; cat /etc/apk/arch; df -h /; free -m`
   需要 `DISTRIB_RELEASE` 为 25.12.x、架构是 `aarch64_*` 或 `x86_64`、约 230 MB 空闲存储、内存 512 MB 以上。不满足就说明原因并停下。
2. **下载并安装**（先说明会往路由器装 Python、Java 等约 225 MB，得到同意）：从 openwrt-dashplay 发布页下载 `dashplay-*.apk`、与 `/etc/apk/arch` 一致的 `dashplay-jre-*_<架构>.apk`、`luci-app-dashplay-*.apk`、`luci-i18n-dashplay-zh-cn-*.apk` 和 `SHA256SUMS`，校验后传到路由器 `/tmp`，然后：
   `apk update && apk add --allow-untrusted /tmp/dashplay-*.apk /tmp/luci-*.apk`
   （包没有签名，所以要 `--allow-untrusted`。）
3. **蓝牙**（无线 CarPlay 需要）：
   - F50：**不要**装系统的 `bluez-daemon`（会带入和 F50 内核不匹配的模块）。执行 openwrt-dashplay README「ZTE F50」一节的四条命令（生成独立蓝牙环境、设置两个选项、重启服务）。
   - 其他路由器：`apk add bluez-daemon`；用 USB 蓝牙棒再加 `kmod-btusb`。没有蓝牙就只能有线。
   - 有线（数据线）需要路由器的 USB 口能当主机：`apk add kmod-tun`。F50 不支持有线。
4. **WiFi**：在路由器后台「服务 → DashPlay」选交给 iPhone 的 WiFi（默认自动选第一个 5 GHz 热点）。网络本身用户怎么设就怎么用，不要改他的网络配置。
5. 去“放认证文件”（路径相同；OpenWrt 上看日志用 `cat /var/run/dashplay/supervisor.log`、`tail /var/run/dashplay/receiver.log`，服务用 `/etc/init.d/dashplay restart`），然后：
   - 设置页 `http://<路由器地址>:18080/`（添加 iPhone）；
   - 车机连路由器的 WiFi，浏览器打开 `https://dashplay.lan:18443/`（或 `https://<路由器地址>:18443/`）。

## 放认证文件（所有设备）

按主仓库 `docs/DashPlay-AI安装说明.md` 第 2、3 节：把用户的两个文件 `scp` 到 `/opt/dashplay/identity/offline-mfi/`、`chmod 600`，用其中的命令核对私钥和证书是否配对（只比较公钥指纹）。不配对就停下，告诉用户这两个文件不是一套。放好后盒子自己会开始工作，不用重启。

## 首次设置与上车（玩客云 / N1）

首次设置在**手机**上做最简单，而且方式一、二要在**车上**做（盒子要连上车机热点或随身 WiFi 才能进行下一步）。你把步骤讲给用户，按用户手册第 2 节：

1. **请在车上给盒子通电，打开车机热点（或随身 WiFi）；手机连上 WiFi「DashPlay」，会自动弹出设置页。**（没弹出就用浏览器打开 `http://192.168.78.1/`。）
2. 选第 0 步商定的使用方式 → 在列表里选车机热点或随身 WiFi 并输入密码（同时给盒子自己的热点设个密码）；方式三是设置盒子热点名称和密码。
3. 连接 iPhone：插数据线的，第一次在 iPhone 上点「信任」并输入锁屏密码；要无线的，在车机屏幕上点「添加 iPhone」，iPhone 打开「设置 → 蓝牙」停在那一页，核对六位数字后两边确认。
4. 车机浏览器打开设置页上列出的 CarPlay 地址（方式一、二是 `https://<盒子地址>:18443/`，方式三固定 `https://192.168.78.1:18443/`），提示证书不安全时点「继续访问」。

用户手边有笔记本电脑并且在车上时，你也可以让电脑连 `DashPlay`，读 `http://192.168.78.1/` 页面里的表单代他提交；选择方式和输入热点密码仍然要问用户。

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
| 车机浏览器打不开、地址变了、证书被拦截 | `docs/DashPlay-AI安装说明.md` 第 4 节 |
| 画面卡、断线 | 设置页「高级 → 下载诊断记录」；完整日志在盒子 `/opt/dashplay/state/logs/`（每次开机一个目录，断电不丢） |

查不出原因时，把诊断记录和日志里相关的几十行整理好，告诉用户可以到主仓库提 issue（不要附带认证文件、密码、令牌）。
