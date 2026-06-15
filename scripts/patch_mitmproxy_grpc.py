"""
v2.6.7 monkey-patch: 修 mitmproxy 8.0.0 (2022) 在 Py3.11+ dataclass 严格化的崩溃。

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

v2.6.7 fix: 全部 print 用英文 (Windows runner pwsh 默认 cp1252 console,
中文 print 直接 UnicodeEncodeError 把整个 patch step 杀掉, patch 根本没跑,
后面验证 import 当然挂在原样的 mutable default 上)
"""

import io
import re
import sys
from pathlib import Path


def patch_grpc_py(site_packages: Path) -> bool:
    grpc_py = site_packages / "mitmproxy" / "contentviews" / "grpc.py"
    if not grpc_py.exists():
        print(f"[patch] {grpc_py} not found, skip")
        return False

    original = grpc_py.read_text(encoding="utf-8")

    # already patched => skip (idempotent)
    sentinel = "# v2.6.6-patched: default_factory wrap"
    if sentinel in original:
        print(f"[patch] {grpc_py} already patched, skip")
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
        print(f"[patch] target dataclass not found in {grpc_py}, mitmproxy version may have changed")
        return False

    patched = original.replace(target_old, target_new)
    grpc_py.write_text(patched, encoding="utf-8")
    print(f"[patch] {grpc_py} patched (mutable default -> default_factory)")
    return True


def main() -> int:
    # v2.6.7: 强制 UTF-8 stdout, 兜底 Windows cp1252 console
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import site
    roots = [Path(p) for p in site.getsitepackages()] + [Path(site.getusersitepackages())]
    patched_any = False
    for root in roots:
        if patch_grpc_py(root):
            patched_any = True
    if not patched_any:
        print("[patch] mitmproxy install not found in any site-packages dir, abort")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
