# 飞行棋骰子工具 v2.4

> **仅供合法用途**: 父的自营飞行棋系统 (Speakeasy DSL Dallas VPS) 自动化测试工具, 灰盒自测

## 文件结构

```
.
├── main.py                       # 主入口 (Tk root + 业务编排 + 子进程管理)
├── core/
│   └── emu_cert.py              # ADB + 模拟器证书自动安装
├── gui/
│   └── main_window.py           # tkinter GUI
├── proxy/
│   ├── divert_proxy.py          # WinDivert 抓包 + mitmdump 子进程管理
│   └── addon_script.py          # mitmproxy addon (WebSocket 协议)
├── requirements.txt              # pip 依赖锁定
├── DiceTool.spec                 # PyInstaller 打包配置
├── .github/
│   └── workflows/
│       └── build-windows.yml     # GitHub Actions 自动 build
└── README.md                     # 本文件
```

## 三种编译方式

### 方式 1: 本地 Win 编译 (最快, 单机)

```cmd
:: 1. 装 Python 3.10 / 3.11 (https://www.python.org/downloads/)
:: 2. clone 仓库
git clone <repo-url>
cd <repo>

:: 3. 装依赖
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

:: 4. 编译
pyinstaller DiceTool.spec

:: 5. 跑
dist\DiceTool.exe   :: 右键 → 管理员身份运行
```

### 方式 2: GitHub Actions 自动 build (推荐, 免配 Win)

```bash
# 1. 推代码到 GitHub
git init && git add . && git commit -m "init"
git branch -M main
git remote add origin git@github.com:<你的用户名>/<仓库>.git
git push -u origin main

# 2. 推 tag 触发自动 build
git tag v2.4.0
git push origin v2.4.0

# 3. 等 5-10 分钟, GitHub Actions 会:
#    - Win Server 2022 runner
#    - Python 3.11
#    - 装依赖 + 编译
#    - 上传 DiceTool.exe 到 release 页
#    - 自动创建 GitHub Release
```

下载: 仓库 → Releases → v2.4.0 → 找 `DiceTool.exe` 下载

### 方式 3: 本地 macOS / Linux 交叉编译 (不可行)

**PyInstaller 不支持交叉编译**, 必须在 Win 下编。Linux/macOS 编出来的 exe 不能在 Win 跑。

## 依赖说明 (常见坑)

| 依赖 | 用途 | 坑 |
|---|---|---|
| `mitmproxy==8.0.0` | mitmdump 子进程 | 必须 8.0+, 7.x addon API 不一样 |
| `pydivert==2.1.0` | WinDivert 抓包 | **Win-only**, 装在 Mac/Linux 上没意义 |
| `pycryptodomex` | cryptography 依赖 | PyInstaller 经常丢这个, `--collect-all cryptography` 修 |
| `tkinter` | GUI | Python 3.13 偶尔缺 wheel, 用 3.11/3.12 稳 |

## 编译参数说明 (DiceTool.spec)

| 参数 | 含义 |
|---|---|
| `--onefile` | 单 exe, ~80MB |
| `--noconsole` | GUI 模式无黑色窗口 |
| `--uac-admin` | 启动自动弹 UAC 提权 (必须, 跟 main.py run_as_admin 配套) |
| `--collect-all mitmproxy` | mitmproxy 用动态 addon, 普通 hidden-import 不够 |
| `--add-binary windivert.dll;.` | 驱动塞进 _MEIPASS, 运行时能找到 |
| `--add-data "proxy;proxy"` | 业务代码 .py 塞进 _MEIPASS |
| `excludes=[numpy, pandas, ...]` | 减体积 (40MB → 25MB) |

## 输出体积

- 单文件 exe: **60-90 MB** (mitmproxy + tkinter + windivert 全塞)
- 第一次启动解压: 5-10 秒
- 后续启动: 1-2 秒

## 验证编译成功

跑 `dist\DiceTool.exe` 应该看到:
1. 黑色 cmd 窗口闪一下 (UAC 提权)
2. UAC 弹窗 → 同意
3. tkinter 窗口弹出
4. log 区显示 `[HH:MM:SS] ✓ 模拟器证书检查完成` / `[HH:MM:SS] ✓ ADB 已就绪`
5. 5 个模式按钮 (K/R/D/J/Q) + 6 个骰子按钮 (1-6) 可点

如果 GUI 弹不出, 看 64.81.114.52 / 飞行棋系统 / 卡密系统 配套文档, 或者把 `dist\DiceTool.exe` 拉回 mac 用 `wine` 跑看错 (wine 兼容性可能差, 推荐 Win 实机测)。

## GitHub Actions 故障排除

| 错 | 原因 | 修 |
|---|---|---|
| `ModuleNotFoundError: No module named 'X'` | requirements.txt 没装 | 加 `X` 到 requirements, 推 tag |
| `windivert.dll not found` | mitmproxy 没装 | 加 `pip install mitmproxy` 步骤 |
| `PyInstaller ImportError: tkinter` | Python 缺 tkinter | 换 Python 3.11 (3.13 偶发缺) |
| `DLL load failed` | windivert 没塞进 _MEIPASS | spec 里 `--add-binary windivert.dll;.` |
| `UPX not found` | UPX 没装 (Linux runner) | Win runner 自带, 不存在 |
| 体积太大 (200MB+) | 没 excludes numpy | spec 加 excludes |
| UAC 不弹 | 缺 `uac_admin=True` | spec EXE 节加 `uac_admin=True` |

## License

仅供父的飞行棋系统自动化测试, 禁止外传。
