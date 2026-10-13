# DashPlay

[English](README.en.md)

把带 Wi-Fi 和蓝牙的 Linux 小盒子变成 CarPlay 接收端：iPhone 用数据线插盒子（有线 CarPlay），或经蓝牙引导连上 Wi-Fi（无线 CarPlay）；车机或任何设备用浏览器打开盒子的网页，就能显示画面、播放声音并触控操作。

DashPlay 基于 [PlayPort](https://github.com/youcci/playport)，提供补丁和配套运行程序：有线 CarPlay（盒子作 USB 主机）、Linux BlueZ 蓝牙桥接、面向车机浏览器的精简界面与系统媒体控制、首次设置与设置页、开机自启与监管等。

**不懂技术也能装**：买好斐讯 N1、玩客云、ZTE F50（已刷 OpenWrt）或其他 OpenWrt 路由器后，把下面这句话发给你的 AI 助手（Claude Code、Codex、WorkBuddy、豆包、DeepSeek 等都可以），它会问清楚你的设备，一步步帮你装好，只在需要你插拔电源、在手机上点选时叫你：

> 请阅读 https://github.com/yizhixiaoheigou/DashPlay/blob/main/skills/dashplay-install/SKILL.md ，按它一步步帮我把 DashPlay 装好。

> **这不是 Apple 认证产品。** 运行需要你自己提供配件认证身份，见下文“认证身份”。

## 当前状态

### 三种使用方式（首次设置第 1 步选择）

| | 方式一：车机热点 | 方式二：随身 WiFi | 方式三：车机连盒子热点 |
|---|---|---|---|
| 怎么连 | 盒子连车机热点 | 盒子、车机、iPhone 都连随身 WiFi | 盒子开热点，车机和 iPhone 连它 |
| 车机能否上网 | 能 | 能（经随身 WiFi） | 不能 |
| iPhone | 只用有线 | 插线用有线，没插用无线 | 插线用有线，没插用无线 |

方式一不提供无线：车机热点在两台客户端之间转发 iPhone 画面实测只有约 0.1 Mbit/s。插着线时盒子不发无线邀请，拔线后立即邀请上次的 iPhone 走无线，再插回又改走有线。

### 硬件

- **玩客云（S805，Armbian / Debian 13 armhf）+ RTL8821CU USB 网卡**（Wi-Fi 与蓝牙二合一，`0bda:c820`），12 V 供电：主力平台，有线 CarPlay 用远离网口的 USB 口（OTG，盒子切成 USB 主机）。线刷固件后手机连盒子热点 `DashPlay` 完成首次设置，由用户自己的 AI 助手放入认证文件即可使用。刷机与使用见 [固件使用说明](docs/DashPlay玩客云固件使用说明.md)，给 AI 的步骤见 [AI 安装说明](docs/DashPlay-AI安装说明.md)；固件构建工具见 `scripts/onecloud/firmware/`。
- **斐讯 N1（S905D，aarch64，内置 BCM43455 Wi-Fi/蓝牙）**：刷机包是预装 DashPlay 的 U 盘镜像（基于 [ophub amlogic-s9xxx-armbian](https://github.com/ophub/amlogic-s9xxx-armbian) 的 Debian trixie），U 盘启动后可用 `armbian-install` 装进 eMMC；步骤同见 [固件使用说明](docs/DashPlay玩客云固件使用说明.md)。两个 USB 口都是主机口，iPhone 插哪个都行。镜像由 `scripts/onecloud/firmware/make-n1-image.sh` 在一台 N1 上生成。
- **已经装好 OpenWrt 25.12 的路由器或随身 WiFi（aarch64 / x86_64，含 ZTE F50）**：OpenWrt 插件在姊妹仓库 [openwrt-dashplay](https://github.com/yizhixiaoheigou/openwrt-dashplay)（apk 包与软件源），网络照常在 OpenWrt 后台设置。插件运行的就是本仓库的程序：每次发布附带的 `dashplay-bundle-<版本>.tar`。本仓库不包含为 F50 刷入 OpenWrt 的任何工具或固件。

### 实测情况

玩客云三种方式各从恢复出厂走完首次设置（家中用手机热点代替车机热点和随身 WiFi），插拔线有线/无线互切、断电重启、重置；**小鹏车机实车方式一（有线）**：首次设置、连续约 27 分钟、断电重开、切换画面后 8–12 s 恢复，30–50 帧、0 丢帧。斐讯 N1 方式一在家实测（画面、点按、声音、封面）。有线音乐为无损 48 kHz（网页以 FLAC 封装交给媒体元素播放），专辑信息与封面进入系统媒体控制，方向盘可切歌。多部 iPhone 轮流使用（无线）。ZTE F50（OpenWrt 插件）在家实测：通电约 37–44 s 进入 CarPlay；iPhone 离开 WiFi 后不再邀请，连回约 4 s 恢复；重启自动邀请上次那部。

### 换其他 Linux 盒子

需要：Wi-Fi 客户端（方式一、二）或热点（方式三，hostapd）；经典蓝牙（BlueZ，仅无线 CarPlay 需要）；一个 USB 主机口（有线 CarPlay）；Java 21（Debian trixie / Ubuntu noble）。`scripts/onecloud/platform_api.py` 是平台接口，`platform_debian.py` 是 Debian/Armbian 实现；`scripts/onecloud/board-probe.sh` 只读检查新板子的这些条件。

## 认证身份

iPhone 只会连接出示有效配件证书、并能用对应私钥签名的接收端。DashPlay 与 PlayPort 一样**不附带也不分发任何身份**。仓库、安装包、文档和测试都不包含真实身份文件；测试所需身份只在运行时生成合成数据。本包的本地认证模式缺少以下两个文件时接收服务拒绝启动：

```
identity/offline-mfi/identity.pk8       # PKCS#8 EC P-256 私钥
identity/offline-mfi/certificate.p7b    # 对应的配件证书
```

身份来源请阅读 [PlayPort README 的 Accessory identity 一节](https://github.com/youcci/playport#accessory-identity-required)。它列出的途径包括：DiPlay 发布 APK 中的实验身份、Carlinkit 固件、你自己作为 MFi 授权方拥有的身份，以及 `--mfi-server` 远程认证服务。远程认证是上游提供的方式，DashPlay 的启动脚本使用上述本地文件。DashPlay 不自动下载身份，也不提供从 APK 一键导入或身份提取脚本。请注意：

- 公开可得的身份是共享的实验身份，并非签发给本项目或 PlayPort，Apple 随时可能使其失效；
- 只用于个人实验，不要出售，不要再分发身份文件，不要提交到任何仓库；
- 身份文件应只保存在设备私有目录、仅 root 可读。

## 构建

需要 JDK 25（构建）、Node.js（网页）和 Git。

```sh
git clone https://github.com/youcci/playport.git
git -C playport checkout 9a0882dd0ffe48e467b59d58b12d81391df55ade
sh scripts/apply-playport-patches.sh playport
(cd playport && ./gradlew :protocol:test :server:test :server:installDist)
(cd playport/web && npm ci && npm test && npm run build)   # FLAC 检查需要 ffmpeg，没有则跳过
sh scripts/onecloud/firmware/build-bundle.sh dashplay-bundle.tar   # 程序包（盒子安装与 OpenWrt 包都用它）
```

`scripts/build-playport.sh` 可在隔离 Docker 容器中完成同样的 Gradle 构建与测试。盒子上运行需要 Java 21 和带 asyncio 的 Python 3：玩客云/N1 由 `install.sh` 从 Debian 安装，OpenWrt 由插件的 `dashplay-jre`（Temurin 21 musl 版）与系统软件源提供。

## 运行（玩客云 / 斐讯 N1）

- `scripts/onecloud/dashplay.service` 启动 `supervisor.py`（安装在 `/opt/dashplay`）：按所选使用方式联网（连车机热点或随身 WiFi、或开盒子热点；连不上时临时开盒子热点进设置）、蓝牙、接收服务与设置页（80 端口，`http://dashplay.local/`）。
- `setup_wizard.py`、`settings_page.py`、`settings_ui.py`：首次设置（使用方式 → 网络 → iPhone）与盒子设置页；`phone-presence.py`、`pairing_window.py`、`pair-phone.py`：多部 iPhone 的添加与轮换（无线）。
- `linux/bt-bridge/`：PlayPort 的 Linux 蓝牙桥，通过 BlueZ D-Bus 建立 iAP2 RFCOMM；`BT_BRIDGE_READY_WAIT` 允许在蓝牙晚于接收服务就绪时等待。
- `iphone-usb-bridge.py`：有线 CarPlay 的 USB 侧——把 iPhone 切到 CarPlay 配置、转发 USBMUX、在用户态桥接 NCM 网卡；iPhone 第一次插上时点「信任」，信任记录按 iPhone 分别保存。
- `run-receiver.py` 生成接收配置并通过 `launcher/onecloud/Launcher.java` 启动 PlayPort；`diagnostics.py` 每 10 秒把网卡、温度、播放统计写入 `state/diag/`，每次开机的完整日志存在 `state/logs/`（断电不丢，设置页可下载诊断记录）。
- 固件与安装：`scripts/onecloud/firmware/README.md`。`build-bundle.sh` 只用本仓库打安装包，`install.sh` 装到 Armbian 上，`prepare-image.sh` 清除所有个人数据，`pack-image.sh` 重建玩客云线刷包。

## 运行（OpenWrt）

- `supervisor_openwrt.py` 与 `platform_openwrt.py`：OpenWrt 上的主控与平台层，只读取 OpenWrt 的网络（`ubus`），把选中的 WiFi 经蓝牙交给 iPhone，WiFi 改了自动跟上；没有蓝牙时只提供有线。`board_zte_f50.py`：ZTE F50 的内置蓝牙初始化与独立 BlueZ。打包、安装与使用见 [openwrt-dashplay](https://github.com/yizhixiaoheigou/openwrt-dashplay)。
- `scripts/onecloud/firmware/build-bundle.sh` 生成的程序包（发布页的 `dashplay-bundle-<版本>.tar`）就是 OpenWrt 包的内容。
- `scripts/carplay-night-probe.mjs`：截取当前 CarPlay 画面（夜间模式检查）。

## 测试

```sh
python3 -m unittest discover -s tests/bluetooth     # 需要 dbus-daemon 与 dbus-next，测试身份均为合成数据
python3 -m unittest discover -s tests/onecloud      # 设置、多手机、平台、诊断、固件打包；证书测试需 OpenSSL 3
```

## 已知问题

- 播放页窗口和 CarPlay 画面形状接近时（如普通模式与全屏）按轴拉伸铺满、不重连；第一次进入全屏时按全屏尺寸重连一次（约 8–12 s），之后盒子记住；竖屏、分屏等形状差很多时立即重连。
- 方式一只能有线；方式三车机连着盒子热点时不能上网；方式二若随身 WiFi 开了设备隔离，无线会卡或连不上，只能有线。
- 车机浏览器第一次打开会提示证书不安全，点「继续访问」即可；车机 IP 变化、浏览器直接拦截自签证书、不让打开局域网地址时，设置页「车机打不开画面时」分组有对应选项，处理步骤见 [AI 安装说明](docs/DashPlay-AI安装说明.md) 第 4 节。
- 打电话和 Siri 用车机的麦克风（经浏览器）、从车机的媒体声道出声（不是头枕喇叭）；车机麦克风给浏览器的音量很小，盒子放大后对方能听清，但可能听到自己的回声。iPhone 同时连着车机自带蓝牙时，车机自己也进入通话、占住麦克风，对方听不到你：用 CarPlay 打电话时请断开 iPhone 与车机自带蓝牙。
- 车机自己的应用（如短视频）放声音时会让 iPhone 暂停，回来后不会自动继续：按一次方向盘或车机音乐卡片的播放键即可。
- 玩客云供电不足（普通 5 V USB 转 DC 线）会让 USB 网卡反复掉线；请用 12 V 电源或 5→12 V 升压线。
- 斐讯 N1 的 5 GHz 盒子热点只能 40 MHz。
- SSH 默认 `root` / `1234`，方便用户的 AI 助手放入认证文件；连上同一网络的人都能登录。
- 网页夜间模式指令已接入，但尚未证实 iPhone 会据此切换外观。

## 盒子设置图标

![盒子设置](assets/box-settings.png)

图标由项目几何绘图脚本重新生成，不使用第三方图标素材。可编辑来源、复现方法与许可见 [图标来源](assets/ICON-SOURCE.md)。接收端内部资源名保留 `f50-settings.png` 以兼容已有代码，产品名为 DashPlay。

## 许可与商标

GPL-3.0，见 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。CarPlay、iPhone、AirPlay 和 MFi 是 Apple Inc. 的商标；本项目与 Apple、PlayPort、DiPlay、xcertplay、WheelPlay 均无从属或背书关系。
