"""
代理引擎 - 子进程模式

用subprocess跑mitmdump，切换进程时直接kill子进程，
OS强制回收WinDivert内核句柄，彻底解决"Cannot spawn more than one local redirector"。

通信机制：
- 主进程 → addon：通过共享JSON文件传递 target_dice / mode
- addon → 主进程：通过stdout打印JSON行（日志+修改结果）
"""

import subprocess
import os
import sys
import json
import time
import threading
import signal
import re
from typing import Optional, Callable, List

WECHAT_PROCESS = 'WeChatAppEx.exe'
EMU_NET_PROCESS = 'VBoxNetNAT.exe'

def find_headless_processes() -> List[str]:
    """扫描当前运行的进程，找到匹配 *headless*.exe 的进程名（用于诊断显示）"""
    try:
        result = subprocess.run(
            ['tasklist', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=10,
            creationflags=134217728 if sys.platform == 'win32' else 0
        )
        if result.returncode != 0:
            return []

        found = set()
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            if len(parts) >= 1:
                proc_name = parts[0].strip('"')
                if re.search('headless', proc_name, re.IGNORECASE) and proc_name.lower().endswith('.exe'):
                    found.add(proc_name)
        return list(found)
    except Exception:
        return []

def find_emu_net_process() -> Optional[str]:
    """查找模拟器实际网络进程（VBoxNetNAT.exe）"""
    try:
        result = subprocess.run(
            ['tasklist', '/FI', f'IMAGENAME eq {EMU_NET_PROCESS}', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=10,
            creationflags=134217728 if sys.platform == 'win32' else 0
        )
        if result.returncode == 0 and EMU_NET_PROCESS.lower() in result.stdout.lower():
            return EMU_NET_PROCESS
    except Exception:
        pass
    return None

def detect_emu_network_process() -> Optional[str]:
    """
自动探测模拟器实际走网络的进程。

策略：用netstat找候选进程(headless + VBoxNetNAT)的PID，
看哪个持有外部(非127.0.0.1)ESTABLISHED连接，那个就是真正的网络进程。
"""
    candidates = {}
    try:
        result = subprocess.run(
            ['tasklist', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=10,
            creationflags=134217728 if sys.platform == 'win32' else 0
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            if len(parts) >= 2:
                proc_name = parts[0].strip('"')
                try:
                    pid = int(parts[1].strip('"'))
                except ValueError:
                    continue

                is_headless = re.search('headless', proc_name, re.IGNORECASE) and proc_name.lower().endswith('.exe')
                is_vboxnat = proc_name.lower() == EMU_NET_PROCESS.lower()
                if not is_headless and not is_vboxnat:
                    continue
                if proc_name not in candidates:
                    candidates[proc_name] = []
                candidates[proc_name].append(pid)
    except Exception:
        return None

    if not candidates:
        return None

    all_pids = set()
    for pids in candidates.values():
        all_pids.update(pids)

    try:
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True, text=True, timeout=15,
            creationflags=134217728 if sys.platform == 'win32' else 0
        )
        if result.returncode != 0:
            return None

        pid_ext_conns = {}
        for line in result.stdout.split('\n'):
            line = line.strip()
            if 'ESTABLISHED' not in line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue

            try:
                pid = int(parts[-1])
            except ValueError:
                continue

            if pid not in all_pids:
                continue

            remote = parts[2]
            if remote.startswith('127.0.0.1:') or remote.startswith('[::1]:'):
                continue
            pid_ext_conns[pid] = pid_ext_conns.get(pid, 0) + 1

        best_proc = None
        best_count = 0
        for proc_name, pids in candidates.items():
            total = sum(pid_ext_conns.get(pid, 0) for pid in pids)
            if total > best_count:
                best_count = total
                best_proc = proc_name

        return best_proc
    except Exception:
        return None

class ProxyEngine:
    """
代理引擎 - 子进程模式

用subprocess启动mitmdump，切换时kill进程重启。
OS杀进程时强制释放WinDivert句柄，不存在泄漏问题。
"""
    def __init__(self, target_process: str = 'WeChatAppEx.exe', proxy_port: int = 8899):
        self._target_process = target_process
        self._proxy_port = proxy_port
        self._process = None
        self._reader_thread = None
        self._on_ws_message = None
        self._running = False
        self._error = None

        self._state_file = os.path.join(os.path.expanduser('~'), '.mitmproxy', 'dice_state.json')

        if hasattr(sys, '_MEIPASS'):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._addon_script = os.path.join(base, 'proxy', 'addon_script.py')

    @property
    def target_process(self) -> str:
        return self._target_process

    def set_ws_callback(self, callback: Callable):
        """设置回调：callback(direction, data) -> Optional[str]"""
        self._on_ws_message = callback

    def write_state(self, target_dice: Optional[int] = None, mode: str = 'K'):
        """写入共享状态文件，addon每次处理消息时读取"""
        state = {
            'target_dice': target_dice,
            'mode': mode
        }
        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
        try:
            with open(self._state_file, 'w') as f:
                json.dump(state, f)
        except:
            pass

    def start(self) -> bool:
        """启动mitmdump子进程"""
        if not os.path.exists(self._addon_script):
            self._error = f'addon脚本不存在: {self._addon_script}'
            return False

        self.write_state(target_dice=None, mode='K')

        # v2.5.0: onedir 模式下, 子进程走 sys.executable (同一个 DiceTool.exe)
        # onedir + PyInstaller 6.21 子进程 import 链:  bootloader 会从 _MEIPASS=DiceTool/_internal/ 加载 modules
        if hasattr(sys, '_MEIPASS'):
            cmd_prefix = [sys.executable, '--mitmdump']
        else:
            launcher = 'from mitmproxy.tools.main import mitmdump; mitmdump()'
            cmd_prefix = [sys.executable, '-c', launcher]

        cmd = cmd_prefix + [
            '--listen-host', '127.0.0.1',
            '--listen-port', str(self._proxy_port),
            '--mode', f'local:{self._target_process}',
            '--ssl-insecure',
            '--set', 'flow_detail=0',
            '-s', self._addon_script
        ]

        if self._target_process == 'WeChatAppEx.exe':
            cmd.insert(-2, '--set')
            cmd.insert(-2, 'allow_hosts=dbankcloud\\.cn|wxagame|weixin\\.qq\\.com')

        print(f'[ProxyEngine] 启动: {sys.executable} -c ... local:{self._target_process}')

        try:
            env = os.environ.copy()
            env.pop('VIRTUAL_ENV', None)
            env.pop('PYTHONHOME', None)
            env.pop('PYTHONPATH', None)

            if hasattr(sys, '_MEIPASS'):
                env['_MEIPASS2'] = sys._MEIPASS

            creation_flags = 0
            if sys.platform == 'win32':
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | 134217728

            # v2.5.4: 子进程 stdout 重定向到 dice_child.log 文件
            # mitmproxy 11.x print() 不用 flush=True, PIPE 会被 C 层 buffer
            # 用文件 + 隔 1s 读就能拿到启动失败信息
            debug_log = os.path.join(os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd(), "dice_debug.log")
            child_log = os.path.join(os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd(), "dice_child.log")
            # 删旧 child log
            try: os.remove(child_log)
            except: pass

            with open(debug_log, 'a', encoding='utf-8') as f:
                f.write(f'\n=== {time.strftime("%Y-%m-%d %H:%M:%S")} 启动引擎 ===\n')
                f.write(f'  cmd: {cmd}\n')
                f.write(f'  _MEIPASS: {getattr(sys, "_MEIPASS", None)}\n')
                f.write(f'  sys.executable: {sys.executable}\n')
                f.write(f'  sys._MEIPASS2: {env.get("_MEIPASS2", None)}\n')

            # 开文件接受子进程 stdout (不用 PIPE, 避免 C 层 buffer)
            self._child_log_file = open(child_log, 'w', encoding='utf-8', buffering=1)  # line buffered

            self._process = subprocess.Popen(
                cmd,
                stdout=self._child_log_file,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                env=env
            )

            self._child_log_path = child_log
            self._child_log_file_handle = self._child_log_file

            with open(debug_log, 'a', encoding='utf-8') as f:
                f.write(f'  pid: {self._process.pid}\n')
                f.write(f'  child_log: {child_log}\n')
        except Exception as e:
            self._error = f'启动mitmdump失败: {e}'
            try:
                with open(debug_log, 'a', encoding='utf-8') as f:
                    f.write(f'  EXCEPTION: {repr(e)}\n')
            except: pass
            return False

        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

        for _ in range(80):
            time.sleep(0.1)
            if self._running or self._error:
                return self._running
            if self._process.poll() is not None:
                self._error = 'mitmdump进程异常退出'
                return self._running

        return self._running

    def _read_output(self):
        """读取子进程stdout，解析addon输出的JSON行"""
        debug_log = os.path.join(os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd(), "dice_debug.log")
        child_log = getattr(self, '_child_log_path', None)
        try:
            if not child_log:
                # 退到 PIPE 模式 (安全路径)
                for line in self._process.stdout:
                    line = line.strip()
                    if line:
                        self._parse_child_line(line)
                return

            # v2.5.4: 从文件读子进程输出 (不是 PIPE, 避免 C 层 buffer)
            last_size = 0
            while True:
                try:
                    if not os.path.exists(child_log):
                        time.sleep(0.1)
                        continue
                    cur_size = os.path.getsize(child_log)
                    if cur_size > last_size:
                        with open(child_log, 'r', encoding='utf-8', errors='ignore') as f:
                            f.seek(last_size)
                            new_text = f.read()
                        last_size = cur_size
                        for line in new_text.splitlines():
                            line = line.strip()
                            if line:
                                self._parse_child_line(line)
                    else:
                        # 检查子进程是否还活着
                        if self._process.poll() is not None and cur_size == last_size:
                            # 进程死了, 读剩余
                            with open(child_log, 'r', encoding='utf-8', errors='ignore') as f:
                                f.seek(last_size)
                                new_text = f.read()
                            for line in new_text.splitlines():
                                line = line.strip()
                                if line:
                                    self._parse_child_line(line)
                            break
                    time.sleep(0.1)
                except Exception:
                    time.sleep(0.1)
        except Exception:
            pass
        finally:
            self._running = False

    def _parse_child_line(self, line: str):
        """解析子进程一行输出"""
        debug_log = os.path.join(os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd(), "dice_debug.log")
        try:
            with open(debug_log, 'a', encoding='utf-8') as f:
                f.write(f'  [child] {line}\n')
        except: pass

        if line.startswith('{"type":'):
            try:
                msg = json.loads(line)
                self._handle_addon_message(msg)
            except json.JSONDecodeError:
                return
        elif 'Proxy server listening' in line or 'Local redirector' in line:
            self._running = True
            print(f'[ProxyEngine] 就绪: {line}')
        elif 'Error' in line or 'error' in line or 'Cannot' in line:
            print(f'[ProxyEngine] {line}')
            if not self._running:
                self._error = line

            if self._on_ws_message:
                err_msg = json.dumps({'type': 'diag_error', 'msg': line}, ensure_ascii=False)
                self._on_ws_message('diag', err_msg)
        elif 'WinDivert' in line or 'driver' in line.lower():
            print(f'[ProxyEngine] {line}')
            if self._on_ws_message:
                err_msg = json.dumps({'type': 'diag_error', 'msg': line}, ensure_ascii=False)
                self._on_ws_message('diag', err_msg)
        elif 'Cannot spawn' in line:
            self._error = line

    def _handle_addon_message(self, msg: dict):
        """处理addon发来的消息"""
        msg_type = msg.get('type')

        if msg_type == 'ready':
            self._running = True
            return

        if msg_type == 'dice':

            if self._on_ws_message:
                self._on_ws_message('c2s', json.dumps(msg, ensure_ascii=False))
            return

        if msg_type in ('diag_flow', 'diag_ws'):

            if self._on_ws_message:
                self._on_ws_message('diag', json.dumps(msg, ensure_ascii=False))
            return

    def stop(self):
        """停止引擎：直接kill子进程，OS回收WinDivert句柄"""
        if self._process and self._process.poll() is None:
            try:
                if sys.platform == 'win32':

                    subprocess.run(
                        ['taskkill', '/F', '/T', '/PID', str(self._process.pid)],
                        capture_output=True, timeout=5,
                        creationflags=134217728 if sys.platform == 'win32' else 0
                    )
                else:
                    self._process.kill()
            except Exception:
                try:
                    self._process.kill()
                except:
                    pass

            try:
                self._process.wait(timeout=5)
            except:
                pass

        # v2.5.4: 关 child log file handle
        if hasattr(self, '_child_log_file_handle'):
            try: self._child_log_file_handle.close()
            except: pass
            self._child_log_file_handle = None

        self._process = None
        self._running = False

        time.sleep(1)

    @property
    def is_running(self) -> bool:
        return self._running
