# Romestead 实时桥接说明

## 当前方案

实时工具走 DLL 桥接：

1. 读取游戏目录中的当前 `Romestead.dll`。
2. 用 Mono.Cecil 给它打一个小补丁。
3. 补丁会在游戏运行后加载 `RomesteadLiveBridge.dll`。
4. 修改器通过命名管道向桥接 DLL 发送实时命令。

安装补丁时会在游戏目录生成原 DLL 备份：

```text
Romestead.dll.bak
```

如果 `Romestead.dll.bak` 已存在，安装器会保留旧备份，不会用补丁版覆盖它。

实时桥接命令：

- `ping`：检查桥接是否加载。
- `get_inventory`：读取游戏进程里的当前背包、装备、副装备。
- `add_item`：通过游戏自己的作弊消息实时添加物品。
- `remove_slot`：从指定槽位删除指定数量。

## 入口

已打包 EXE：

```text
dist\RomesteadRealtimeInventory.exe
```

EXE 内置：

- `RomesteadLiveBridge.dll`
- `patch_romestead_bridge.ps1`
- `Mono.Cecil.dll`
- `items_catalog.json`

EXE 不携带官方 `Romestead.dll`。点击 `安装/修复桥接` 时，它会读取本机游戏目录中的当前官方 DLL 并现场打补丁。

EXE 里的 `还原桥接` 会优先使用游戏目录中的：

```text
Romestead.dll.bak
```

来覆盖回 `Romestead.dll`，并删除 `RomesteadLiveBridge.dll`。

源码/BAT 入口：

```text
打开实时背包修改器.bat
install_romestead_bridge_patch.bat
restore_romestead_original_dll.bat
```

## 游戏更新后

Steam 更新可能会覆盖 `Romestead.dll`，实时功能会失效。

更新后关闭游戏，再使用 EXE 里的 `安装/修复桥接`，或运行：

```text
install_romestead_bridge_patch.bat
```

## 测试

正常启动游戏并进入存档后，可以运行：

```text
5_测试补丁桥接.bat
6_测试补丁实时加木板.bat
7_测试实时读取背包.bat
8_实时删除指定槽位.bat
```

`8_实时删除指定槽位.bat` 会要求手动输入区域、槽位和数量后才会删除。

## 还原

关闭游戏后运行：

```text
restore_romestead_original_dll.bat
```

它会优先恢复游戏目录里的 `Romestead.dll.bak`，并删除游戏目录里的 `RomesteadLiveBridge.dll`。

## 已废弃路线

- `DOTNET_STARTUP_HOOKS`：会弹 `An error has occurred`，并且 Steam 可能启动另一个真正可玩的进程，不稳定。
- 普通 `LoadLibraryW` 注入：可以把托管 DLL 加载成模块，但不会执行 C# 托管代码。
- 开发者终端输入 `.item`：能加物品，但体验不好，只作为备用验证手段保留。
