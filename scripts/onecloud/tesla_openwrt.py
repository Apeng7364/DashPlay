"""F50 offline Tesla checks. Own only selected DNS/DHCP fields and one nft table.

A separate LAN address routes port 80 to SettingsPage without changing uhttpd.
The original UCI values are journalled before changes, restored on exit/restart
or WAN recovery. No Wi-Fi concurrency on the F50's current driver.
"""
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import setup_wizard

TABLE = 'dashplay_tesla_checks'
# Any one answering means the upstream works. A Chinese SIM may drop 1.1.1.1:443 and 8.8.8.8:443 while the
# rest of the internet works (F50, 2026-10-15), so domestic resolvers come first.
PROBES = (('223.5.5.5', 443), ('1.12.12.12', 443), ('1.1.1.1', 443))


def offline_fields(sections, network, host, own_name):
    """UCI field map; refuse ambiguous multi-dnsmasq layouts rather than modify another LAN."""
    dns = [key for key, value in sections.items() if value.get('.type') == 'dnsmasq']
    lan = [key for key, value in sections.items()
           if value.get('.type') == 'dhcp' and value.get('interface') == network]
    if len(dns) != 1 or len(lan) != 1:
        raise ValueError('联网检查需要一个明确的 LAN DHCP 和 dnsmasq 实例')
    address = ipaddress.ip_interface(host)
    alias = str(address.network.network_address + 2)
    if alias == str(address.ip):
        raise ValueError('联网检查别名地址已被 LAN 使用')
    entries = [f'/{name}/{alias}' for name in setup_wizard.TESLA_CHECK_NAMES]
    if own_name:
        entries.append(f'/{own_name}/{address.ip}')
    # Preserve unrelated DHCP options, replacing only router and DNS. The box is the router: Teslas left a network
    # without a default route ~30 s after joining (two N1 users, 2026-10-16), as supervisor.hotspot_router.
    existing = sections[lan[0]].get('dhcp_option', [])
    existing = [existing] if isinstance(existing, str) else existing
    dhcp = [v for v in existing if v.split(',', 1)[0] not in ('3', '6')]
    fields = {f'{dns[0]}.address': entries, f'{dns[0]}.server': None,
              f'{dns[0]}.noresolv': '1', f'{dns[0]}.nohosts': '1', f'{dns[0]}.resolvfile': None,
              f'{dns[0]}.logqueries': '1', f'{dns[0]}.logfacility': None,
              f'{lan[0]}.dhcp_option': dhcp + [f'3,{address.ip}', f'6,{address.ip}']}
    return alias, fields


def redirect_rules(device, alias, port):
    if not device or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in device):
        raise ValueError('Invalid LAN device')
    ipaddress.IPv4Address(alias)
    if not 1024 <= int(port) <= 65535:
        raise ValueError('Invalid settings port')
    return (f'add table ip {TABLE}\n'
            f'add chain ip {TABLE} inbound {{ type nat hook prerouting priority -101; policy accept; }}\n'
            f'add rule ip {TABLE} inbound iifname "{device}" ip daddr {alias} tcp dport 80 redirect to :{port}\n')


def wan_available(run=subprocess.run, connect=socket.create_connection):
    """Probe from an up, non-LAN interface with a default route; bypass our offline DNS.
    TCP success proves an upstream path, not the availability of each Tesla service.
    """
    try:
        data = json.loads(run(['ubus', 'call', 'network.interface', 'dump'],
                              capture_output=True, text=True, timeout=5).stdout)
        for item in data.get('interface', []):
            if not item.get('up') or item.get('interface') in ('lan', 'loopback', 'tailscale'):
                continue
            if not any(r.get('target') == '0.0.0.0' and r.get('mask') == 0 for r in item.get('route', [])):
                continue
            for address in item.get('ipv4-address', []):
                for target in PROBES:
                    try:
                        with connect(target, timeout=2, source_address=(address['address'], 0)):
                            return True
                    except OSError:
                        pass
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return False


class OfflineChecks:
    def __init__(self, state, run=subprocess.run, platform=None):
        self.path = Path(state) / 'tesla-dhcp-before.json'
        self.run = run
        self.active = False
        self.wanted = None
        self.samples = 0
        self.platform = platform
        self.query_process = None
        self.query_error = None

    def start_query_log(self):
        if self.platform is None or self.query_process is not None and self.query_process.poll() is None:
            return
        self.platform.stop_child('tesla-dns-log')
        try:
            target = self.path.parent / 'diag/tesla-dns.log'
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # ubox logread -S takes KiB despite its usage text saying bytes.
            # Native rotation keeps one .old file; both files are created 0600.
            self.query_process = self.platform.start('tesla-dns-log',
                ['logread', '-f', '-e', 'dnsmasq', '-F', str(target), '-S', '1024'])
            self.query_error = None
        except OSError as error:
            self.query_process = None
            self.query_error = type(error).__name__

    def stop_query_log(self):
        if self.platform is not None:
            self.platform.stop_child('tesla-dns-log')
        self.query_process = None

    def command(self, argv, check=True, **kwargs):
        return self.run(argv, capture_output=True, text=True, timeout=20, check=check, **kwargs)

    def sections(self):
        return json.loads(self.command(['ubus', 'call', 'uci', 'get', '{"config":"dhcp"}']).stdout)['values']

    def write_fields(self, fields):
        for key, value in fields.items():
            self.command(['uci', '-q', 'delete', 'dhcp.' + key], check=False)
            if isinstance(value, list):
                for entry in value:
                    self.command(['uci', 'add_list', f'dhcp.{key}={entry}'])
            elif value is not None:
                self.command(['uci', 'set', f'dhcp.{key}={value}'])
        self.command(['uci', 'commit', 'dhcp'])
        self.command(['/etc/init.d/dnsmasq', 'restart'])

    def restore(self):
        self.stop_query_log()
        if not self.path.exists():
            return
        saved = json.loads(self.path.read_text())
        # Remove our network plumbing even if it was only partly installed.
        self.command(['nft', 'delete', 'table', 'ip', TABLE], check=False)
        self.command(['ip', 'addr', 'del', saved['alias'] + '/32', 'dev', saved['device']], check=False)
        self.write_fields(saved['before'])
        self.path.unlink()
        self.active = False

    def enable(self, network, own_name, port):
        sections = self.sections()
        alias, fields = offline_fields(sections, network['network'], str(network['address']), own_name)
        before = {key: sections[key.split('.')[0]].get(key.split('.')[1]) for key in fields}
        setup_wizard.save_json(self.path, {'before': before, 'alias': alias, 'device': network['device']})
        try:
            self.command(['ip', 'addr', 'add', alias + '/32', 'dev', network['device']])
            self.command(['nft', '-f', '-'], input=redirect_rules(network['device'], alias, port))
            self.start_query_log()
            self.write_fields(fields)
            self.active = True
        except Exception:
            self.restore()
            raise

    def update(self, config, network, status, online=None):
        was_active = self.active
        choice = config.get('answer_tesla_checks', 'auto')
        eligible = config.get('tesla_mode') == '1' and network['mode'] == 'ap'
        upstream = wan_available(self.run) if eligible and choice != '0' and online is None else bool(online)
        # Even explicit enable may not shadow genuine services when WAN is available.
        wanted = eligible and choice != '0' and not upstream
        self.samples = self.samples + 1 if wanted == self.wanted else 1
        self.wanted = wanted
        # Stop answering immediately when WAN returns; require two offline samples after startup.
        if self.active != wanted and (not wanted or self.samples >= 2 or 'tesla_offline' not in status):
            if wanted:
                self.enable(network, config['name'], int(config['settings_port']))
            else:
                self.restore()
            status['tesla_network_change'] = '网络已切换，请在特斯拉上断开再连接 WiFi，更新网关租约'
        status['tesla_offline'] = self.active
        if self.active:
            self.start_query_log()  # a failed logger retries without interrupting CarPlay
        status['tesla_dns_log'] = ('error: ' + self.query_error if self.query_error else
                                   'recording' if self.query_process is not None and self.query_process.poll() is None else 'stopped')
        status['tesla_internet'] = ('特斯拉：不上网（替特斯拉回答联网检查）。自带音乐、地图不能联网；要上网请插 SIM 卡。'
                                    if self.active else '使用上游网络；F50 不支持借手机热点同时供车机连接。')
        return was_active != self.active
