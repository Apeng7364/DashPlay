# Linux / OpenWrt 蓝牙桥接

面向 PlayPort `9a0882dd0ffe48e467b59d58b12d81391df55ade`，使用 BlueZ D-Bus Profile API 实现 iAP2 RFCOMM 客户端。Python 3.11+ 与纯 Python dbus-next（版本与 wheel 哈希固定）避免引入 CPU 架构相关的桥接二进制；F50 ARM64 和玩客云 ARMv7 已完成真实 iPhone 引导与 CarPlay 基础闭环；驱动初始化仍由各设备运行脚本负责。

## 接口

```sh
./bt-bridge --print-address
./bt-bridge --list
./bt-bridge --address AA:BB:CC:DD:EE:FF
```

- 与 PlayPort 现有调用兼容。连接模式 stdout 只包含二进制数据，诊断写 stderr。
- 可用 `--adapter hci0` 或环境变量 `BT_BRIDGE_ADAPTER=hci0` 选择控制器。多控制器未指定时失败，避免地址查询和连接使用不同设备。
- `--timeout 15` 限制 D-Bus 操作和 RFCOMM 建立；连接后不因正常空闲断开。
- 只连接已经配对且未屏蔽的设备。不会自动配对、信任、开启适配器、改热点或修改 Wi-Fi 配置。
- 初次配对使用 BlueZ 自带的交互式 `bluetoothctl`，须在实际 iPhone 上核对配对码。本桥接不实现自动确认配对或 `--force-pair`。
- `RegisterProfile` 使用 iAP2 UUID、client 角色、RequireAuthentication=true、AutoConnect=false；不写死 RFCOMM channel，由 BlueZ 查询 SDP。
- 单连接的两个方向各最多缓存 64 KiB，处理短写和背压。stdin EOF 意味着关闭整个桥接；远端 EOF 在输出已接收数据后退出。SIGINT/SIGTERM、BlueZ/总线退出都会收尾。
- 仅接受当前 BlueZ 所有者针对目标设备的回调；拒绝重复 fd、错误设备和伪造来源。
- 清理只调用目标设备的 DisconnectProfile(iAP2) 和本进程的 UnregisterProfile，不调用全设备 Disconnect，不删除配对信息。

## 安装与接入

在项目隔离 Python 环境安装，或安装到本目录 `deps/`，不要修改共享 Python：

```sh
python3 -m pip install --only-binary=:all: --require-hashes \
  --target /opt/playport/linux-bt-bridge/deps \
  -r /opt/playport/linux-bt-bridge/requirements.txt
```

OpenWrt 验证用到的解释器模块为 python3-light、python3-asyncio、python3-logging、python3-xml；测试额外需要 python3-unittest。实际使用还需要可工作的 BlueZ、系统 D-Bus、经典蓝牙驱动和已完成配对的 iPhone。可在构建机下载 wheel 后离线复制，不要求 F50 安装 pip。

PlayPort 启动时显式配置 `--bt-bridge /opt/playport/linux-bt-bridge/bt-bridge`，需要指定设备时使用已有的 `--bt-address` 参数。本轮没有修改其 macOS 默认路径。Linux 必须提供正确的 Wi-Fi SSID/信道等配置；原有 macOS 自动探测逻辑不能自动读取 OpenWrt UCI。

必须同时应用 [ProcessByteStream 补丁](../../patches/playport-process-byte-stream.patch)。原有分段读取会把剩余数据排到后续块之后，破坏 iAP2 字节顺序；补丁保留原顺序并稳定返回 EOF。它包含确定性回归测试，原代码失败、修复后通过。

```sh
git -C /path/to/playport apply --check /path/to/project/patches/playport-process-byte-stream.patch
git -C /path/to/playport apply /path/to/project/patches/playport-process-byte-stream.patch
```

## 无硬件测试

在项目根作为 Docker build context：

```sh
docker build -t f50-bt-bridge-test:local -f linux/bt-bridge/Dockerfile.test .
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --memory 256m --pids-limit 128 f50-bt-bridge-test:local
```

测试使用独立 dbus-daemon、模拟 org.bluez 服务和真实 Unix socketpair/fd 传递。测试数据均为合成字节，不能证明真实 SDP、射频、配对或 CarPlay 成功。

若具备编译后的 PlayPort，可用 JDK 编译 `tests/bluetooth/JvmBridgeProbe.java`（`--release 21`，classpath 指向 server/lib/*），设置 `JAVA_BRIDGE_TEST_BIN`、`JAVA_BRIDGE_TEST_CLASSPATH` 后运行 unittest，启用实际 ProcessByteStream → 子进程 → D-Bus fd → socketpair 的往返测试。未设置时该项明确跳过。

`ManagedBus` 对固定 dbus-next 0.2.3 的 socket/makefile 清理做了局部补充，没有修改发行依赖。升级依赖时须复核这一兼容实现。

## 硬件验证与权限

真实射频、配对和 RFCOMM 需要目标设备上可用的 BlueZ 与经典蓝牙。首次配对须人工核对码。容器内无硬件测试不能证明硬件通过；公开导出不包含私有 NAS 扫描探针，不提供依赖该探针的运行命令。

NAS 历史实验中，仅 NET_ADMIN/NET_RAW 不足以让 bluetoothd 绑定 SDP/GATT；经具体授权补充 NET_BIND_SERVICE 后完成手机配对与 RFCOMM。该实验不授权永久 host 网络部署，也不替代每台设备的驱动与权限检查。

## 实机验证边界

F50 和玩客云已验证实际配对、SDP/RFCOMM、认证及 Wi-Fi 交接，不能据此外推所有适配器。首次配对仍需人工核对码；长期运行、车辆供电、麦克风/通话不属于桥接软件测试保证。公开导出目前只带 F50 运行脚本。

来源：[BlueZ Profile API](https://bluez.readthedocs.io/en/latest/profile-api/)、[Device API](https://bluez.readthedocs.io/en/latest/device-api/)、[BlueZ profile.c](https://github.com/bluez/bluez/blob/5.72/src/profile.c)、PlayPort macOS 桥接进程接口。上游补丁沿用 PlayPort 的 GPL-3.0 许可。
