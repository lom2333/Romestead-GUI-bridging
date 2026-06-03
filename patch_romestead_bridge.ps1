param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\romestead",
    [string]$InputDll = "",
    [string]$OutputDll = "",
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$gameDir = $GameDir
if ([string]::IsNullOrWhiteSpace($InputDll)) {
    $input = Join-Path $gameDir "Romestead.dll"
} else {
    $input = $InputDll
}
$outputDir = Join-Path $root "patched"
if ([string]::IsNullOrWhiteSpace($OutputDll)) {
    $output = Join-Path $outputDir "Romestead.dll"
} else {
    $output = $OutputDll
}
$cecil = Join-Path $root "tools\Mono.Cecil.0.11.6\lib\net40\Mono.Cecil.dll"
$bridgeSource = Join-Path $root "RomesteadLiveBridge.dll"
$bridgePath = Join-Path $gameDir "RomesteadLiveBridge.dll"

if (-not (Test-Path $cecil)) {
    throw "Missing Mono.Cecil.dll. Expected: $cecil"
}
if (-not (Test-Path $input)) {
    throw "Missing game DLL: $input"
}
if (-not (Test-Path $bridgeSource)) {
    throw "Missing bridge DLL: $bridgeSource. Run build_live_bridge.ps1 first."
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$source = @"
using System;
using System.IO;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

public static class RomesteadBridgePatcher
{
    public static bool Patch(string input, string output, string gameDir, string bridgePath)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(gameDir);
        resolver.AddSearchDirectory(Path.GetDirectoryName(input));

        var reader = new ReaderParameters
        {
            AssemblyResolver = resolver,
            ReadWrite = false,
            InMemory = true
        };

        var assembly = AssemblyDefinition.ReadAssembly(input, reader);
        var module = assembly.MainModule;
        var type = module.GetType("Candide.GameModels.Systems.DebugSystem");
        if (type == null)
        {
            throw new Exception("Could not find Candide.GameModels.Systems.DebugSystem");
        }

        var method = type.Methods.FirstOrDefault(m => m.Name == "Update" && m.Parameters.Count == 0);
        if (method == null || !method.HasBody)
        {
            throw new Exception("Could not find DebugSystem.Update()");
        }

        var bootstrap = module.GetType("RomesteadBridgeBootstrap");
        MethodDefinition initializeMethod = null;
        if (bootstrap == null)
        {
            bootstrap = new TypeDefinition(
                "",
                "RomesteadBridgeBootstrap",
                TypeAttributes.NotPublic | TypeAttributes.Abstract | TypeAttributes.Sealed | TypeAttributes.BeforeFieldInit,
                module.TypeSystem.Object);
            module.Types.Add(bootstrap);

            var initializedField = new FieldDefinition(
                "Initialized",
                FieldAttributes.Private | FieldAttributes.Static,
                module.TypeSystem.Boolean);
            bootstrap.Fields.Add(initializedField);

            initializeMethod = new MethodDefinition(
                "Initialize",
                MethodAttributes.Public | MethodAttributes.Static,
                module.TypeSystem.Void);
            bootstrap.Methods.Add(initializeMethod);
            BuildBootstrapMethod(module, initializeMethod, initializedField, bridgePath);
        }
        else
        {
            initializeMethod = bootstrap.Methods.FirstOrDefault(m => m.Name == "Initialize" && m.Parameters.Count == 0);
            if (initializeMethod == null)
            {
                throw new Exception("RomesteadBridgeBootstrap exists but Initialize() was not found");
            }
        }

        bool alreadyPatched = method.Body.Instructions.Any(i => {
            var mr = i.Operand as MethodReference;
            return i.OpCode == OpCodes.Call &&
                mr != null &&
                mr.Name == "Initialize" &&
                mr.DeclaringType.FullName == "RomesteadBridgeBootstrap";
        });

        if (!alreadyPatched)
        {
            var il = method.Body.GetILProcessor();
            var first = method.Body.Instructions[0];
            il.InsertBefore(first, il.Create(OpCodes.Call, initializeMethod));
        }

        var writer = new WriterParameters { WriteSymbols = false };
        assembly.Write(output, writer);
        return alreadyPatched;
    }

    private static void BuildBootstrapMethod(ModuleDefinition module, MethodDefinition method, FieldDefinition initializedField, string bridgePath)
    {
        var body = method.Body;
        body.InitLocals = true;

        var assemblyType = module.ImportReference(typeof(System.Reflection.Assembly));
        var typeType = module.ImportReference(typeof(System.Type));
        var methodInfoType = module.ImportReference(typeof(System.Reflection.MethodInfo));
        var exceptionType = module.ImportReference(typeof(System.Exception));

        body.Variables.Add(new VariableDefinition(assemblyType));
        body.Variables.Add(new VariableDefinition(typeType));
        body.Variables.Add(new VariableDefinition(methodInfoType));

        var assemblyLoadFrom = module.ImportReference(typeof(System.Reflection.Assembly).GetMethod("LoadFrom", new Type[] { typeof(string) }));
        var assemblyGetType = module.ImportReference(typeof(System.Reflection.Assembly).GetMethod("GetType", new Type[] { typeof(string) }));
        var typeGetMethod = module.ImportReference(typeof(System.Type).GetMethod("GetMethod", new Type[] { typeof(string), typeof(System.Reflection.BindingFlags) }));
        var methodInvoke = module.ImportReference(typeof(System.Reflection.MethodBase).GetMethod("Invoke", new Type[] { typeof(object), typeof(object[]) }));

        var il = body.GetILProcessor();
        var ret = Instruction.Create(OpCodes.Ret);
        var tryStart = Instruction.Create(OpCodes.Ldstr, bridgePath);
        var catchStart = Instruction.Create(OpCodes.Pop);

        il.Append(Instruction.Create(OpCodes.Ldsfld, initializedField));
        il.Append(Instruction.Create(OpCodes.Brfalse_S, tryStart));
        il.Append(Instruction.Create(OpCodes.Ret));

        il.Append(tryStart);
        il.Append(Instruction.Create(OpCodes.Call, assemblyLoadFrom));
        il.Append(Instruction.Create(OpCodes.Stloc_0));
        il.Append(Instruction.Create(OpCodes.Ldloc_0));
        il.Append(Instruction.Create(OpCodes.Ldstr, "StartupHook"));
        il.Append(Instruction.Create(OpCodes.Callvirt, assemblyGetType));
        il.Append(Instruction.Create(OpCodes.Stloc_1));
        il.Append(Instruction.Create(OpCodes.Ldloc_1));
        il.Append(Instruction.Create(OpCodes.Ldstr, "Initialize"));
        il.Append(Instruction.Create(OpCodes.Ldc_I4_S, (sbyte)24));
        il.Append(Instruction.Create(OpCodes.Callvirt, typeGetMethod));
        il.Append(Instruction.Create(OpCodes.Stloc_2));
        il.Append(Instruction.Create(OpCodes.Ldloc_2));
        il.Append(Instruction.Create(OpCodes.Ldnull));
        il.Append(Instruction.Create(OpCodes.Ldnull));
        il.Append(Instruction.Create(OpCodes.Callvirt, methodInvoke));
        il.Append(Instruction.Create(OpCodes.Pop));
        il.Append(Instruction.Create(OpCodes.Ldc_I4_1));
        il.Append(Instruction.Create(OpCodes.Stsfld, initializedField));
        il.Append(Instruction.Create(OpCodes.Leave_S, ret));

        il.Append(catchStart);
        il.Append(Instruction.Create(OpCodes.Leave_S, ret));
        il.Append(ret);

        body.ExceptionHandlers.Add(new ExceptionHandler(ExceptionHandlerType.Catch)
        {
            CatchType = exceptionType,
            TryStart = tryStart,
            TryEnd = catchStart,
            HandlerStart = catchStart,
            HandlerEnd = ret
        });
    }
}
"@

Add-Type -Path $cecil
Add-Type -TypeDefinition $source -ReferencedAssemblies $cecil
$alreadyPatched = [RomesteadBridgePatcher]::Patch($input, $output, $gameDir, $bridgePath)

Write-Host "Patched DLL written to: $output"
Write-Host "Patch input: $input"
Write-Host "Bridge path embedded: $bridgePath"
Write-Host "Input already had bridge patch: $alreadyPatched"
Get-FileHash -Algorithm SHA256 -LiteralPath $output | Format-List

if ($Install) {
    $running = Get-Process -Name "Romestead" -ErrorAction SilentlyContinue
    if ($running) {
        throw "Romestead.exe is still running. Close the game before installing the patch."
    }

    $gameDll = Join-Path $gameDir "Romestead.dll"
    if (-not (Test-Path $gameDll)) {
        throw "Missing game DLL: $gameDll"
    }

    if (-not $alreadyPatched) {
        $backupDll = Join-Path $gameDir "Romestead.dll.bak"
        if (Test-Path -LiteralPath $backupDll) {
            Write-Host "Backup already exists; keeping existing file: $backupDll"
        } else {
            Copy-Item -LiteralPath $gameDll -Destination $backupDll -Force
            Write-Host "Backup before patch: $backupDll"
        }
    } else {
        Write-Host "Game DLL already contains the bridge patch; skipping pre-patch backup."
    }

    Copy-Item -LiteralPath $output -Destination $gameDll -Force
    Copy-Item -LiteralPath $bridgeSource -Destination $bridgePath -Force
    Write-Host "Installed patched Romestead.dll and RomesteadLiveBridge.dll."
}
