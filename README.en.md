# DashPlay

[中文](README.md)

DashPlay turns a small Linux box with Wi-Fi and Bluetooth into a CarPlay receiver. The iPhone plugs into the box with a cable (wired CarPlay) or is guided onto Wi-Fi over Bluetooth (wireless CarPlay); the head unit, or any device, opens the box's web page in a browser and gets the picture, the sound and touch control.

DashPlay is built on [PlayPort](https://github.com/youcci/playport): patches plus the programs around it, namely wired CarPlay (the box as USB host), a Linux BlueZ Bluetooth bridge, a lean page for head-unit browsers with system media controls, first-run setup and a settings page, and a supervisor that starts everything at boot.

**No technical background needed**: once you have a Phicomm N1, a OneCloud (玩客云), a ZTE F50 on stock B16 or OpenWrt or another OpenWrt router, send this sentence to your AI assistant (Claude Code, Codex, WorkBuddy, Doubao, DeepSeek and the like all work). It asks which device you have and installs DashPlay step by step, calling you only to plug or unplug power or tap something on your phone:

> Please download https://raw.githubusercontent.com/yizhixiaoheigou/DashPlay/main/skills/dashplay-install/SKILL.md with curl in a terminal and read all of it (not through a web summarizer, which may return an old or shortened copy), then follow it step by step to install DashPlay for me.

The guide itself is in Chinese; assistants follow it in whatever language you write to them.

For an N1, append the sentence that matches its current system. On stock firmware, connect a display and read the version on the Phicomm home screen.

| N1 starting point | Sentence to add |
|---|---|
| Already running Linux | My N1 runs Linux and is reachable over Ethernet. I have a USB drive of at least 8 GB that can be erased. |
| Stock Phicomm, version 2.19 or earlier | My N1 runs stock Phicomm firmware, version 2.19 or earlier. I have a display, a USB mouse and a USB drive of at least 8 GB that can be erased. |
| Stock Phicomm, later than 2.19 | My N1 runs stock Phicomm firmware later than 2.19, for example 2.22. I have a display, a USB mouse, a USB-A male-to-male cable and a USB drive of at least 8 GB that can be erased. |

The already-Linux route has been tested on an N1. Both stock-firmware routes come from public guides and have not been tested by this project. If the box runs Android TV or cannot boot, say so instead of choosing a Linux or stock starting point.

> **This is not an Apple-certified product.** You have to supply your own accessory authentication identity; see "Authentication identity" below.

## Status

### Three ways of use (chosen in step 1 of first-run setup)

| | 1: car hotspot | 2: portable Wi-Fi | 3: car joins the box hotspot |
|---|---|---|---|
| Connections | box joins the car's hotspot | box, head unit and iPhone join a portable Wi-Fi | box raises a hotspot; head unit and iPhone join it |
| Head unit has internet | yes | yes (through the portable Wi-Fi) | no |
| iPhone | cable only | cable when plugged in, wireless otherwise | cable when plugged in, wireless otherwise |

Way 1 offers no wireless CarPlay: car hotspots relay the iPhone's picture between two clients at only about 0.1 Mbit/s (measured). While a cable is plugged in the box sends no wireless invitations; when it is unplugged the last iPhone is invited to go wireless at once, and plugging back in returns to wired.

### Hardware

- **OneCloud (S805, Armbian / Debian 13 armhf) with an RTL8821CU USB adapter** (Wi-Fi and Bluetooth in one, `0bda:c820`), 12 V supply: the main platform. Wired CarPlay uses the USB port away from the Ethernet port (OTG, switched to USB host). After flashing, a phone joins the box hotspot `DashPlay` for first-run setup, and the user's own AI assistant puts the authentication files in place. Flashing and use: [firmware guide (Chinese)](docs/DashPlay玩客云固件使用说明.md); steps for an AI: [AI install notes (Chinese)](docs/DashPlay-AI安装说明.md); firmware build tools: `scripts/onecloud/firmware/`.
- **Phicomm N1 (S905D, aarch64, built-in BCM43455 Wi-Fi/Bluetooth)**: the image is a USB-stick image with DashPlay preinstalled (Debian trixie from [ophub amlogic-s9xxx-armbian](https://github.com/ophub/amlogic-s9xxx-armbian)); after booting from the stick, `armbian-install` copies it to the eMMC. Same [firmware guide](docs/DashPlay玩客云固件使用说明.md). Both USB ports are host ports, so the iPhone can use either. `scripts/onecloud/firmware/make-n1-image.sh` makes the image on an N1.
- **Routers or portable Wi-Fi already running OpenWrt 25.12 (aarch64 / x86_64, including the ZTE F50)**: the OpenWrt plugin lives in the sister repository [openwrt-dashplay](https://github.com/yizhixiaoheigou/openwrt-dashplay) (apk packages and a package feed); the network stays configured in OpenWrt as usual. The plugin runs this repository's program, the `dashplay-bundle-<version>.tar` attached to each release. The [installation skill](skills/dashplay-install/SKILL.md) includes the stock F50 B16 backup, root and SD-card OpenWrt procedure and supporting scripts. This weakens boot verification and recovery may require opening the device. Stop on other firmware unless a reliable downgrade route is verified.

### Tested

OneCloud: each way of use from factory reset through first-run setup (at home, with a phone hotspot standing in for the car hotspot and the portable Wi-Fi), switching between cable and wireless by plugging and unplugging, power cycles, reset. **On the road in an Xpeng, way 1 (wired)**: first-run setup, about 27 minutes continuous, power off and on, the picture back 8–12 s after switching screens, 30–50 fps with no dropped frames. Phicomm N1 way 1 at home (picture, touch, sound, artwork). Wired music is lossless 48 kHz (the page wraps it in FLAC for the media element); album information and artwork reach the system media controls, and the steering-wheel buttons change tracks. Several iPhones take turns (wireless). ZTE F50 (OpenWrt plugin) at home: CarPlay about 37–44 s after power-on; no invitations after the iPhone leaves the Wi-Fi, back in about 4 s when it returns; after a reboot the last iPhone is invited.

### Other Linux boxes

They need a Wi-Fi client (ways 1 and 2) or access point (way 3, hostapd); classic Bluetooth (BlueZ, for wireless CarPlay only); a USB host port (wired CarPlay); Java 21 (Debian trixie / Ubuntu noble). `scripts/onecloud/platform_api.py` is the platform interface and `platform_debian.py` the Debian/Armbian implementation; `scripts/onecloud/board-probe.sh` checks a new board for these, read-only.

## Authentication identity

An iPhone only connects to a receiver that presents a valid accessory certificate and signs with the matching private key. Like PlayPort, DashPlay **neither includes nor distributes any identity**. The repository, packages, documentation and tests contain no real identity files; tests generate synthetic ones at runtime. In local authentication mode the receiver refuses to start without these two files:

```
identity/offline-mfi/identity.pk8       # PKCS#8 EC P-256 private key
identity/offline-mfi/certificate.p7b    # the matching accessory certificate
```

The easiest source is the Android APK that [DiPlay](https://github.com/shihabal3amri/DiPlay/releases) publishes: take the newest `DiPlay-*.apk` in its release list; the two files are under `assets/offline-mfi/` in it. The steps (including the check that key and certificate match) are in section 2 of the [install notes for AI assistants (Chinese)](docs/DashPlay-AI安装说明.md), which your AI assistant can follow for you. Other sources are in the [Accessory identity section of PlayPort's README](https://github.com/youcci/playport#accessory-identity-required): Carlinkit firmware, an identity you hold as an MFi licensee, and the `--mfi-server` remote authentication service (upstream's option; DashPlay's start scripts use the local files above). DashPlay itself ships no identity and downloads none. Please note:

- publicly available identities are shared experimental ones, not issued to this project or to PlayPort, and Apple may revoke them at any time;
- use them for personal experiments only: do not sell them, redistribute the files or commit them to any repository;
- keep identity files in the device's private directory, readable by root only.

## Build

Needs JDK 25 (build), Node.js (web page) and Git.

```sh
git clone https://github.com/youcci/playport.git
git -C playport checkout 9a0882dd0ffe48e467b59d58b12d81391df55ade
sh scripts/apply-playport-patches.sh playport
(cd playport && ./gradlew :protocol:test :server:test :server:installDist)
(cd playport/web && npm ci && npm test && npm run build)   # the FLAC check needs ffmpeg and is skipped without it
sh scripts/onecloud/firmware/build-bundle.sh dashplay-bundle.tar   # the program bundle (box installs and the OpenWrt packages)
```

`scripts/build-playport.sh` runs the same Gradle build and tests in an isolated Docker container. On the box DashPlay needs Java 21 and Python 3 with asyncio: OneCloud and N1 get them from Debian through `install.sh`; OpenWrt from the plugin's `dashplay-jre` (Temurin 21, musl build) and the system package feeds.

## Running (OneCloud / Phicomm N1)

- `scripts/onecloud/dashplay.service` starts `supervisor.py` (installed in `/opt/dashplay`): the network for the chosen way of use (joining the car hotspot or portable Wi-Fi, or raising the box hotspot; when the hotspot cannot be joined, the box hotspot opens temporarily for settings), Bluetooth, the receiver and the settings page (port 80, `http://dashplay.local/`).
- `setup_wizard.py`, `settings_page.py`, `settings_ui.py`: first-run setup (way of use → network → iPhone) and the box settings page; `phone-presence.py`, `pairing_window.py`, `pair-phone.py`: adding iPhones and taking turns (wireless).
- `linux/bt-bridge/`: PlayPort's Linux Bluetooth bridge, which opens the iAP2 RFCOMM channel through BlueZ D-Bus; `BT_BRIDGE_READY_WAIT` lets it wait when Bluetooth comes up after the receiver.
- `iphone-usb-bridge.py`: the USB side of wired CarPlay. It switches the iPhone to its CarPlay configuration, forwards USBMUX and bridges the NCM network in user space; the first time an iPhone is plugged in, tap "Trust" on it; trust records are kept per iPhone.
- `run-receiver.py` writes the receiver configuration and starts PlayPort through `launcher/onecloud/Launcher.java`; `diagnostics.py` records Wi-Fi, temperature and playback statistics to `state/diag/` every 10 s, and the full logs of each boot go to `state/logs/` (kept across power cuts; the settings page offers a diagnostics download).
- Firmware and installation: `scripts/onecloud/firmware/README.md`. `build-bundle.sh` makes the install bundle from this repository alone, `install.sh` installs it on Armbian, `prepare-image.sh` removes all personal data, and `pack-image.sh` rebuilds the OneCloud burn image.

## Running (OpenWrt)

- `supervisor_openwrt.py` and `platform_openwrt.py`: the supervisor and platform layer on OpenWrt. They only read OpenWrt's network (`ubus`), hand the chosen Wi-Fi to the iPhone over Bluetooth and follow it when it changes; without Bluetooth only wired CarPlay is offered. `board_zte_f50.py`: the ZTE F50's built-in Bluetooth bring-up with its own BlueZ. Packaging, installation and use: [openwrt-dashplay](https://github.com/yizhixiaoheigou/openwrt-dashplay).
- The bundle that `scripts/onecloud/firmware/build-bundle.sh` makes (`dashplay-bundle-<version>.tar` on the release page) is what the OpenWrt package installs.
- `scripts/carplay-night-probe.mjs`: captures the current CarPlay screen (night mode checks).

## Tests

```sh
python3 -m unittest discover -s tests/bluetooth     # needs dbus-daemon and dbus-next; all test identities are synthetic
python3 -m unittest discover -s tests/onecloud      # settings, several phones, platforms, diagnostics, firmware packing; certificate tests need OpenSSL 3
```

## Known issues

- When the player window and the CarPlay picture have a similar shape (normal view and full screen, for example), the picture is stretched to fill without reconnecting; the first time full screen is entered the box reconnects once at that size (about 8–12 s) and remembers it; very different shapes (portrait, split screen) reconnect at once.
- Way 1 is cable only; in way 3 the head unit has no internet while it is on the box hotspot; in way 2 a portable Wi-Fi with client isolation makes wireless stutter or fail, so use the cable.
- The head unit's browser warns about the certificate the first time; choose "continue". For a head unit whose address changes, a browser that blocks self-signed certificates outright, or one that refuses a LAN address, the settings page group "When the head unit cannot open the page" has the options; the steps are in section 4 of the [AI install notes (Chinese)](docs/DashPlay-AI安装说明.md).
- Calls and Siri use the head unit's microphone through the browser and play on its media channel (not the headrest speakers). The microphone reaches the browser very quiet; raised by the box, the other side hears you well but may hear its own echo. While the iPhone is also on the car's own Bluetooth, the car takes the call and the microphone too and the other side hears nothing: disconnect the iPhone from the car's own Bluetooth to call over CarPlay.
- When an app of the head unit itself (a video app, say) plays sound, the iPhone pauses and does not resume on its own: press play once on the steering wheel or the car's media card.
- A weak supply (an ordinary 5 V USB-to-DC cable) makes the OneCloud's USB adapter drop out again and again; use a 12 V supply or a 5→12 V boost cable.
- The Phicomm N1's 5 GHz box hotspot is limited to 40 MHz.
- SSH defaults to `root` / `1234` so that the user's AI assistant can add the authentication files; anyone on the same network can log in.
- The page's night mode command is wired up, but it is not yet confirmed that the iPhone switches its appearance accordingly.

## Box settings icon

![Box settings](assets/box-settings.png)

The icon is regenerated by the project's own geometric drawing script and uses no third-party icon assets. Editable source, reproduction and license: [icon provenance](assets/ICON-SOURCE.md). The receiver keeps the internal resource name `f50-settings.png` for compatibility; the product name is DashPlay.

## License and trademarks

GPL-3.0; see [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md). CarPlay, iPhone, AirPlay and MFi are trademarks of Apple Inc.; this project is not affiliated with or endorsed by Apple, PlayPort, DiPlay, xcertplay or WheelPlay.
