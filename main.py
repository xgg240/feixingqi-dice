import sys
import os
import subprocess

# v2.5.5: 子进程极早期探针 (在 import mitmproxy 之前) — 看子进程是否进得了 main.py
try:
    _boot_log = os.path.join(
        os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.getcwd(),
        'dice_child_crash.log'
    )
    with open(_boot_log, 'a', encoding='utf-8') as _bf:
        _bf.write(f'[boot] main.py loaded, frozen={getattr(sys, "frozen", False)}, argv[0]={sys.argv[0] if sys.argv else "?"}\n')
except: pass
import time
import threading
import json
from datetime import datetime

sys.stdout = open(os.devnull, 'w')
sys.stderr = open(os.devnull, 'w')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.emu_cert import auto_setup_emulator_cert, find_adb, get_connected_devices, _run_cmd
from gui.main_window import DiceToolGUI
from proxy.divert_proxy import ProxyEngine, find_headless_processes, find_emu_net_process, detect_emu_network_process, EMU_NET_PROCESS

WECHAT_PROCESS = 'WeChatAppEx.exe'
STATE_FILE = os.path.join(os.path.expanduser('~'), '.mitmproxy', 'dice_state.json')

def is_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    import ctypes
    import os
    import sys
    script = os.path.abspath(sys.argv[0])
    ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable, '"' + script + '"', None, 1)
    sys.exit(0)

def check_and_install_cert():
    cert_path = os.path.join(os.path.expanduser('~'), '.mitmproxy', 'mitmproxy-ca-cert.cer')
    if not os.path.exists(cert_path):
        from mitmproxy.certs import CertStore
        cert_dir = os.path.join(os.path.expanduser('~'), '.mitmproxy')
        os.makedirs(cert_dir, exist_ok=True)
        CertStore.from_store(cert_dir, 'mitmproxy', 2048)
    if os.path.exists(cert_path):
        creationflags = 134217728 if sys.platform == 'win32' else 0
        result = subprocess.run(['certutil', '-verifystore', 'root', 'mitmproxy'], capture_output=True, text=True, creationflags=creationflags)
        if 'mitmproxy' not in result.stdout:
            subprocess.run(['certutil', '-addstore', 'root', cert_path], capture_output=True, creationflags=creationflags)
            return '证书已自动安装'
        return '证书已就绪'
    return '证书生成失败，首次拦截时会自动生成'

def check_emulator_root(adb_path, device):
    rc, out, _ = _run_cmd(adb_path, '-s', device, 'shell', 'id')
    if rc == 0 and 'uid=0' in out:
        return True
    rc, out, _ = _run_cmd(adb_path, '-s', device, 'shell', 'su', '-c', 'id')
    if rc == 0 and 'uid=0' in out:
        return True
    return False

def write_state(target_dice, mode):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump({'target_dice': target_dice, 'mode': mode}, f)

def run_startup_diagnostics():
    results = []
    results.append(('管理员权限', True, '已获取'))
    windivert_path = None
    if hasattr(sys, '_MEIPASS'):
        for root, dirs, files in os.walk(sys._MEIPASS):
            for f in files:
                if f.lower() == 'windivert.dll':
                    windivert_path = os.path.join(root, f)
                    break
            if windivert_path:
                break
    if windivert_path:
        results.append(('WinDivert驱动', True, '找到: ' + os.path.basename(windivert_path)))
    else:
        results.append(('WinDivert驱动', True, '开发环境(由mitmproxy管理)'))
    headless = find_headless_processes()
    if headless:
        results.append(('模拟器进程', True, '检测到: ' + ', '.join(headless)))
    else:
        results.append(('模拟器进程', None, '未检测到(选QK模式时需要)'))
    detected = detect_emu_network_process()
    if detected:
        results.append(('模拟器网络', True, '自动探测: ' + detected))
    else:
        emu_net = find_emu_net_process()
        if emu_net:
            results.append(('模拟器网络', None, '无外部连接，兜底: ' + emu_net))
        else:
            results.append(('模拟器网络', None, '未运行(选QK模式时需要)'))
    try:
        creationflags = 134217728 if sys.platform == 'win32' else 0
        r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + WECHAT_PROCESS, '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=10, creationflags=creationflags)
        if WECHAT_PROCESS.lower() in r.stdout.lower():
            results.append(('微信进程', True, WECHAT_PROCESS + ' 运行中'))
        else:
            results.append(('微信进程', None, WECHAT_PROCESS + ' 未运行(选KRDJ模式时需要)'))
    except:
        results.append(('微信进程', None, '检测失败'))
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', 8899))
        sock.close()
        if result == 0:
            results.append(('端口8899', False, '已被占用！可能有残留进程'))
        else:
            results.append(('端口8899', True, '可用'))
    except:
        results.append(('端口8899', True, '可用(检测跳过)'))
    cert_path = os.path.join(os.path.expanduser('~'), '.mitmproxy', 'mitmproxy-ca-cert.pem')
    if os.path.exists(cert_path):
        results.append(('mitmproxy证书', True, '已生成'))
    else:
        results.append(('mitmproxy证书', False, '未生成(首次启动会自动创建)'))
    if hasattr(sys, '_MEIPASS'):
        addon_path = os.path.join(sys._MEIPASS, 'proxy', 'addon_script.py')
    else:
        addon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proxy', 'addon_script.py')
    if os.path.exists(addon_path):
        results.append(('addon脚本', True, '存在'))
    else:
        results.append(('addon脚本', False, '缺失: ' + addon_path))
    return results

class DiceToolApp:
    def __init__(self):
        self.gui = DiceToolGUI(on_dice_select=self._on_dice_select, on_mode_change=self._on_mode_change)
        self._engine = None
        self._current_mode = None
        self._target_dice = None

    def _on_dice_select(self, dice):
        if dice is None:
            self._target_dice = None
            write_state(None, self._current_mode if self._current_mode is not None else 'K')
            self.gui.log('[' + self._ts() + '] 已取消选择')
            return
        if self._engine and self._engine.is_running():
            self._target_dice = dice
            write_state(dice, self._current_mode if self._current_mode is not None else 'K')
            self.gui.log('[' + self._ts() + '] 下次掷骰: ' + str(dice) + '点')
        else:
            self.gui.log('[' + self._ts() + '] ⚠ 请先选择模式启动引擎')
            self.gui.clear_selection()

    def _on_mode_change(self, mode):
        old_mode = self._current_mode
        self._current_mode = mode
        write_state(self._target_dice, mode)
        need_restart = False
        if old_mode is None:
            need_restart = True
        elif (old_mode == 'Q') != (mode == 'Q'):
            need_restart = True
        if need_restart:
            self.gui.log('[' + self._ts() + '] 模式: ' + mode)
            threading.Thread(target=self._start_for_mode, args=(mode,), daemon=True).start()
        else:
            self.gui.log('[' + self._ts() + '] 模式: ' + mode)

    def _start_for_mode(self, mode):
        if self._engine:
            self.gui.log('[' + self._ts() + '] 释放旧引擎...')
            self._engine.stop()
            self._engine = None
            self.gui.log('[' + self._ts() + '] ✓ 已释放')
        if mode == 'Q':
            target = detect_emu_network_process()
            if not target:
                headless_procs = find_headless_processes()
                emu_net = find_emu_net_process()
                if not headless_procs and not emu_net:
                    self.gui.set_status('未检测到模拟器', '#ff9800')
                    self.gui.log('[' + self._ts() + '] ✗ 未找到模拟器进程')
                    return
                target = emu_net if emu_net else (headless_procs[0] if headless_procs else None)
                if not target:
                    self.gui.set_status('探测失败', '#ff9800')
                    self.gui.log('[' + self._ts() + '] ✗ 无法确定网络进程')
                    return
                self.gui.log('[' + self._ts() + '] ⚠ 未检测到外部连接，兜底使用: ' + target)
            else:
                self.gui.log('[' + self._ts() + '] 网络进程(自动探测): ' + target)
            self._check_emulator_status()
        else:
            target = WECHAT_PROCESS
        self.gui.log('[' + self._ts() + '] 启动引擎: ' + target)
        try:
            self._engine = ProxyEngine(target_process=target, proxy_port=8899)
            self._engine.set_ws_callback(self._on_addon_message)
            if self._engine.start():
                label = '模拟器' if mode == 'Q' else '微信'
                self.gui.set_status('运行中 (' + label + ')', '#4CAF50')
                self.gui.log('[' + self._ts() + '] ✓ 就绪')
            else:
                err = self._engine._error if self._engine._error else '引擎启动超时'
                self.gui.set_status('启动失败', '#f44336')
                self.gui.log('[' + self._ts() + '] ✗ ' + err)
        except Exception as e:
            self.gui.set_status('错误', '#f44336')
            self.gui.log('[' + self._ts() + '] ✗ ' + str(e))

    def _on_addon_message(self, direction, data):
        try:
            msg = json.loads(data)
            msg_type = msg.get('type')
            if msg_type == 'dice':
                original = msg.get('original')
                final = msg.get('final')
                modified = msg.get('modified')
                room = msg.get('room', '')
                had_target = msg.get('had_target', False)
                six_locked = msg.get('six_locked', False)
                self.gui.set_six_locked(six_locked)
                if had_target:
                    if modified:
                        self.gui.log('[' + self._ts() + '] 🎲 ' + str(original) + '→' + str(final) + ' 房间:' + room)
                    else:
                        self.gui.log('[' + self._ts() + '] 🎲 已设' + str(final) + '点 房间:' + room)
                    self.gui.clear_selection()
                    self._target_dice = None
                    write_state(None, self._current_mode if self._current_mode is not None else 'K')
                else:
                    if modified:
                        self.gui.log('[' + self._ts() + '] 🎲 防封:' + str(original) + '→' + str(final))
                    else:
                        self.gui.log('[' + self._ts() + '] 🎲 透传:' + str(original) + '点')
            elif msg_type == 'diag_flow':
                count = msg.get('count', 0)
                host = msg.get('host', '')
                self.gui.log('[' + self._ts() + '] 📡 流量#' + str(count) + ': ' + host)
            elif msg_type == 'diag_ws':
                count = msg.get('count', 0)
                host = msg.get('host', '')
                self.gui.log('[' + self._ts() + '] 🔌 WS连接#' + str(count) + ': ' + host)
            elif msg_type == 'diag_error':
                err = msg.get('msg', '')
                self.gui.log('[' + self._ts() + '] ⚠ 引擎: ' + err)
        except:
            pass

    def _check_emulator_status(self):
        adb = find_adb()
        if not adb:
            self.gui.log('[' + self._ts() + '] ⚠ 未找到ADB')
            return
        devices = get_connected_devices(adb)
        if not devices:
            for port in (5555, 5556, 5557, 62001, 62025, 21503):
                _run_cmd(adb, 'connect', '127.0.0.1:' + str(port))
            devices = get_connected_devices(adb)
        if not devices:
            self.gui.log('[' + self._ts() + '] ⚠ ADB未连接模拟器')
            return
        device = devices[0]
        has_root = check_emulator_root(adb, device)
        if has_root:
            self.gui.log('[' + self._ts() + '] ✓ root正常')
        else:
            self.gui.log('[' + self._ts() + '] ✗ 模拟器无root，请开启')
            return
        self.gui.log('[' + self._ts() + '] 🔐 检查证书...')
        success, cert_msg = auto_setup_emulator_cert()
        self.gui.log('[' + self._ts() + '] ' + ('✓' if success else '⚠') + ' ' + cert_msg)

    def _ts(self):
        return datetime.now().strftime('%H:%M:%S')

    def run(self):
        self.gui.log('飞行棋骰子工具 v2.4')
        self.gui.log('选择模式后自动启动，点数用完自动复位')
        self.gui.log('')
        def _init():
            self.gui.log('[' + self._ts() + '] ─── 环境诊断 ───')
            try:
                diag_results = run_startup_diagnostics()
                for name, ok, detail in diag_results:
                    if ok is True:
                        icon = '✓'
                    elif ok is False:
                        icon = '✗'
                    else:
                        icon = '○'
                    self.gui.log('  ' + icon + ' ' + name + ': ' + detail)
            except Exception as e:
                self.gui.log('  ✗ 诊断异常: ' + str(e))
            self.gui.log('[' + self._ts() + '] ─── 证书检查 ───')
            cert_status = check_and_install_cert()
            self.gui.log('  ' + cert_status)
            self.gui.log('')
            self.gui.set_status('请选择模式', '#ff9800')
            self.gui.log('[' + self._ts() + '] 请点击上方模式按钮启动')
            self.gui.log('[' + self._ts() + '] 💡 启动后如无📡流量提示=WinDivert未捕获到流量')
        self.gui.root.after(300, lambda: threading.Thread(target=_init, daemon=True).start())
        self.gui.run()
        if self._engine:
            self._engine.stop()

def main():
    if '--mitmdump' in sys.argv:
        sys.argv.remove('--mitmdump')
        # v2.5.5: 子进程任何错误都写到 dice_child_crash.log, 不靠 stdout
        _crash_log = os.path.join(
            os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.getcwd(),
            'dice_child_crash.log'
        )
        # 第一次写 (证明子进程至少跑到这里)
        try:
            with open(_crash_log, 'a', encoding='utf-8') as _f:
                _f.write(f'\n=== {time.strftime("%Y-%m-%d %H:%M:%S")} 子进程启动 ===\n')
                _f.write(f'  python: {sys.version}\n')
                _f.write(f'  frozen: {getattr(sys, "frozen", False)}\n')
                _f.write(f'  _MEIPASS: {getattr(sys, "_MEIPASS", None)}\n')
                _f.write(f'  argv: {sys.argv}\n')
        except: pass
        try:
            # v2.5.7: 一层一层 import 诊断, 看哪一步崩
            try:
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write('[diag] import sys/os OK\n')
            except: pass

            # step 1: import mitmproxy (顶层)
            try:
                import mitmproxy
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[step 1] import mitmproxy OK, version={getattr(mitmproxy, "__version__", "?")}\n')
            except BaseException as _e:
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[step 1] FAIL mitmproxy: {type(_e).__name__}: {_e}\n')
                    import traceback as _tb1; _f.write(_tb1.format_exc())
                sys.exit(1)

            # v2.6.0: mitmproxy 10.x 用 pydivert (Python 层 windivert 绑定)
            try:
                import pydivert
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[step 1.5] import pydivert OK, version={pydivert.__version__ if hasattr(pydivert, "__version__") else "?"}\n')
            except BaseException as _e:
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[step 1.5] FAIL pydivert: {type(_e).__name__}: {_e}\n')

            # step 2: import mitmproxy.tools.main
            try:
                from mitmproxy.tools.main import mitmdump
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[step 2] import mitmproxy.tools.main OK, mitmdump={mitmdump!r}\n')
            except BaseException as _e:
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[step 2] FAIL mitmproxy.tools.main: {type(_e).__name__}: {_e}\n')
                    import traceback as _tb2; _f.write(_tb2.format_exc())
                sys.exit(1)

            # step 3: 调 mitmdump()
            with open(_crash_log, 'a', encoding='utf-8') as _f:
                _f.write('[step 3] 调用 mitmdump() ...\n')
                _f.flush()

            # v2.5.9: 开后台线程, 每 1s 报个心跳, 看 mitmdump 到底死在哪一秒
            import threading as _th
            _alive_flag = [True]
            def _heartbeat():
                for i in range(120):  # 最多报 120s 心跳
                    if not _alive_flag[0]:
                        return
                    time.sleep(1)
                    try:
                        with open(_crash_log, 'a', encoding='utf-8') as _f:
                            _f.write(f'[hb] +{i+1}s 还在 mitmdump() 里 ...\n')
                    except: pass
            _hb_thread = _th.Thread(target=_heartbeat, daemon=True)
            _hb_thread.start()

            # v2.6.8: 把 mitmdump 的 stdout/stderr 转发到 crash log
            # (mitmdump 内部 print + traceback 全部接住, 不然不知道为啥 SystemExit(1))
            _log_fh = open(_crash_log, 'a', encoding='utf-8')
            with open(_crash_log, 'a', encoding='utf-8') as _f:
                _f.write(f'[step 3.0] redirecting stdout/stderr to crash log (orig={type(sys.stdout).__name__}/{type(sys.stderr).__name__})\n')
            _orig_stdout, _orig_stderr = sys.stdout, sys.stderr
            sys.stdout = _log_fh
            sys.stderr = _log_fh
            try:
                try:
                    mitmdump()
                finally:
                    _alive_flag[0] = False
                    sys.stdout = _orig_stdout
                    sys.stderr = _orig_stderr
                    _log_fh.flush()
                    _log_fh.close()
            except BaseException as _mderr:
                sys.stdout = _orig_stdout
                sys.stderr = _orig_stderr
                _log_fh.flush()
                _log_fh.close()
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[step 3 mitmdump raised] {type(_mderr).__name__}: {_mderr}\n')
                    import traceback as _tbmd; _f.write(_tbmd.format_exc())
                raise

            with open(_crash_log, 'a', encoding='utf-8') as _f:
                _f.write('[step 3] mitmdump() 返回 (正常退出)\n')
        except SystemExit as e:
            try:
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[exit] SystemExit code={e.code}\n')
            except: pass
        except BaseException as e:
            import traceback as _tb
            try:
                with open(_crash_log, 'a', encoding='utf-8') as _f:
                    _f.write(f'[crash] {type(e).__name__}: {e}\n')
                    _f.write(_tb.format_exc())
            except: pass
            try:
                sys.stderr.write(f'[crash] {type(e).__name__}: {e}\n')
                _tb.print_exc(file=sys.stderr)
                sys.stderr.flush()
            except: pass
            sys.exit(1)
        return
    if sys.platform == 'win32' and not is_admin():
        run_as_admin()
    app = DiceToolApp()
    app.run()

if __name__ == '__main__':
    main()