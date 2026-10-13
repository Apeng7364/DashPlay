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

先问清楚**车机屏幕上看到的是什么**，再按下表处理。设置页里这几项都在「车机打不开画面时」分组（OpenWrt 在后台「服务 → DashPlay → 车机打不开画面时」标签）。一般用不到，按顺序只做需要的那一步。

| 车机看到的 | 原因 | 怎么办 |
|---|---|---|
| 「无法访问此网站」，手机也打不开 | 地址变了或没连同一个热点 | 见下面「地址变了」 |
| 「您的连接不是私密连接」，有「高级 → 继续前往」 | 盒子用自己签的证书 | 点继续即可，正常现象 |
| 证书警告，**没有**继续选项（如 `NET::ERR_CERT_AUTHORITY_INVALID`） | 车机浏览器不让跳过自签证书 | ① 装盒子 CA 证书；② 车机不让装，就设置「自己的域名」 |
| 手机能打开同一地址，车机提示无法访问或被拦截，地址是 `192.168.x.x` | 车机浏览器不让打开局域网地址 | 「自己的域名」并勾选「同时登记公网 IPv6」（只适用于方式一、二）；OpenWrt 把「访问名称指向公网 IPv6」选「同时」 |

### 地址变了

方式一、二里盒子的地址由车机热点或随身 WiFi 分配，盒子会请求沿用上次的地址，但不保证。手机连同一个热点后打开 `http://dashplay.local/`，设置页「CarPlay 画面」里是当前地址；也可以在车机热点或随身 WiFi 的已连接设备列表里找 `DashPlay`。方式三的地址固定为 `https://192.168.78.1:18443/`。车机和盒子要连在同一个热点上；方式三要让车机连盒子热点 `DashPlay`。

### 装盒子 CA 证书

设置页「车机打不开画面时 → 下载盒子 CA 证书」，把 `dashplay-ca.crt` 装进车机系统的受信任证书（车机支持时）。盒子证书会随地址变化自动重签，装过一次 CA 后不再提示。

### 自己的域名（DuckDNS + 免费正式证书）

1. 用户在 duckdns.org 免费注册，建一个子域名（如 `mycar`），记下页面上的 token。**token 只让用户自己在设置页输入**，不要写进对话、文件或日志。
2. 设置页「车机打不开画面时 → 自己的域名」填子域名和 token，保存；OpenWrt 在后台「服务 → DashPlay → 车机打不开画面时」填「自己的域名」和「DuckDNS token」。
3. 盒子把 `子域名.duckdns.org` 的公网解析指向车机访问它的地址（方式一、二是盒子在热点里的地址，变了自动改；方式三固定 192.168.78.1；OpenWrt 是路由器地址），并用内置的 acme.sh 向 Let's Encrypt 申请免费正式证书（DNS 验证，不需要开放端口），到期前 30 天自动续签。设置页这一项会显示进度和有效期。
4. 证书申请到后**下次开机**启用，之后车机打开 `https://子域名.duckdns.org:18443/`（设置页「CarPlay 画面」会把它排在第一个）。

- 申请和续签要盒子能上网：方式一、二在车上就有网；方式三车上没网，要在家插上网线开机。日志里看 `duckdns ... OK` 和 `certificate for ... installed`；acme.sh 的详细输出在 `/opt/dashplay/state/acme/acme.log`（0600，可能含账号信息，不要整段贴出）。
- 先确认这条路对这台车机有用：只填好域名、等解析生效后，在车机打开 `https://子域名.duckdns.org:18443/`。如果出现的仍是证书警告，说明车机接受这个域名，等证书即可；如果是「无法访问」或被拦截，多半是车机或它用的 DNS 不接受指向局域网地址的域名（有的路由器、随身 WiFi 有「DNS 重绑定保护」），这时再勾选下面的「同时登记公网 IPv6」。
- 已有别处申请好的证书（含完整链）时，也可以直接放进 `/opt/dashplay/state/tls/own/fullchain.pem` 与 `key.pem`（目录 0700、私钥 0600），重启服务后生效；它过期前一天自动退回盒子自带证书。这种方式需要自己按时更换。

### 同时登记公网 IPv6（车机不让打开局域网地址时）

- **用途**：有的车机浏览器拒绝打开 `192.168.x.x` 这类局域网地址。勾选后，盒子把自己的公网 IPv6 地址也登记到「自己的域名」：车机拿到的是公网地址，但这个地址就是同一个 WiFi 里的盒子，画面仍在 WiFi 里直接传，不经过外网、不耗流量。
- **条件**：只适用于方式一、二（盒子连车机热点或随身 WiFi），且这个热点要给设备分配 IPv6；方式三盒子热点不连外网，没有公网地址。要先设置好「自己的域名」，勾选框在同一个表单里。
- **安全**：这个地址登记在公开的 DuckDNS 上，谁都能查到，所以盒子同时启用 IPv6 防火墙，只让同一个 WiFi（同一网段前缀）和链路本地地址的设备连进盒子，外网连不进来（包括 SSH）。防火墙启用不了时盒子不会登记这个地址，设置页会说明。热点重新拨号换了地址，盒子一分钟内跟着改。
- **检查**：设置页这一项显示「公网 IPv6 … 已登记」才算生效；「没有给盒子分配公网 IPv6」说明热点不给 IPv6，这个办法用不了。在盒子上 `ip -6 addr show scope global` 能看到地址，`nft list table inet dashplay_v6` 能看到防火墙规则。
- **OpenWrt 路由器**做法不同、也更简单：路由器自己回答这个名字，地址不公开，也不需要额外防火墙（OpenWrt 自带）。在「访问名称指向公网 IPv6」选「同时」；「只用」只用来确认这条路是否通。

## 常用位置

| 内容 | 位置 |
|---|---|
| 程序 | `/opt/dashplay` |
| 服务 | `systemctl status dashplay` |
| 认证文件 | `/opt/dashplay/identity/offline-mfi/` |
| 运行日志 | `/run/dashplay/`（`receiver.log` 为 CarPlay 接收端） |
| 设置页 | `http://<盒子地址>/` |
| CarPlay 网页 | `https://<盒子地址>:18443/` |
