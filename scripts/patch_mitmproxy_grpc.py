"""
v2.6.6 monkey-patch: 修 mitmproxy 8.0.0 (2022) 在 Py3.11+ dataclass 严格化的崩溃。

mitmproxy 8.0.0/contentviews/grpc.py 第 906-909 行:
    @dataclass
    class ViewConfig:
        parser_options: ProtoParser.ParserOptions = ProtoParser.ParserOptions()  # mutable default
        parser_rules: List[ProtoParser.ParserRule] = field(default_factory=list)

Py3.11+ dataclasses 拒绝 mutable default, ValueError 抛在 import 阶段,
直接挂掉 from mitmproxy.tools.main import mitmdump 整条链.

fix: 把 ProtoParser.ParserOptions() 包成 field(default_factory=...),
保留 default_factory=list 那一行不动 (本来就对).

这个 patch 由 .github/workflows/build-windows.yml 装完包后调用一次, 改 site-packages
里的 grpc.py, 让 PyInstaller froze 时拿到的是修好的版本.
"""

import io
import re
import sys
from pathlib import Path


def patch_grpc_py(site_packages: Path) -> bool:
    grpc_py = site_packages / "mitmproxy" / "contentviews" / "grpc.py"
    if not grpc_py.exists():
        print(f"[patch] {grpc_py} 不存在, 跳过")
        return False

    original = grpc_py.read_text(encoding="utf-8")

    # 已经 patch 过就别再 patch (idempotent)
    sentinel = "# v2.6.6-patched: default_factory wrap"
    if sentinel in original:
        print(f"[patch] {grpc_py} 已经 patch 过, 跳过")
        return True

    target_old = (
        "    parser_options: ProtoParser.ParserOptions = ProtoParser.ParserOptions()\n"
        "    parser_rules: List[ProtoParser.ParserRule] = field(default_factory=list)\n"
    )
    target_new = (
        "    parser_options: ProtoParser.ParserOptions = field(  # v2.6.6-patched: default_factory wrap\n"
        "        default_factory=ProtoParser.ParserOptions\n"
        "    )\n"
        "    parser_rules: List[ProtoParser.ParserRule] = field(default_factory=list)\n"
    )

    if target_old not in original:
        print(f"[patch] 在 {grpc_py} 找不到目标 dataclass, mitmproxy 版本可能已变")
        return False

    patched = original.replace(target_old, target_new)
    grpc_py.write_text(patched, encoding="utf-8")
    print(f"[patch] {grpc_py} 改完 (mutable default → default_factory)")
    return True


def main() -> int:
    import site
    roots = [Path(p) for p in site.getsitepackages()] + [Path(site.getusersitepackages())]
    patched_any = False
    for root in roots:
        if patch_grpc_py(root):
            patched_any = True
    if not patched_any:
        print("[patch] 没找到 mitmproxy 安装位置, abort")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
