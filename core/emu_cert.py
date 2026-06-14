"""
模拟器证书自动安装模块

功能：
- 检测ADB是否可用
- 检测模拟器是否连接
- 检查mitmproxy CA证书是否已安装到Android系统证书目录
- 自动推送并安装证书（需要root权限，模拟器一般都有）

Android系统证书目录: /system/etc/security/cacerts/
证书文件名格式: <hash>.0 (OpenSSL subject_hash_old)

注意：不依赖openssl命令，使用Python cryptography库（mitmproxy自带）
"""

import subprocess
import os
import tempfile
from typing import Tuple, Optional

def _run_cmd(cmd: list, timeout: int = 15) -> Tuple[int, str, str]:
    """执行命令，返回 (returncode, stdout, stderr)"""
    try:
        kwargs = {}
        import sys
        if sys.platform == "win32":
            kwargs["creationflags"] = 134217728
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)
        return (r.returncode, r.stdout, r.stderr)
    except FileNotFoundError:
        return (-1, "", "命令不存在")
    except subprocess.TimeoutExpired:
        return (-2, "", "超时")
    except Exception as e:
        return (-3, "", str(e))

def find_adb() -> Optional[str]:
    """查找ADB路径，优先检查常见模拟器自带的ADB"""
    candidates = [
        "C:\\leidian\\LDPlayer9\\adb.exe",
        "C:\\LDPlayer\\LDPlayer9\\adb.exe",
        "D:\\leidian\\LDPlayer9\\adb.exe",
        "D:\\LDPlayer\\LDPlayer9\\adb.exe",
        "C:\\Program Files\\MuMu\\emulator\\nemu9\\EmulatorShell\\adb.exe",
        "D:\\Program Files\\MuMu\\emulator\\nemu9\\EmulatorShell\\adb.exe",
        "C:\\Program Files\\Nox\\bin\\adb.exe",
        "D:\\Program Files\\Nox\\bin\\adb.exe",
        os.path.expandvars("%LOCALAPPDATA%\\Android\\Sdk\\platform-tools\\adb.exe"),
    ]

    rc, out, _ = _run_cmd(["adb", "version"])
    if rc == 0:
        return "adb"

    for path in candidates:
        if os.path.exists(path):
            return path

    return None

def get_connected_devices(adb_path: str) -> list:
    """获取已连接的ADB设备列表"""
    rc, out, _ = _run_cmd([adb_path, "devices"])
    if rc != 0:
        return []

    devices = []
    for line in out.strip().split("\n")[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        if state.strip() == "device":
            devices.append(serial)
    return devices

def get_cert_hash_python(cert_path: str) -> Optional[str]:
    """
用Python cryptography库计算证书的subject_hash_old
等价于 openssl x509 -subject_hash_old
不依赖外部openssl命令
"""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding
        import hashlib
        import struct

        with open(cert_path, "rb") as f:
            cert_data = f.read()

        if b"-----BEGIN CERTIFICATE-----" in cert_data:
            cert = x509.load_pem_x509_certificate(cert_data)
        else:
            cert = x509.load_der_x509_certificate(cert_data)

        subject_der = cert.subject.public_bytes()
        md5_hash = hashlib.md5(subject_der).digest()

        hash_val = struct.unpack("<I", md5_hash[:4])[0]
        return format(hash_val, "08x")
    except ImportError:
        pass
    except Exception as e:
        print(f"[emu_cert] hash计算异常: {e}")

    rc, out, _ = _run_cmd(["openssl", "x509", "-inform", "PEM", "-subject_hash_old", "-noout", "-in", cert_path])
    if rc == 0 and out.strip():
        return out.strip()

    return None

def check_cert_installed(adb_path: str, cert_hash: str, device: str = None) -> bool:
    """检查证书是否已安装到模拟器系统证书目录"""
    cmd = [adb_path]
    if device:
        cmd.extend(["-s", device])
    cmd.extend(["shell", "ls", f"/system/etc/security/cacerts/{cert_hash}.0"])

    rc, out, err = _run_cmd(cmd)
    return rc == 0 and "No such file" not in out and "No such file" not in err

def install_cert_to_emulator(adb_path: str, cert_pem_path: str, device: str = None) -> Tuple[bool, str]:
    """
将mitmproxy CA证书安装到模拟器系统证书目录

使用tmpfs overlay方案（兼容Android 10+ system-as-root只读分区）：
1. 计算证书hash
2. push证书到/data/local/tmp
3. 复制现有系统证书到临时目录
4. 添加新证书
5. 用tmpfs挂载覆盖/system/etc/security/cacerts/
6. 将所有证书（含新证书）复制到tmpfs中
"""
    import time

    cert_hash = get_cert_hash_python(cert_pem_path)
    if not cert_hash:
        return (False, "无法计算证书hash（cryptography库缺失）")

    if check_cert_installed(adb_path, cert_hash, device):
        return (True, "证书已安装")

    def adb_cmd(args: list) -> Tuple[int, str, str]:
        cmd = [adb_path]
        if device:
            cmd.extend(["-s", device])
        cmd.extend(args)
        return _run_cmd(cmd)

    def adb_shell(cmd_str: str, as_root: bool = True) -> Tuple[int, str, str]:
        if as_root:
            escaped = cmd_str.replace("'", "'\\''")
            return adb_cmd(["shell", f"su -c '{escaped}'"])
        return adb_cmd(["shell", cmd_str])

    tmp_cert = os.path.join(tempfile.gettempdir(), f"{cert_hash}.0")
    try:
        with open(cert_pem_path, "r") as f:
            pem_content = f.read()
        with open(tmp_cert, "w") as f:
            f.write(pem_content)
    except Exception as e:
        return (False, f"读取证书失败: {e}")

    tmp_remote = f"/data/local/tmp/{cert_hash}.0"
    tmp_certs_dir = "/data/local/tmp/cacerts_overlay"

    rc, out, err = adb_cmd(["push", tmp_cert, tmp_remote])
    if rc != 0:
        return (False, f"证书推送失败: {err}")

    adb_shell(f"rm -rf {tmp_certs_dir}")
    adb_shell(f"mkdir -p {tmp_certs_dir}")
    rc, out, err = adb_shell(f"cp /system/etc/security/cacerts/* {tmp_certs_dir}/")
    if rc != 0 or "No such file" in out + err:
        adb_cmd(["shell", f"rm {tmp_remote}"])
        return (False, f"复制系统证书失败: {out}{err}")

    adb_shell(f"cp {tmp_remote} {tmp_certs_dir}/{cert_hash}.0")

    rc, out, err = adb_shell("mount -t tmpfs tmpfs /system/etc/security/cacerts")
    if rc != 0 or "Permission denied" in out + err:
        adb_shell(f"rm -rf {tmp_certs_dir}")
        adb_shell(f"rm {tmp_remote}")
        return (False, f"tmpfs挂载失败(需root): {out}{err}")

    time.sleep(0.3)

    adb_shell(f"cp {tmp_certs_dir}/* /system/etc/security/cacerts/")
    adb_shell("chmod 644 /system/etc/security/cacerts/*")

    adb_shell(f"rm -rf {tmp_certs_dir}")
    adb_shell(f"rm {tmp_remote}")

    try:
        os.remove(tmp_cert)
    except:
        pass

    time.sleep(0.5)

    if check_cert_installed(adb_path, cert_hash, device):
        return (True, f"证书安装成功(tmpfs overlay) ({cert_hash}.0)")

    return (False, "证书安装验证失败")

def auto_setup_emulator_cert() -> Tuple[bool, str]:
    """
一键自动安装证书到模拟器

Returns:
    (成功与否, 状态消息)
"""
    adb = find_adb()
    if not adb:
        return (False, "未找到ADB，请确认模拟器已安装")

    devices = get_connected_devices(adb)
    if not devices:
        for port in (5555, 5556, 5557, 62001, 62025, 62026, 21503):
            _run_cmd([adb, "connect", f"127.0.0.1:{port}"])
        devices = get_connected_devices(adb)

    if not devices:
        return (False, "未检测到模拟器连接，请确认模拟器已启动")

    cert_path = os.path.join(os.path.expanduser("~"), ".mitmproxy", "mitmproxy-ca-cert.pem")
    if not os.path.exists(cert_path):
        cert_cer = os.path.join(os.path.expanduser("~"), ".mitmproxy", "mitmproxy-ca-cert.cer")
        if os.path.exists(cert_cer):
            cert_path = cert_cer
        else:
            return (False, "mitmproxy证书未生成，首次运行会自动生成")

    device = devices[0]
    success, msg = install_cert_to_emulator(adb, cert_path, device)

    if success:
        return (True, f"模拟器证书就绪 [{device}] {msg}")

    return (False, f"模拟器证书安装失败 [{device}]: {msg}")
