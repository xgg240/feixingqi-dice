"""
飞行棋骰子工具 - GUI界面（暗色紧凑版v4）
"""

import tkinter as tk
from tkinter import ttk
import os
import sys
from typing import Optional, Callable


class DiceToolGUI:
    def __init__(
        self,
        on_dice_select: Optional[Callable] = None,
        on_mode_change: Optional[Callable] = None,
    ):
        self._on_dice_select = on_dice_select
        self._on_mode_change = on_mode_change
        self._six_locked = False
        self._current_dice = None
        self._build_ui()

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("飞行棋骰子工具")
        self.root.geometry("340x380")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e1e")

        self._set_icon()
        self.root.after(50, self._set_dark_titlebar)

        dice_frame = tk.Frame(self.root, bg="#1e1e1e")
        dice_frame.pack(pady=(12, 8), padx=20, fill="x")

        for col in range(3):
            dice_frame.columnconfigure(col, weight=1)

        self.dice_buttons = {}
        for i in range(1, 7):
            row = (i - 1) // 3
            col = (i - 1) % 3
            btn = tk.Button(
                dice_frame,
                text=str(i),
                font=("微软雅黑", 18, "bold"),
                height=1,
                relief="flat",
                cursor="hand2",
                bg="#333333",
                fg="white",
                activebackground="#555",
                activeforeground="white",
                command=lambda d=i: self._on_dice_click(d),
            )
            btn.grid(row=row, column=col, padx=8, pady=6, sticky="ew")
            self.dice_buttons[i] = btn

        mode_frame = tk.Frame(self.root, bg="#1e1e1e")
        mode_frame.pack(pady=(4, 4), padx=14, fill="x")

        tk.Label(
            mode_frame,
            text="模式",
            font=("微软雅黑", 9),
            fg="#777",
            bg="#1e1e1e",
        ).pack(side="left", padx=(0, 8))

        self.mode_var = tk.StringVar(value="")
        self.mode_buttons = {}

        for text, value in (("K", "K"), ("R", "R"), ("D", "D"), ("J", "J"), ("QK", "Q")):
            rb = tk.Radiobutton(
                mode_frame,
                text=text,
                variable=self.mode_var,
                value=value,
                font=("微软雅黑", 11, "bold"),
                bg="#1e1e1e",
                fg="#ccc",
                selectcolor="#2e7d32",
                activebackground="#2e7d32",
                activeforeground="white",
                indicatoron=0,
                width=3,
                relief="flat",
                overrelief="raised",
                command=self._on_mode_select,
            )
            rb.pack(side="left", padx=4)
            self.mode_buttons[value] = rb

        log_frame = tk.Frame(self.root, bg="#1e1e1e")
        log_frame.pack(pady=(0, 2), padx=14, fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame,
            height=8,
            font=("Consolas", 10),
            bg="#111111",
            fg="#00cc66",
            insertbackground="#00cc66",
            relief="flat",
            borderwidth=1,
            wrap="word",
            highlightbackground="#333",
            highlightthickness=1,
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")

        bottom = tk.Frame(self.root, bg="#1e1e1e")
        bottom.pack(pady=(0, 6), padx=14, fill="x")

        self.status_dot = tk.Label(
            bottom,
            text="●",
            font=("", 9),
            fg="#4CAF50",
            bg="#1e1e1e",
        )
        self.status_dot.pack(side="left")

        self.status_label = tk.Label(
            bottom,
            text="运行中",
            font=("微软雅黑", 8),
            fg="#666",
            bg="#1e1e1e",
        )
        self.status_label.pack(side="left", padx=(3, 0))

    def _on_dice_click(self, dice: int):
        if dice == 6 and self._six_locked:
            return

        if self._current_dice == dice:
            self._current_dice = None
            btn = self.dice_buttons[dice]
            btn.config(bg="#333333", fg="white")
            if self._on_dice_select:
                self._on_dice_select(None)
            return

        self._current_dice = dice
        for d, btn in self.dice_buttons.items():
            if d == dice:
                btn.config(bg="#2e7d32", fg="white")
                continue
            if d == 6 and self._six_locked:
                continue
            btn.config(bg="#333333", fg="white")

        if self._on_dice_select:
            self._on_dice_select(dice)

    def _on_mode_select(self):
        mode = self.mode_var.get()

        for text, rb in self.mode_buttons.items():
            if text == mode:
                rb.config(bg="#2e7d32", fg="white")
                continue
            rb.config(bg="#1e1e1e", fg="#ccc")

        if self._on_mode_change:
            self._on_mode_change(mode)

    def clear_selection(self):
        """修改完毕后恢复按钮颜色"""
        self._current_dice = None
        for d, btn in self.dice_buttons.items():
            if d == 6 and self._six_locked:
                continue
            btn.config(bg="#333333", fg="white")

    def set_six_locked(self, locked: bool):
        self._six_locked = locked
        btn = self.dice_buttons[6]
        if locked:
            btn.config(state="disabled", bg="#4a2020", fg="#ff5252")
            return
        btn.config(state="normal", bg="#333333", fg="white")

    def set_status(self, text: str, color: str = "#4CAF50"):
        self.root.after(
            0,
            lambda: (
                self.status_label.config(text=text),
                self.status_dot.config(fg=color),
            ),
        )

    def log(self, message: str):
        def _append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 200:
                self.log_text.delete("1.0", "50.0")
            self.log_text.config(state="disabled")

        self.root.after(0, _append)

    def run(self):
        self.root.mainloop()

    def close(self):
        self.root.quit()
        self.root.destroy()

    def _set_icon(self):
        """设置窗口图标（标题栏+任务栏）"""
        base_paths = []
        if hasattr(sys, "_MEIPASS"):
            base_paths.append(sys._MEIPASS)
        base_paths.extend(
            [
                os.path.dirname(sys.executable),
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                os.path.dirname(os.path.abspath(__file__)),
            ]
        )

        for base in base_paths:
            icon_path = os.path.join(base, "plane.ico")
            if not os.path.exists(icon_path):
                continue
            try:
                self.root.iconbitmap(icon_path)
                from PIL import Image, ImageTk

                img = Image.open(icon_path)
                photo = ImageTk.PhotoImage(img.resize((32, 32), Image.LANCZOS))
                self.root.iconphoto(True, photo)
                self._icon_photo = photo
            except Exception:
                try:
                    self.root.iconbitmap(icon_path)
                except Exception:
                    pass
            return

    def _set_dark_titlebar(self):
        """Windows 10/11 暗色标题栏"""
        try:
            import ctypes

            self.root.update()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int),
            )
            self.root.withdraw()
            self.root.after(10, self.root.deiconify)
        except Exception:
            return
