"""
v2.7.0 local redirector — mitmproxy 8.0.0 不支持 --mode local:xxx
(mitmproxy_rs 的 LocalRedirector 是 10.x/11.x 才合并的),
pydivert 在 Python 层自己写.

原理:
  WeChatAppEx.exe 发起 TCP 连接 (orig_dst, orig_port)
  -> WinDivert filter 抓到 SYN
  -> 改 dst → 127.0.0.1:8899 (mitmdump listen)
  -> 改 src → 127.0.0.1 (模拟本地连, 不然 mitmdump 看到原 src)
  -> 转发
  -> mitmdump regular 模式 listen 8899, 看到 Host/SNI (packet payload 没改过)
  -> mitmdump 拿 pretty_host 连回原目标
  -> 响应路径: WinDivert 看到 src=127.0.0.1:8899 反向改回 (orig_dst, orig_port)
  -> 回到 WeChatAppEx.exe

mitmdump 看 flow.client_address 是 127.0.0.1 (loss), 但 flow.server_address
仍然是原目标 (从 SNI/Host 头拿的), addon_script.py 不需要 client_address 所以无感.

filter: tcp and proc.name == WeChatAppEx.exe and tcp.DstPort != 8899
  (空格 + 双等号是 pydivert 3.0+ / WinDivert 2.2+ filter 语法;
   2.1.0 / WinDivert 1.3 不支持 proc.name + 不支持 '=='/ 空格, 报 WinError 87)

v2.7.0 依赖:
  - pydivert >= 3.1.3 (bundled WinDivert 2.2.2, 支持 proc.name filter)
  - Python 3.10+ (PyInstaller 6.21 / GitHub Actions runner 用 3.12 没问题)
  - Admin 权限 (WinDivert 驱动要 admin token, DiceTool.exe 已经 runas)
"""
import os
import sys
import time
import threading
from typing import Optional


def _run_redirector(target_process: str, proxy_port: int,
                    log_path: str, stop_event: threading.Event) -> None:
    """跑 pydivert 重定向 loop, 在独立 thread 里. 异常写 log, 自然退出 on stop_event."""
    import pydivert

    # filter: 语法跟 Wireshark display filter 类似, 必须 '==' 不是 '=',
    # 必须有空格 (WinError 87 = 参数错误是 filter 解析失败)
    target_lower = target_process.lower()
    # 排除 mitmdump listen port 双向 (回环 + mitmdump 反代回去的 conn)
    # proc.name 是 WinDivert 2.0+ 扩展, 大小写不敏感
    flt = (
        f"tcp and "
        f"proc.name == {target_lower} and "
        f"tcp.DstPort != {proxy_port} and "
        f"tcp.SrcPort != {proxy_port}"
    )

    def _log(msg: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    _log(f"starting redirector: target={target_process} proxy_port={proxy_port}")
    _log(f"  filter: {flt}")

    try:
        with pydivert.WinDivert(flt) as w:
            _log("WinDivert handle opened OK")
            while not stop_event.is_set():
                try:
                    # recv timeout 500ms 让 loop 能 check stop_event
                    packet = w.recv(timeout=500)
                except TypeError:
                    # 老版 pydivert 不支持 timeout
                    packet = w.recv()
                except Exception as e:
                    _log(f"recv error: {type(e).__name__}: {e}")
                    continue
                if packet is None:
                    continue
                try:
                    # 只改 outbound (SYN/PSH/ACK from WeChatAppEx)
                    # inbound (from mitmdump) 不会 match filter (proc.name 不同)
                    # 但保险起见检查: 如果 dst 已经是 127.0.0.1 就不动
                    if packet.dst_addr == "127.0.0.1":
                        w.send(packet)
                        continue
                    # 改 IP 层 dest
                    orig_dst = packet.dst_addr
                    orig_dport = packet.dst_port
                    packet.dst_addr = "127.0.0.1"
                    packet.dst_port = proxy_port
                    # src 改 127.0.0.1 (从 mitmdump 视角是 localhost 连, 不改就拿到原 src IP)
                    # 但保留 src_port (系统看到 src_port 仍是 ephemeral, 不冲突)
                    packet.src_addr = "127.0.0.1"
                    # recompute checksums (pydivert auto-recalc on send, 但显式更稳)
                    try:
                        packet.recalculate_checksums()
                    except AttributeError:
                        pass  # 老 API 自动算
                    w.send(packet)
                except Exception as e:
                    _log(f"packet rewrite/send error: {type(e).__name__}: {e}")
                    # 出错就 drop 别发, 免得污染 stream
                    continue
            _log("stop_event set, exiting redirector loop")
    except Exception as e:
        _log(f"FATAL redirector crashed: {type(e).__name__}: {e}")
        import traceback
        _log(traceback.format_exc())
        raise


class LocalRedirector:
    """包装后台 thread, 配 ProxyEngine 用."""

    def __init__(self, target_process: str, proxy_port: int,
                 log_dir: Optional[str] = None):
        self.target_process = target_process
        self.proxy_port = proxy_port
        if log_dir is None:
            log_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.getcwd()
        self.log_path = os.path.join(log_dir, "local_redirector.log")
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[str] = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=_safe_run,
            args=(self.target_process, self.proxy_port,
                  self.log_path, self._stop_event, self),
            daemon=True,
            name=f"LocalRedirector[{self.target_process}]",
        )
        self._thread.start()
        # 给 WinDivert 一点时间开 handle, 失败了 thread 会写 log 退出
        time.sleep(0.5)
        return self._error is None

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._thread = None


def _safe_run(target: str, port: int, log: str,
              stop: threading.Event, parent: "LocalRedirector") -> None:
    try:
        _run_redirector(target, port, log, stop)
    except Exception as e:
        parent._error = f"{type(e).__name__}: {e}"
