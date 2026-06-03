#!/usr/bin/env python
"""Live Romestead inventory viewer and item adder."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from romestead_live_client import get_live_inventory, remove_live_slot, send_command


DEFAULT_GAME_DIR = Path(r"D:\SteamLibrary\steamapps\common\romestead")


def resource_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


ROOT = resource_root()
CATALOG_PATH = ROOT / "items_catalog.json"

SECTIONS = {
    "inventory": "背包",
    "equipment": "装备",
    "secondary": "副装备",
}

CATEGORY_BY_PREFIX = {
    "material": "材料",
    "food": "食物",
    "seed": "种子",
    "ammo": "弹药",
    "weapon": "武器",
    "armor": "护甲",
    "axe": "工具",
    "pickaxe": "工具",
    "torch": "工具",
    "trinket": "饰品",
    "furniture": "家具/建筑",
    "placeable": "家具/建筑",
    "consumable": "消耗品",
    "potion": "消耗品",
    "money": "货币",
    "quest": "任务",
    "wardeclaration": "宣战书",
}

PREFERRED_CATEGORIES = [
    "全部",
    "材料",
    "食物",
    "种子",
    "弹药",
    "武器",
    "护甲",
    "工具",
    "饰品",
    "家具/建筑",
    "消耗品",
    "货币",
    "任务",
    "宣战书",
    "其他",
]


class LiveInventoryApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Romestead 实时背包修改器")
        self.root.geometry("1180x720")
        self.root.minsize(980, 620)

        self.catalog: list[dict] = []
        self.catalog_by_id: dict[str, dict] = {}
        self.inventory_data: dict = {"sections": []}
        self.trees: dict[str, ttk.Treeview] = {}
        self.current_section = "inventory"
        self.current_slot: int | None = None
        self.auto_after_id: str | None = None
        self.bridge_wait_after_id: str | None = None
        self.bridge_wait_deadline = 0.0

        self.status_var = tk.StringVar(value="等待连接游戏")
        self.game_dir_var = tk.StringVar(value=str(DEFAULT_GAME_DIR))
        self.search_var = tk.StringVar()
        self.auto_refresh_var = tk.BooleanVar(value=False)
        self.section_var = tk.StringVar(value="-")
        self.slot_var = tk.StringVar(value="-")
        self.name_var = tk.StringVar(value="")
        self.base_id_var = tk.StringVar(value="")
        self.stack_var = tk.StringVar(value="")
        self.guid_var = tk.StringVar(value="")
        self.inventory_id_var = tk.StringVar(value="")
        self.auras_var = tk.StringVar(value="")
        self.uses_var = tk.StringVar(value="")

        self.load_catalog()
        self.build_ui()
        self.root.after(200, lambda: self.start_bridge_wait("等待桥接加载；安装桥接并启动游戏后会自动读取", 20))

    def build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(9, weight=1)

        ttk.Button(toolbar, text="安装/修复桥接", command=self.install_bridge_patch).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(toolbar, text="还原桥接", command=self.restore_bridge_patch).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(toolbar, text="游戏目录...", command=self.choose_game_dir).grid(row=0, column=2, padx=(0, 10))
        ttk.Button(toolbar, text="刷新当前背包", command=self.refresh_live).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(toolbar, text="添加物品...", command=self.open_item_picker).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(toolbar, text="测试连接", command=self.ping_bridge).grid(row=0, column=5, padx=(0, 6))
        ttk.Checkbutton(
            toolbar,
            text="自动刷新",
            variable=self.auto_refresh_var,
            command=self.on_auto_refresh_changed,
        ).grid(row=0, column=6, padx=(0, 12))
        ttk.Label(toolbar, text="筛选").grid(row=0, column=7, padx=(0, 6))
        search = ttk.Entry(toolbar, textvariable=self.search_var)
        search.grid(row=0, column=9, sticky="ew")
        ttk.Button(toolbar, text="清空", command=lambda: self.search_var.set("")).grid(row=0, column=10, padx=(6, 0))
        self.search_var.trace_add("write", lambda *_: self.refresh_tables())

        pane = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        pane.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)

        left = ttk.Frame(pane)
        right = ttk.Frame(pane, width=330)
        pane.add(left, weight=4)
        pane.add(right, weight=1)

        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.notebook = ttk.Notebook(left)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        for section, label in SECTIONS.items():
            frame = ttk.Frame(self.notebook, padding=4)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            self.notebook.add(frame, text=label)
            tree = ttk.Treeview(
                frame,
                columns=("slot", "name_zh", "base", "quest", "stack", "uses", "auras", "guid"),
                show="headings",
                selectmode="browse",
            )
            tree.heading("slot", text="槽位")
            tree.heading("name_zh", text="中文名")
            tree.heading("base", text="物品 ID")
            tree.heading("quest", text="任务")
            tree.heading("stack", text="数量")
            tree.heading("uses", text="使用")
            tree.heading("auras", text="Auras")
            tree.heading("guid", text="GUID")
            tree.column("slot", width=54, anchor="center", stretch=False)
            tree.column("name_zh", width=180, anchor="w")
            tree.column("base", width=270, anchor="w")
            tree.column("quest", width=54, anchor="center", stretch=False)
            tree.column("stack", width=72, anchor="e", stretch=False)
            tree.column("uses", width=70, anchor="center", stretch=False)
            tree.column("auras", width=60, anchor="center", stretch=False)
            tree.column("guid", width=230, anchor="w")
            tree.tag_configure("quest", foreground="#8a4b00")
            tree.grid(row=0, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            scroll.grid(row=0, column=1, sticky="ns")
            tree.bind("<<TreeviewSelect>>", lambda _e, s=section: self.on_select(s))
            self.trees[section] = tree

        details = ttk.LabelFrame(right, text="当前槽位", padding=10)
        details.grid(row=0, column=0, sticky="new")
        details.columnconfigure(1, weight=1)

        rows = [
            ("区域", self.section_var),
            ("槽位", self.slot_var),
            ("中文名", self.name_var),
            ("物品 ID", self.base_id_var),
            ("数量", self.stack_var),
            ("GUID", self.guid_var),
            ("库存 ID", self.inventory_id_var),
            ("Auras", self.auras_var),
            ("使用", self.uses_var),
        ]
        for row, (label, var) in enumerate(rows):
            ttk.Label(details, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(details, textvariable=var, wraplength=220).grid(row=row, column=1, sticky="ew", pady=3)

        actions = ttk.Frame(details)
        actions.grid(row=len(rows), column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions.columnconfigure((0, 1), weight=1)
        ttk.Button(actions, text="添加同类...", command=self.add_selected_item).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(actions, text="删除...", command=self.remove_selected_item).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(8, 3))
        status.grid(row=2, column=0, sticky="ew")

    def load_catalog(self) -> None:
        try:
            if CATALOG_PATH.exists():
                self.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            else:
                self.catalog = []
                messagebox.showwarning("物品库读取失败", f"没有找到物品库：{CATALOG_PATH}")
        except Exception as exc:
            self.catalog = []
            messagebox.showwarning("物品库读取失败", str(exc))
        self.catalog_by_id = {str(item.get("id")): item for item in self.catalog if item.get("id")}

    def game_dir(self) -> Path:
        return Path(self.game_dir_var.get().strip().strip('"'))

    def choose_game_dir(self) -> None:
        path = filedialog.askdirectory(
            title="选择 Romestead 游戏目录",
            initialdir=str(self.game_dir()) if self.game_dir().exists() else str(DEFAULT_GAME_DIR.parent),
        )
        if path:
            self.game_dir_var.set(path)
            self.status_var.set(f"游戏目录：{path}")

    def install_bridge_patch(self) -> None:
        game_dir = self.game_dir()
        game_dll = game_dir / "Romestead.dll"
        script = ROOT / "patch_romestead_bridge.ps1"
        bridge = ROOT / "RomesteadLiveBridge.dll"
        cecil = ROOT / "tools" / "Mono.Cecil.0.11.6" / "lib" / "net40" / "Mono.Cecil.dll"

        if not game_dll.exists():
            messagebox.showerror(
                "无法安装桥接",
                f"没有找到：\n{game_dll}\n\n如果刚验证完整性，请确认 Steam 已经下载完成。",
            )
            return
        missing = [str(path) for path in (script, bridge, cecil) if not path.exists()]
        if missing:
            messagebox.showerror("无法安装桥接", "EXE 缺少内置文件：\n" + "\n".join(missing))
            return
        if self.is_game_running():
            messagebox.showerror("无法安装桥接", "请先关闭 Romestead，再安装/修复桥接。")
            return
        if not messagebox.askyesno(
            "安装/修复桥接",
            f"将基于当前游戏 DLL 自动打补丁：\n{game_dll}\n\n继续？",
        ):
            return

        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Install",
            "-GameDir",
            str(game_dir),
        ]
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except Exception as exc:
            messagebox.showerror("安装失败", str(exc))
            return

        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0:
            messagebox.showerror("安装失败", output.strip() or f"退出码：{proc.returncode}")
            return

        self.inventory_data = {"sections": []}
        self.refresh_tables()
        self.status_var.set("桥接补丁安装完成；启动游戏并进入存档后会自动读取")
        self.start_bridge_wait("桥接补丁安装完成；启动游戏并进入存档后会自动读取", 180)
        messagebox.showinfo(
            "安装完成",
            f"桥接已安装/修复。\n\n原 DLL 备份：\n{game_dir / 'Romestead.dll.bak'}",
        )

    def restore_bridge_patch(self) -> None:
        game_dir = self.game_dir()
        backup_dll = game_dir / "Romestead.dll.bak"
        game_dll = game_dir / "Romestead.dll"
        bridge_dll = game_dir / "RomesteadLiveBridge.dll"

        if self.is_game_running():
            messagebox.showerror("无法还原桥接", "请先关闭 Romestead，再还原桥接。")
            return
        if not backup_dll.exists():
            messagebox.showerror(
                "没有找到备份",
                f"没有找到：\n{backup_dll}\n\n如果你已经用 Steam 验证完整性恢复原版 DLL，则不需要再还原。",
            )
            return
        if not messagebox.askyesno(
            "还原桥接",
            f"将用备份覆盖当前 DLL：\n{backup_dll}\n\n并删除：\n{bridge_dll}\n\n继续？",
        ):
            return

        try:
            shutil.copy2(backup_dll, game_dll)
            if bridge_dll.exists():
                bridge_dll.unlink()
        except Exception as exc:
            messagebox.showerror("还原失败", str(exc))
            return

        self.cancel_bridge_wait()
        self.inventory_data = {"sections": []}
        self.refresh_tables()
        self.status_var.set("已还原原版 Romestead.dll，并移除桥接 DLL")
        messagebox.showinfo("还原完成", f"已从备份还原：\n{backup_dll}")

    def is_game_running(self) -> bool:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "if (Get-Process -Name Romestead -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def ping_bridge(self) -> None:
        try:
            status, message = send_command(["ping"], timeout=2.0)
            if status == "OK":
                self.status_var.set(f"桥接正常：{message}")
            else:
                messagebox.showerror("桥接失败", message)
        except Exception as exc:
            messagebox.showerror("桥接失败", str(exc))

    def start_bridge_wait(self, message: str, timeout_seconds: int = 180) -> None:
        self.cancel_bridge_wait()
        self.bridge_wait_deadline = time.monotonic() + timeout_seconds
        self.status_var.set(message)
        self.bridge_wait_after_id = self.root.after(500, self.poll_bridge_until_ready)

    def cancel_bridge_wait(self) -> None:
        if self.bridge_wait_after_id is not None:
            try:
                self.root.after_cancel(self.bridge_wait_after_id)
            except Exception:
                pass
            self.bridge_wait_after_id = None

    def poll_bridge_until_ready(self) -> None:
        self.bridge_wait_after_id = None
        try:
            status, _message = send_command(["ping"], timeout=0.75)
            if status == "OK":
                self.status_var.set("桥接已加载，正在读取当前背包...")
                self.root.after(300, self.refresh_live)
                return
        except Exception:
            pass

        if time.monotonic() < self.bridge_wait_deadline:
            self.bridge_wait_after_id = self.root.after(2000, self.poll_bridge_until_ready)
        else:
            self.status_var.set("未检测到桥接；确认已安装补丁、启动游戏并进入存档后再点刷新")

    def refresh_live(self) -> None:
        self.cancel_bridge_wait()
        try:
            data = get_live_inventory(timeout=1.5)
            self.inventory_data = data
            self.refresh_tables()
            filled, total = self.count_slots()
            self.status_var.set(f"已读取游戏当前背包：{filled}/{total} 个槽位有物品")
        except Exception as exc:
            self.status_var.set(f"读取失败：{exc}")
        finally:
            if self.auto_refresh_var.get():
                if self.auto_after_id is not None:
                    try:
                        self.root.after_cancel(self.auto_after_id)
                    except Exception:
                        pass
                self.auto_after_id = self.root.after(2500, self.refresh_live)

    def on_auto_refresh_changed(self) -> None:
        if self.auto_after_id is not None:
            self.root.after_cancel(self.auto_after_id)
            self.auto_after_id = None
        if self.auto_refresh_var.get():
            self.auto_after_id = self.root.after(2500, self.refresh_live)

    def count_slots(self) -> tuple[int, int]:
        filled = 0
        total = 0
        for section in self.inventory_data.get("sections", []):
            slots = section.get("slots") or []
            total += len(slots)
            filled += sum(1 for item in slots if item)
        return filled, total

    def refresh_tables(self) -> None:
        needle = self.search_var.get().strip().lower()
        sections = {section.get("key"): section for section in self.inventory_data.get("sections", [])}
        for section_key, tree in self.trees.items():
            selected = tree.selection()
            selected_iid = selected[0] if selected else None
            tree.delete(*tree.get_children())
            section = sections.get(section_key) or {}
            slots = section.get("slots") or []
            for idx, item in enumerate(slots):
                if item:
                    base_id = str(item.get("base_data_id") or "")
                    meta = self.catalog_by_id.get(base_id, {})
                    name_zh = str(meta.get("name_zh") or base_id)
                    if needle and needle not in base_id.lower() and needle not in name_zh.lower():
                        continue
                    values = (
                        idx,
                        name_zh,
                        base_id,
                        "是" if meta.get("is_quest") else "",
                        item.get("stack_count") or "",
                        self.format_uses(item),
                        item.get("auras_count") if item.get("auras_count") is not None else "",
                        item.get("id") or "",
                    )
                    tags = ("quest",) if meta.get("is_quest") else ()
                else:
                    if needle:
                        continue
                    values = (idx, "", "", "", "", "", "", "")
                    tags = ()
                iid = f"{section_key}:{idx}"
                tree.insert("", tk.END, iid=iid, values=values, tags=tags)
            if selected_iid and tree.exists(selected_iid):
                tree.selection_set(selected_iid)

    def format_uses(self, item: dict) -> str:
        uses = item.get("uses_left")
        cooldown = item.get("cooldown_remaining")
        if uses is None and cooldown is None:
            return ""
        if cooldown is None:
            return str(uses)
        return f"{uses}/{cooldown:g}"

    def on_tab_changed(self, _event=None) -> None:
        idx = self.notebook.index(self.notebook.select())
        self.current_section = list(SECTIONS.keys())[idx]

    def on_select(self, section: str) -> None:
        tree = self.trees[section]
        selection = tree.selection()
        if not selection:
            return
        _, slot_text = selection[0].split(":", 1)
        self.current_section = section
        self.current_slot = int(slot_text)
        self.populate_details(section, self.current_slot)

    def get_slot_item(self, section_key: str, slot: int) -> dict | None:
        for section in self.inventory_data.get("sections", []):
            if section.get("key") == section_key:
                slots = section.get("slots") or []
                if 0 <= slot < len(slots):
                    return slots[slot]
        return None

    def populate_details(self, section: str, slot: int) -> None:
        item = self.get_slot_item(section, slot)
        self.section_var.set(SECTIONS.get(section, section))
        self.slot_var.set(str(slot))
        if not item:
            self.name_var.set("")
            self.base_id_var.set("")
            self.stack_var.set("")
            self.guid_var.set("")
            self.inventory_id_var.set("")
            self.auras_var.set("")
            self.uses_var.set("")
            return

        base_id = str(item.get("base_data_id") or "")
        meta = self.catalog_by_id.get(base_id, {})
        self.name_var.set(str(meta.get("name_zh") or base_id))
        self.base_id_var.set(base_id)
        self.stack_var.set(str(item.get("stack_count") or ""))
        self.guid_var.set(str(item.get("id") or ""))
        self.inventory_id_var.set(str(item.get("inventory_id") or ""))
        self.auras_var.set(str(item.get("auras_count") if item.get("auras_count") is not None else ""))
        self.uses_var.set(self.format_uses(item))

    def item_category(self, item: dict) -> str:
        item_id = str(item.get("id") or "")
        prefix = item_id.split(":", 1)[0] if ":" in item_id else ""
        if item.get("is_quest"):
            return "任务"
        return CATEGORY_BY_PREFIX.get(prefix, "其他")

    def catalog_categories(self) -> list[str]:
        existing = {self.item_category(item) for item in self.catalog}
        ordered = [cat for cat in PREFERRED_CATEGORIES if cat == "全部" or cat in existing]
        ordered.extend(sorted(existing.difference(ordered)))
        return ordered

    def open_item_picker(self) -> None:
        if not self.catalog:
            messagebox.showerror("物品库为空", "没有读取到 items_catalog.json。")
            return

        picker = tk.Toplevel(self.root)
        picker.title("实时添加物品")
        picker.geometry("920x620")
        picker.minsize(760, 480)
        picker.transient(self.root)
        picker.grab_set()
        picker.columnconfigure(0, weight=1)
        picker.rowconfigure(1, weight=1)

        controls = ttk.Frame(picker, padding=(8, 8, 8, 4))
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(3, weight=1)

        category_var = tk.StringVar(value="全部")
        search_var = tk.StringVar()
        ttk.Label(controls, text="分类").grid(row=0, column=0, padx=(0, 6))
        ttk.Combobox(
            controls,
            textvariable=category_var,
            values=self.catalog_categories(),
            state="readonly",
            width=16,
        ).grid(row=0, column=1, padx=(0, 12))
        ttk.Label(controls, text="搜索").grid(row=0, column=2, padx=(0, 6))
        search_entry = ttk.Entry(controls, textvariable=search_var)
        search_entry.grid(row=0, column=3, sticky="ew")
        ttk.Button(controls, text="清空", command=lambda: search_var.set("")).grid(row=0, column=4, padx=(6, 0))

        tree = ttk.Treeview(
            picker,
            columns=("name", "id", "category", "quest", "max_stack", "flags"),
            show="headings",
            selectmode="browse",
        )
        tree.heading("name", text="中文名")
        tree.heading("id", text="物品 ID")
        tree.heading("category", text="分类")
        tree.heading("quest", text="任务")
        tree.heading("max_stack", text="最大堆叠")
        tree.heading("flags", text="Flags")
        tree.column("name", width=190, anchor="w")
        tree.column("id", width=260, anchor="w")
        tree.column("category", width=92, anchor="center", stretch=False)
        tree.column("quest", width=54, anchor="center", stretch=False)
        tree.column("max_stack", width=78, anchor="e", stretch=False)
        tree.column("flags", width=150, anchor="w")
        tree.tag_configure("quest", foreground="#8a4b00")
        tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=4)
        scroll = ttk.Scrollbar(picker, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns", pady=4, padx=(0, 8))

        item_by_iid: dict[str, dict] = {}

        def refresh_picker(*_args) -> None:
            needle = search_var.get().strip().lower()
            category = category_var.get()
            tree.delete(*tree.get_children())
            item_by_iid.clear()
            for index, item in enumerate(self.catalog):
                item_id = str(item.get("id") or "")
                name = str(item.get("name_zh") or item_id)
                cat = self.item_category(item)
                if category != "全部" and cat != category:
                    continue
                if needle and needle not in item_id.lower() and needle not in name.lower():
                    continue
                iid = str(index)
                item_by_iid[iid] = item
                tree.insert(
                    "",
                    tk.END,
                    iid=iid,
                    values=(
                        name,
                        item_id,
                        cat,
                        "是" if item.get("is_quest") else "",
                        item.get("max_stack", ""),
                        item.get("flags", ""),
                    ),
                    tags=("quest",) if item.get("is_quest") else (),
                )

        def selected_item() -> dict | None:
            selection = tree.selection()
            if not selection:
                return None
            return item_by_iid.get(selection[0])

        def choose_selected() -> None:
            item = selected_item()
            if item is None:
                messagebox.showinfo("未选择物品", "先选中一个物品。", parent=picker)
                return
            self.open_add_item_dialog(picker, item)

        category_var.trace_add("write", refresh_picker)
        search_var.trace_add("write", refresh_picker)
        tree.bind("<Double-1>", lambda _e: choose_selected())

        buttons = ttk.Frame(picker, padding=(8, 4, 8, 8))
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text="确定", command=choose_selected).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(buttons, text="取消", command=picker.destroy).grid(row=0, column=2)

        refresh_picker()
        search_entry.focus_set()

    def add_selected_item(self) -> None:
        if self.current_slot is None:
            messagebox.showinfo("未选择槽位", "先选择一个已有物品的槽位。")
            return
        item = self.get_slot_item(self.current_section, self.current_slot)
        if not item:
            messagebox.showinfo("空槽位", "当前槽位没有物品。")
            return
        base_id = str(item.get("base_data_id") or "")
        catalog_item = self.catalog_by_id.get(base_id, {"id": base_id, "name_zh": base_id, "is_quest": False})
        self.open_add_item_dialog(self.root, catalog_item)

    def remove_selected_item(self) -> None:
        if self.current_slot is None:
            messagebox.showinfo("未选择槽位", "先选择一个非空槽位。")
            return
        item = self.get_slot_item(self.current_section, self.current_slot)
        if not item:
            messagebox.showinfo("空槽位", "当前槽位没有物品。")
            return

        base_id = str(item.get("base_data_id") or "")
        meta = self.catalog_by_id.get(base_id, {})
        name = str(meta.get("name_zh") or base_id)
        stack = int(item.get("stack_count") or 1)
        instance_id = str(item.get("id") or "")

        dialog = tk.Toplevel(self.root)
        dialog.title("实时删除")
        dialog.geometry("430x230")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)

        amount_var = tk.StringVar(value=str(stack))

        ttk.Label(dialog, text="物品").grid(row=0, column=0, sticky="w", padx=10, pady=(12, 4))
        ttk.Label(dialog, text=f"{name}\n{base_id}", wraplength=300).grid(row=0, column=1, sticky="ew", padx=10, pady=(12, 4))
        ttk.Label(dialog, text="位置").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        ttk.Label(dialog, text=f"{SECTIONS.get(self.current_section, self.current_section)} {self.current_slot}").grid(
            row=1, column=1, sticky="w", padx=10, pady=4
        )
        ttk.Label(dialog, text="当前数量").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        ttk.Label(dialog, text=str(stack)).grid(row=2, column=1, sticky="w", padx=10, pady=4)
        ttk.Label(dialog, text="删除数量").grid(row=3, column=0, sticky="w", padx=10, pady=4)
        amount_entry = ttk.Entry(dialog, textvariable=amount_var)
        amount_entry.grid(row=3, column=1, sticky="ew", padx=10, pady=4)

        def confirm() -> None:
            try:
                amount = int(amount_var.get().strip())
                if amount < 1:
                    raise ValueError("删除数量必须大于 0")
                if amount > stack:
                    raise ValueError("删除数量不能大于当前数量")
                if not messagebox.askyesno(
                    "删除确认",
                    f"确定从 {SECTIONS.get(self.current_section, self.current_section)} {self.current_slot} 删除：\n"
                    f"{name} x{amount}？",
                    parent=dialog,
                ):
                    return

                status, message = remove_live_slot(
                    self.current_section,
                    self.current_slot,
                    amount,
                    instance_id,
                    timeout=5.0,
                )
                if status != "OK":
                    raise RuntimeError(message)
                self.status_var.set(f"已实时删除：{name} x{amount}")
                dialog.destroy()
                self.root.after(400, self.refresh_live)
            except Exception as exc:
                messagebox.showerror("实时删除失败", str(exc), parent=dialog)

        buttons = ttk.Frame(dialog, padding=(10, 8, 10, 10))
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text="确定删除", command=confirm).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(buttons, text="取消", command=dialog.destroy).grid(row=0, column=2)
        amount_entry.focus_set()

    def open_add_item_dialog(self, parent: tk.Misc, item: dict) -> None:
        item_id = str(item.get("id") or "")
        name = str(item.get("name_zh") or item_id)

        dialog = tk.Toplevel(parent)
        dialog.title("实时添加")
        dialog.geometry("420x210")
        dialog.resizable(False, False)
        dialog.transient(parent)
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)

        amount_var = tk.StringVar(value="1")

        ttk.Label(dialog, text="物品").grid(row=0, column=0, sticky="w", padx=10, pady=(12, 4))
        ttk.Label(dialog, text=f"{name}\n{item_id}", wraplength=300).grid(row=0, column=1, sticky="ew", padx=10, pady=(12, 4))
        ttk.Label(dialog, text="任务道具").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        ttk.Label(dialog, text="是" if item.get("is_quest") else "否").grid(row=1, column=1, sticky="w", padx=10, pady=4)
        ttk.Label(dialog, text="数量").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        amount_entry = ttk.Entry(dialog, textvariable=amount_var)
        amount_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=4)
        ttk.Label(dialog, text=f"最大堆叠：{item.get('max_stack', '-')}", foreground="#555").grid(
            row=3, column=1, sticky="w", padx=10, pady=4
        )

        def confirm() -> None:
            try:
                amount = int(amount_var.get().strip())
                if amount < 1:
                    raise ValueError("数量必须大于 0")
                if item.get("is_quest"):
                    if not messagebox.askyesno(
                        "任务道具确认",
                        f"{name} 是任务道具。\n\n仍然实时添加到游戏？",
                        parent=dialog,
                    ):
                        return
                status, message = send_command(["add_item", item_id, str(amount), ""], timeout=5.0)
                if status != "OK":
                    raise RuntimeError(message)
                self.status_var.set(f"已实时添加：{name} x{amount}")
                dialog.destroy()
                self.root.after(400, self.refresh_live)
            except Exception as exc:
                messagebox.showerror("实时添加失败", str(exc), parent=dialog)

        buttons = ttk.Frame(dialog, padding=(10, 8, 10, 10))
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text="确定", command=confirm).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(buttons, text="取消", command=dialog.destroy).grid(row=0, column=2)
        amount_entry.focus_set()


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    LiveInventoryApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
