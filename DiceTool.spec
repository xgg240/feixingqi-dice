# -*- mode: python ; coding: utf-8 -*-
# 飞行棋骰子工具 v2.4 — PyInstaller spec
# 编译: pyinstaller DiceTool.spec
# 输出: dist/DiceTool.exe (管理员权限启动, 单文件)

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# v2.6.2 回归 mitmproxy 8.0.0 老架构 (纯 Python, mode_specs.py 不引 mitmproxy_rs)
try:
    mitm_datas, mitm_bins, mitm_hidden = collect_all('mitmproxy')
    print(f'[spec] collect_all mitmproxy: {len(mitm_hidden)} hidden imports, {len(mitm_bins)} binaries, {len(mitm_datas)} datas')
except Exception as e:
    print(f'[spec] WARN collect_all mitmproxy failed: {e}')
    mitm_datas, mitm_bins, mitm_hidden = [], [], []

# 递归扫 mitmproxy 所有子 module (补 collect_all 的漏)
try:
    mitm_submodules = collect_submodules('mitmproxy')
    print(f'[spec] collect_submodules mitmproxy: {len(mitm_submodules)} modules')
    mitm_hidden = list(set(mitm_hidden + mitm_submodules))
except Exception as e:
    print(f'[spec] WARN collect_submodules mitmproxy failed: {e}')

# v2.6.2: 回归 mitmproxy 8.0.0 老架构 (纯 Python, 没 mitmproxy_rs)
# v2.6.0/2.6.1 误判 "10.x 没有 Rust pyd" — 实际 9.x/10.x/11.x 的 mode_specs.py 都强 import mitmproxy_rs

try:
    tk_datas, tk_bins, tk_hidden = collect_all('tkinter')
except Exception as e:
    print(f'[spec] WARN collect_all tkinter failed: {e}')
    tk_datas, tk_bins, tk_hidden = [], [], []

# v2.6.0: mitmproxy 10.x 自带 windivert.dll (在 mitmproxy/windows/ 下)
wd_bin = []
try:
    import mitmproxy.windows
    wd_dll = os.path.join(os.path.dirname(mitmproxy.windows.__file__), 'windivert.dll')
    if os.path.exists(wd_dll):
        wd_bin = [(wd_dll, '.')]
        print(f'[spec] windivert.dll found: {wd_dll}')
    else:
        print('[spec] WARN: windivert.dll not found, code will auto-download on Win')
except Exception as e:
    print(f'[spec] WARN: cannot locate windivert.dll: {e}')

# v2.6.0: pydivert 2.1+ 也带 windivert 驱动 (WinDivert.dll/Sys)
pd_bin = []
try:
    import pydivert
    pd_dir = os.path.dirname(pydivert.__file__)
    for fn in os.listdir(pd_dir):
        if fn.lower().endswith('.dll') or fn.lower().endswith('.sys'):
            pd_bin.append((os.path.join(pd_dir, fn), '.'))
    if pd_bin:
        print(f'[spec] pydivert 抓了 {len(pd_bin)} 个 dll/sys')
except Exception as e:
    print(f'[spec] WARN: pydivert 扫描失败: {e}')

# 业务代码 (你写的 5 个 .py + 2 个 __init__.py)
biz_datas = [
    ('core', 'core'),
    ('gui', 'gui'),
    ('proxy', 'proxy'),
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=mitm_bins + tk_bins + wd_bin + pd_bin,
    datas=mitm_datas + tk_datas + biz_datas,
    hiddenimports=mitm_hidden + tk_hidden + [
        'core.emu_cert',
        'gui.main_window',
        'proxy.divert_proxy',
        'proxy.addon_script',
        # 业务依赖
        'pydivert',
        'pydivert.windivert',
        'cryptography',
        'cryptography.hazmat.primitives.serialization',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 排除不需要的库减小体积
        'unittest', 'pydoc_data', 'lib2to3', 'tkinter.test',
        'matplotlib', 'numpy', 'scipy', 'pandas',
        # v2.6.2: 不用 Rust 扩展 (mitmproxy 8.x 没这些 module, 列着无害)
        'mitmproxy_rs',
        'mitmproxy_windows',
        'mitmproxy_macos',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# v2.5.0: 改用 --onedir 模式 (生成 DiceTool/ 目录)
# 单文件模式 + 子进程 [exe --mitmdump] 在 PyInstaller 6.21 + mitmproxy 11.x 下会 import 不出来
# onedir 模式下子进程的 _MEIPASS=DiceTool/_internal/ 业务 modules 都能被 bootloader 加载
# ⚠️ 关键: uac_admin=True → 启动自动弹 UAC 提权
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # ← onedir 模式: binaries 走 COLLECT
    name='DiceTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=['windivert.dll', 'vcruntime140.dll', 'msvcp140.dll', '*.pyd', '*.dll'],
    runtime_tmpdir=None,
    console=False,           # GUI 模式, 不要黑色 cmd 窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,          # ← 管理员权限 (跟 main.py run_as_admin() 配套)
    icon=None,               # 想要图标就放 .ico 路径, 例: 'app.ico'
)

# COLLECT: 收 binaries / zipfiles / datas 到 DiceTool/_internal/
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=['windivert.dll', 'vcruntime140.dll', 'msvcp140.dll', '*.pyd', '*.dll'],
    name='DiceTool',
)
