# Romestead Realtime Inventory

Romestead 实时背包修改器源码。

这个工具由两部分组成：

- `RomesteadRealtimeInventory.exe`：Tkinter GUI，负责读取背包、添加物品、删除槽位物品、安装/还原桥接。
- `RomesteadLiveBridge.dll`：被游戏加载的托管 C# 桥接 DLL，负责在游戏进程内执行实时命令。

## 功能

- 自动修补当前游戏目录中的 `Romestead.dll`
- 安装时备份为 `Romestead.dll.bak`
- 还原桥接
- 实时读取背包、装备、副装备
- 全物品库搜索
- 实时添加物品
- 实时删除指定槽位的指定数量

## 不包含的内容

这个仓库不包含：

- 官方游戏 DLL
- 官方游戏资源
- 已编译 EXE
- 已编译 `RomesteadLiveBridge.dll`
- 从游戏数据提取出的完整 `items_catalog.json`
- 第三方二进制依赖

`items_catalog.example.json` 只是空示例。完整物品库应由使用者自行生成或提供。

`Mono.Cecil.dll` 是用于修改 .NET 程序集的开源依赖。构建脚本会在缺失时从 NuGet 下载 Mono.Cecil 0.11.6 到本地 `tools/` 目录。

## 构建

需要 Windows 和 Python 3。

构建 EXE：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_realtime_exe.ps1
```

脚本会：

1. 缺少 Mono.Cecil 时从 NuGet 下载
2. 缺少 `items_catalog.json` 时生成空物品库
3. 编译 `RomesteadLiveBridge.cs` 为 `RomesteadLiveBridge.dll`
4. 检查并安装 PyInstaller
5. 打包 `dist\RomesteadRealtimeInventory.exe`

## 使用

1. 启动 `RomesteadRealtimeInventory.exe`
2. 如果游戏目录不正确，点击 `游戏目录...`
3. 关闭游戏
4. 点击 `安装/修复桥接`
5. 启动游戏并进入存档
6. 工具会自动检测桥接并读取当前背包

还原时关闭游戏，点击 `还原桥接`。

## 桥接协议

命名管道：

```text
\\.\pipe\RomesteadLiveBridge
```

消息格式：

1. 4 字节 little-endian payload 长度
2. UTF-8 payload

命令：

```text
ping
get_inventory
add_item<TAB>item_id<TAB>amount<TAB>aura_id
remove_slot<TAB>section<TAB>slot<TAB>amount<TAB>expected_item_instance_guid
```

响应：

```text
OK<TAB>message
ERR<TAB>message
```

`get_inventory` 的 `message` 是 JSON。

## 许可证

发布前请添加你选择的开源许可证，例如 MIT、Apache-2.0 或 GPL。
