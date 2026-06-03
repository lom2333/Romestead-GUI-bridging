using System;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Diagnostics;
using System.Reflection;
using System.Text;
using System.Threading;

public class StartupHook
{
    private const string PipeName = "RomesteadLiveBridge";
    private static bool _started;

    public static void Initialize()
    {
        if (_started)
        {
            return;
        }

        _started = true;
        Thread thread = new Thread(ServerLoop);
        thread.IsBackground = true;
        thread.Name = "RomesteadLiveBridge";
        thread.Start();
    }

    private static void ServerLoop()
    {
        // Keep the startup hook as quiet as possible while the game initializes
        // graphics and its own exception UI.
        Thread.Sleep(8000);
        Log("startup hook initialized in pid " + Process.GetCurrentProcess().Id.ToString());

        while (true)
        {
            try
            {
                using (NamedPipeServerStream pipe = new NamedPipeServerStream(
                    PipeName,
                    PipeDirection.InOut,
                    1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.None))
                {
                    Log("waiting for pipe connection");
                    pipe.WaitForConnection();
                    Log("pipe client connected");

                    string line = ReadFrame(pipe);
                    Log("received: " + (line ?? "<null>"));
                    string response = Handle(line);
                    Log("response: " + response);
                    WriteFrame(pipe, response);
                }
            }
            catch (Exception ex)
            {
                Log("server loop error: " + Flatten(ex));
                Thread.Sleep(250);
            }
        }
    }

    private static string ReadFrame(Stream stream)
    {
        byte[] lengthBytes = ReadExact(stream, 4);
        int length = BitConverter.ToInt32(lengthBytes, 0);
        if (length <= 0 || length > 65536)
        {
            throw new InvalidDataException("invalid frame length: " + length.ToString());
        }

        byte[] payload = ReadExact(stream, length);
        return Encoding.UTF8.GetString(payload);
    }

    private static void WriteFrame(Stream stream, string text)
    {
        byte[] payload = Encoding.UTF8.GetBytes(text ?? "");
        byte[] lengthBytes = BitConverter.GetBytes(payload.Length);
        stream.Write(lengthBytes, 0, lengthBytes.Length);
        stream.Write(payload, 0, payload.Length);
        stream.Flush();
    }

    private static byte[] ReadExact(Stream stream, int length)
    {
        byte[] data = new byte[length];
        int offset = 0;
        while (offset < length)
        {
            int read = stream.Read(data, offset, length - offset);
            if (read <= 0)
            {
                throw new EndOfStreamException("pipe closed while reading");
            }

            offset += read;
        }

        return data;
    }

    private static string Handle(string line)
    {
        try
        {
            if (String.IsNullOrWhiteSpace(line))
            {
                return Error("empty command");
            }

            string[] parts = line.Split('\t');
            string command = parts[0].Trim().ToLowerInvariant();

            if (command == "ping")
            {
                return Ok("bridge_loaded");
            }

            if (command == "get_inventory")
            {
                return QueueOnClientThread(delegate
                {
                    return GetInventoryNow();
                });
            }

            if (command == "add_item")
            {
                if (parts.Length < 3)
                {
                    return Error("usage: add_item<TAB>item_id<TAB>count<TAB>aura_id");
                }

                string itemId = parts[1].Trim();
                int count;
                if (!Int32.TryParse(parts[2], out count) || count <= 0)
                {
                    return Error("count must be a positive integer");
                }

                string auraId = parts.Length >= 4 ? NullIfEmpty(parts[3]) : null;
                return QueueOnClientThread(delegate
                {
                    return AddItemNow(itemId, count, auraId);
                });
            }

            if (command == "remove_slot")
            {
                if (parts.Length < 4)
                {
                    return Error("usage: remove_slot<TAB>section<TAB>slot<TAB>amount<TAB>expected_item_instance_id");
                }

                string section = parts[1].Trim();
                int slot;
                if (!Int32.TryParse(parts[2], out slot) || slot < 0)
                {
                    return Error("slot must be a non-negative integer");
                }

                int amount;
                if (!Int32.TryParse(parts[3], out amount) || amount <= 0)
                {
                    return Error("amount must be a positive integer");
                }

                string expectedItemInstanceId = parts.Length >= 5 ? NullIfEmpty(parts[4]) : null;
                return QueueOnClientThread(delegate
                {
                    return RemoveSlotNow(section, slot, amount, expectedItemInstanceId);
                });
            }

            return Error("unknown command: " + command);
        }
        catch (Exception ex)
        {
            return Error(Flatten(ex));
        }
    }

    private static string QueueOnClientThread(Func<string> work)
    {
        Type debugSystem = FindType("Candide.GameModels.Systems.DebugSystem");
        if (debugSystem == null)
        {
            return Error("client debug queue is not loaded yet");
        }

        MethodInfo queue = debugSystem.GetMethod("Queue", BindingFlags.Public | BindingFlags.Static);
        if (queue == null)
        {
            return Error("client debug queue method was not found");
        }

        ManualResetEvent done = new ManualResetEvent(false);
        string result = null;
        Exception error = null;

        Action action = delegate
        {
            try
            {
                result = work();
            }
            catch (Exception ex)
            {
                error = ex;
            }
            finally
            {
                done.Set();
            }
        };

        queue.Invoke(null, new object[] { action });

        if (!done.WaitOne(5000))
        {
            return Error("queued command timed out; enter a loaded game world and try again");
        }

        if (error != null)
        {
            return Error(Flatten(error));
        }

        return result ?? Ok("queued");
    }

    private static string GetInventoryNow()
    {
        string connectError;
        if (!IsConnectedToGame(out connectError))
        {
            return Error(connectError);
        }

        Type gameState = FindType("Candide.GameModels.GameState");
        if (gameState == null)
        {
            return Error("game state is not loaded yet");
        }

        StringBuilder sb = new StringBuilder(32768);
        sb.Append("{\"sections\":[");
        AppendInventoryFromMethod(sb, gameState, "inventory", "TryGetLocalPlayerInventory", true);
        AppendInventoryFromMethod(sb, gameState, "equipment", "TryGetLocalPlayerEquipmentInventory", false);
        AppendInventoryFromMethod(sb, gameState, "secondary", "TryGetLocalPlayerSecondaryEquipmentInventory", false);
        sb.Append("]}");
        return Ok(sb.ToString());
    }

    private static bool TryGetInventoryForSection(string section, out object simpleInventory, out string error)
    {
        simpleInventory = null;
        error = null;

        Type gameState = FindType("Candide.GameModels.GameState");
        if (gameState == null)
        {
            error = "game state is not loaded yet";
            return false;
        }

        string methodName;
        string normalized = (section ?? "").Trim().ToLowerInvariant();
        if (normalized == "inventory")
        {
            methodName = "TryGetLocalPlayerInventory";
        }
        else if (normalized == "equipment")
        {
            methodName = "TryGetLocalPlayerEquipmentInventory";
        }
        else if (normalized == "secondary")
        {
            methodName = "TryGetLocalPlayerSecondaryEquipmentInventory";
        }
        else
        {
            error = "unknown inventory section: " + section;
            return false;
        }

        MethodInfo method = gameState.GetMethod(methodName, BindingFlags.Public | BindingFlags.Static);
        if (method == null)
        {
            error = "method not found: " + methodName;
            return false;
        }

        object[] args = new object[] { null };
        bool ok = (bool)method.Invoke(null, args);
        if (!ok || args[0] == null)
        {
            error = "inventory is not available: " + normalized;
            return false;
        }

        simpleInventory = args[0];
        return true;
    }

    private static void AppendInventoryFromMethod(StringBuilder sb, Type gameState, string key, string methodName, bool first)
    {
        if (!first)
        {
            sb.Append(",");
        }

        MethodInfo method = gameState.GetMethod(methodName, BindingFlags.Public | BindingFlags.Static);
        if (method == null)
        {
            AppendMissingInventory(sb, key, "method not found: " + methodName);
            return;
        }

        object[] args = new object[] { null };
        bool ok = false;
        try
        {
            ok = (bool)method.Invoke(null, args);
        }
        catch (Exception ex)
        {
            AppendMissingInventory(sb, key, Flatten(ex));
            return;
        }

        if (!ok || args[0] == null)
        {
            AppendMissingInventory(sb, key, "inventory is not available");
            return;
        }

        AppendSimpleInventory(sb, key, args[0]);
    }

    private static void AppendMissingInventory(StringBuilder sb, string key, string error)
    {
        sb.Append("{\"key\":");
        AppendJsonString(sb, key);
        sb.Append(",\"available\":false,\"error\":");
        AppendJsonString(sb, error);
        sb.Append(",\"slots\":[]}");
    }

    private static void AppendSimpleInventory(StringBuilder sb, string key, object simpleInventory)
    {
        object model = GetMemberValue(simpleInventory, "Model");
        if (model == null)
        {
            AppendMissingInventory(sb, key, "inventory model is null");
            return;
        }

        object slotsObject = GetMemberValue(model, "InventorySlots");
        Array slots = slotsObject as Array;

        sb.Append("{\"key\":");
        AppendJsonString(sb, key);
        sb.Append(",\"available\":true");
        sb.Append(",\"inventory_id\":");
        AppendJsonString(sb, ToText(GetMemberValue(model, "Id")));
        sb.Append(",\"name\":");
        AppendJsonString(sb, ToText(GetMemberValue(model, "Name")));
        sb.Append(",\"inventory_type\":");
        AppendJsonString(sb, ToText(GetMemberValue(model, "InventoryType")));
        sb.Append(",\"money\":");
        AppendJsonNumberOrNull(sb, GetMemberValue(model, "Money"));
        sb.Append(",\"slot_count\":");
        sb.Append(slots == null ? 0 : slots.Length);
        sb.Append(",\"slots\":[");

        if (slots != null)
        {
            for (int i = 0; i < slots.Length; i++)
            {
                if (i > 0)
                {
                    sb.Append(",");
                }

                object item = slots.GetValue(i);
                if (item == null)
                {
                    sb.Append("null");
                }
                else
                {
                    AppendItem(sb, i, item);
                }
            }
        }

        sb.Append("]}");
    }

    private static void AppendItem(StringBuilder sb, int slot, object item)
    {
        object usable = GetMemberValue(item, "UsableInfo");
        object auras = GetMemberValue(item, "Auras");

        sb.Append("{\"slot\":");
        sb.Append(slot);
        sb.Append(",\"base_data_id\":");
        AppendJsonString(sb, ToText(GetMemberValue(item, "BaseDataId")));
        sb.Append(",\"id\":");
        AppendJsonString(sb, ToText(GetMemberValue(item, "Id")));
        sb.Append(",\"inventory_id\":");
        AppendJsonString(sb, ToText(GetMemberValue(item, "InventoryId")));
        sb.Append(",\"stack_count\":");
        AppendJsonNumberOrNull(sb, GetMemberValue(item, "StackCount"));
        sb.Append(",\"auras_count\":");
        AppendJsonNumberOrNull(sb, auras == null ? null : GetMemberValue(auras, "Count"));
        sb.Append(",\"uses_left\":");
        AppendJsonNumberOrNull(sb, usable == null ? null : GetMemberValue(usable, "UsesLeft"));
        sb.Append(",\"cooldown_remaining\":");
        AppendJsonNumberOrNull(sb, usable == null ? null : GetMemberValue(usable, "CooldownRemaining"));
        sb.Append("}");
    }

    private static string AddItemNow(string itemId, int count, string auraId)
    {
        string connectError;
        if (!IsConnectedToGame(out connectError))
        {
            return Error(connectError);
        }

        Type gameState = FindType("Candide.GameModels.GameState");
        FieldInfo configField = gameState == null ? null : gameState.GetField("Config", BindingFlags.Public | BindingFlags.Static);
        object config = configField == null ? null : configField.GetValue(null);
        FieldInfo cheatsField = config == null ? null : config.GetType().GetField("CheatsEnabled", BindingFlags.Public | BindingFlags.Instance);
        if (cheatsField == null || !(bool)cheatsField.GetValue(config))
        {
            return Error("cheats are not enabled in this world");
        }

        Type itemDb = FindType("Shared.Data.ItemDataBase");
        MethodInfo getItem = itemDb == null ? null : itemDb.GetMethod("GetItemDataOrNull", BindingFlags.Public | BindingFlags.Static);
        object itemData = getItem == null ? null : getItem.Invoke(null, new object[] { itemId });
        if (itemData == null)
        {
            return Error("item id not found: " + itemId);
        }

        Type service = FindType("Candide.GameModels.Services.SimpleInventoryService");
        MethodInfo addItem = service == null ? null : service.GetMethod("SendAddItemCheat", BindingFlags.Public | BindingFlags.Static);
        if (addItem == null)
        {
            return Error("SendAddItemCheat was not found");
        }

        addItem.Invoke(null, new object[] { itemId, count, auraId });
        return Ok("sent add_item " + itemId + " x" + count.ToString());
    }

    private static string RemoveSlotNow(string section, int slot, int amount, string expectedItemInstanceId)
    {
        string connectError;
        if (!IsConnectedToGame(out connectError))
        {
            return Error(connectError);
        }

        object simpleInventory;
        string inventoryError;
        if (!TryGetInventoryForSection(section, out simpleInventory, out inventoryError))
        {
            return Error(inventoryError);
        }

        object model = GetMemberValue(simpleInventory, "Model");
        object slotsObject = GetMemberValue(model, "InventorySlots");
        Array slots = slotsObject as Array;
        if (slots == null)
        {
            return Error("inventory slots are not available");
        }

        if (slot < 0 || slot >= slots.Length)
        {
            return Error("slot out of range");
        }

        object item = slots.GetValue(slot);
        if (item == null)
        {
            return Error("slot is already empty");
        }

        string itemInstanceId = ToText(GetMemberValue(item, "Id"));
        if (!String.IsNullOrWhiteSpace(expectedItemInstanceId) &&
            !String.Equals(itemInstanceId, expectedItemInstanceId, StringComparison.OrdinalIgnoreCase))
        {
            return Error("slot item changed; refresh inventory and try again");
        }

        object stackObject = GetMemberValue(item, "StackCount");
        int stack = stackObject == null ? 0 : Convert.ToInt32(stackObject, CultureInfo.InvariantCulture);
        if (amount > stack)
        {
            return Error("amount is larger than current stack");
        }

        Type messageType = FindType("CandideServer.MessageModels.Inventories.ExpendItemInstanceMessage");
        if (messageType == null)
        {
            return Error("ExpendItemInstanceMessage was not found");
        }

        object msg = Activator.CreateInstance(messageType);
        SetMemberValue(msg, "ItemInstanceId", new Guid(itemInstanceId));
        SetMemberValue(msg, "Amount", amount);

        Type service = FindType("Candide.GameModels.Services.ItemInstanceService");
        MethodInfo sendExpend = service == null ? null : service.GetMethod("SendExpend", BindingFlags.Public | BindingFlags.Static);
        if (sendExpend == null)
        {
            return Error("ItemInstanceService.SendExpend was not found");
        }

        sendExpend.Invoke(null, new object[] { msg });
        return Ok("sent remove_slot " + section + "[" + slot.ToString() + "] x" + amount.ToString());
    }

    private static bool IsConnectedToGame(out string error)
    {
        Type connectService = FindType("Candide.Multiplayer.Services.ConnectService");
        if (connectService == null)
        {
            error = "connect service is not loaded yet";
            return false;
        }

        FieldInfo stateField = connectService.GetField("State", BindingFlags.Public | BindingFlags.Static);
        object state = stateField == null ? null : stateField.GetValue(null);
        if (state == null)
        {
            error = "not connected to a game server";
            return false;
        }

        PropertyInfo stepProperty = state.GetType().GetProperty("Step", BindingFlags.Public | BindingFlags.Instance);
        object step = stepProperty == null ? null : stepProperty.GetValue(state, null);
        if (step == null || Convert.ToInt32(step) != 7)
        {
            error = "not connected to a game server";
            return false;
        }

        error = null;
        return true;
    }

    private static Type FindType(string fullName)
    {
        Assembly[] assemblies = AppDomain.CurrentDomain.GetAssemblies();
        for (int i = 0; i < assemblies.Length; i++)
        {
            Type t = assemblies[i].GetType(fullName, false);
            if (t != null)
            {
                return t;
            }
        }

        return null;
    }

    private static void SetMemberValue(object obj, string name, object value)
    {
        if (obj == null)
        {
            return;
        }

        Type type = obj.GetType();
        FieldInfo field = type.GetField(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static);
        if (field != null)
        {
            field.SetValue(obj, value);
            return;
        }

        PropertyInfo property = type.GetProperty(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static);
        if (property != null && property.CanWrite && property.GetIndexParameters().Length == 0)
        {
            property.SetValue(obj, value, null);
        }
    }

    private static object GetMemberValue(object obj, string name)
    {
        if (obj == null)
        {
            return null;
        }

        Type type = obj.GetType();
        FieldInfo field = type.GetField(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static);
        if (field != null)
        {
            return field.GetValue(obj);
        }

        PropertyInfo property = type.GetProperty(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static);
        if (property != null && property.GetIndexParameters().Length == 0)
        {
            return property.GetValue(obj, null);
        }

        return null;
    }

    private static string ToText(object value)
    {
        return value == null ? "" : Convert.ToString(value, CultureInfo.InvariantCulture);
    }

    private static void AppendJsonNumberOrNull(StringBuilder sb, object value)
    {
        if (value == null)
        {
            sb.Append("null");
            return;
        }

        IFormattable formattable = value as IFormattable;
        if (formattable != null)
        {
            sb.Append(formattable.ToString(null, CultureInfo.InvariantCulture));
            return;
        }

        long parsed;
        if (Int64.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed))
        {
            sb.Append(parsed.ToString(CultureInfo.InvariantCulture));
            return;
        }

        sb.Append("null");
    }

    private static void AppendJsonString(StringBuilder sb, string value)
    {
        sb.Append('"');
        if (value != null)
        {
            for (int i = 0; i < value.Length; i++)
            {
                char c = value[i];
                switch (c)
                {
                    case '\\':
                        sb.Append("\\\\");
                        break;
                    case '"':
                        sb.Append("\\\"");
                        break;
                    case '\b':
                        sb.Append("\\b");
                        break;
                    case '\f':
                        sb.Append("\\f");
                        break;
                    case '\n':
                        sb.Append("\\n");
                        break;
                    case '\r':
                        sb.Append("\\r");
                        break;
                    case '\t':
                        sb.Append("\\t");
                        break;
                    default:
                        if (c < ' ')
                        {
                            sb.Append("\\u");
                            sb.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            sb.Append(c);
                        }
                        break;
                }
            }
        }
        sb.Append('"');
    }

    private static string NullIfEmpty(string value)
    {
        if (String.IsNullOrWhiteSpace(value))
        {
            return null;
        }

        return value.Trim();
    }

    private static string Ok(string message)
    {
        return "OK\t" + message;
    }

    private static string Error(string message)
    {
        return "ERR\t" + message;
    }

    private static string Flatten(Exception ex)
    {
        Exception current = ex;
        while (current is TargetInvocationException && current.InnerException != null)
        {
            current = current.InnerException;
        }

        return current.GetType().Name + ": " + current.Message;
    }

    private static void Log(string text)
    {
        try
        {
            string path = Path.Combine(Path.GetTempPath(), "romestead_live_bridge.log");
            File.AppendAllText(path, DateTime.Now.ToString("s") + " " + text + Environment.NewLine);
        }
        catch
        {
        }
    }
}
