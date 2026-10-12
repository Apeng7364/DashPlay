# DashPlay（玩客云 / 斐讯 N1）：给 AI 助手的安装说明

把这份说明交给你的 AI 助手（能执行终端命令的那种），再告诉它：盒子的地址、认证文件在你电脑上的位置。它照下面做即可。

## 背景

DashPlay 是刷在玩客云或斐讯 N1 上的 CarPlay 接收盒：iPhone 用数据线插盒子（有线）或经蓝牙引导连 Wi-Fi（无线），车机浏览器打开盒子的网页显示 CarPlay。固件不含 CarPlay 认证文件（MFi 身份），需要用户自己提供两个文件：`identity.pk8`（私钥）和 `certificate.p7b`（证书）。

## 1. 连上盒子

- 电脑和盒子在同一个网络：
  - 盒子自己的热点 `DashPlay`（首次设置前不设密码），盒子地址 `192.168.78.1`；
  - 或者车机热点，盒子地址在手机浏览器打开 `http://dashplay.local/` 的设置页里能看到；
  - 或者用网线把盒子接到路由器，在路由器里查地址。
- SSH 登录：用户名 `root`，密码 `1234`。

```sh
ssh root@<盒子地址>
```

## 2. 放入认证文件

```sh
scp identity.pk8 certificate.p7b root@<盒子地址>:/opt/dashplay/identity/offline-mfi/
ssh root@<盒子地址> 'chmod 600 /opt/dashplay/identity/offline-mfi/*'
```

检查私钥和证书是否配对（只比较公钥指纹，不显示私钥）：

```sh
ssh root@<盒子地址> 'cd /opt/dashplay/identity/offline-mfi &&
  a=$(openssl pkey -inform DER -in identity.pk8 -pubout -outform DER | sha256sum);
  b=$(openssl pkcs7 -inform DER -in certificate.p7b -print_certs | openssl x509 -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum);
  [ "$a" = "$b" ] && echo 配对正确 || echo 不配对'
```

不配对时停下，告诉用户这两个文件不是一套。文件放好后约 2 秒盒子自动重启服务，不需要手动重启。

## 3. 确认在工作

```sh
ssh root@<盒子地址> 'systemctl is-active dashplay; journalctl -u dashplay -n 20 -o cat'
```

- 看到 `MFi identity missing` 说明文件没放对（路径或文件名）。
- 盒子设置页（`http://<盒子地址>/`）顶部不再提示“还缺 CarPlay 认证文件”。
- 已添加的 iPhone 打开蓝牙和 Wi-Fi 后，日志里出现 `CarPlay is live`。
- 还没添加 iPhone 时，让用户按设置页「添加 iPhone」操作（要在车机或另一台设备的浏览器里操作，iPhone 停在蓝牙设置页）。

## 4. 车机浏览器打不开时

- **地址变了**：方式一、二里盒子的地址由车机热点或随身 WiFi 分配，盒子会请求沿用上次的地址，但不保证。手机连同一个热点后打开 `http://dashplay.local/`，设置页「CarPlay 画面」里是当前地址；也可以在车机热点或随身 WiFi 的已连接设备列表里找 `DashPlay`。方式三的地址固定为 `https://192.168.78.1:18443/`。
- **提示证书不安全**：盒子用自己生成的证书，第一次打开时点「高级 → 继续访问」即可。
- **浏览器直接拦截、没有继续选项**：在设置页「高级 → 下载盒子 CA 证书」，把 `dashplay-ca.crt` 装进车机系统的受信任证书（车机支持时）；或换用允许继续访问的浏览器。盒子证书会随地址变化自动重签，装过一次 CA 后不再提示。
- **完全打不开**：确认车机和盒子连在同一个热点上；方式三要让车机连盒子热点 `DashPlay`。

## 常用位置

| 内容 | 位置 |
|---|---|
| 程序 | `/opt/dashplay` |
| 服务 | `systemctl status dashplay` |
| 认证文件 | `/opt/dashplay/identity/offline-mfi/` |
| 运行日志 | `/run/dashplay/`（`receiver.log` 为 CarPlay 接收端） |
| 设置页 | `http://<盒子地址>/` |
| CarPlay 网页 | `https://<盒子地址>:18443/` |
