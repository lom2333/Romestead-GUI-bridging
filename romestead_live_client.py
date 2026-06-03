#!/usr/bin/env python
"""Client for the Romestead live command bridge."""

from __future__ import annotations

import argparse
import ctypes
import json
import time
import struct
from ctypes import wintypes


PIPE_NAME = "RomesteadLiveBridge"
PIPE_PATH = r"\\.\pipe\RomesteadLiveBridge"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
ERROR_FILE_NOT_FOUND = 2
ERROR_PIPE_BUSY = 231
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel32.WaitNamedPipeW.restype = wintypes.BOOL


def send_command(parts: list[str], timeout: float = 10.0) -> tuple[str, str]:
    payload = "\t".join(parts)
    handle = _connect_pipe(timeout)

    try:
        payload_raw = payload.encode("utf-8")
        _write_all(handle, struct.pack("<I", len(payload_raw)))
        _write_all(handle, payload_raw)

        response_length = struct.unpack("<I", _read_exact(handle, 4))[0]
        response_bytes = _read_exact(handle, response_length)
        text = response_bytes.decode("utf-8", "replace").strip()
    finally:
        kernel32.CloseHandle(handle)

    if "\t" in text:
        status, message = text.split("\t", 1)
    else:
        status, message = text, ""
    return status, message


def get_live_inventory(timeout: float = 5.0) -> dict:
    status, message = send_command(["get_inventory"], timeout=timeout)
    if status != "OK":
        raise RuntimeError(message or "live inventory request failed")
    return json.loads(message)


def remove_live_slot(
    section: str,
    slot: int,
    amount: int,
    expected_item_instance_id: str = "",
    timeout: float = 5.0,
) -> tuple[str, str]:
    return send_command(
        ["remove_slot", section, str(slot), str(amount), expected_item_instance_id],
        timeout=timeout,
    )


def _connect_pipe(timeout: float):
    deadline = time.monotonic() + timeout
    last_error = ERROR_FILE_NOT_FOUND
    while time.monotonic() < deadline:
        handle = kernel32.CreateFileW(
            PIPE_PATH,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle != INVALID_HANDLE_VALUE:
            return handle

        last_error = ctypes.get_last_error()
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        if last_error == ERROR_PIPE_BUSY:
            kernel32.WaitNamedPipeW(PIPE_PATH, min(remaining_ms, 250))
        elif last_error == ERROR_FILE_NOT_FOUND:
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))
        else:
            raise ctypes.WinError(last_error)

    raise RuntimeError(
        "live bridge is not reachable; install the bridge patch, start the game, and enter a world"
        f" (last error {last_error})"
    )


def _read_exact(handle, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk_len = length - len(data)
        buffer = ctypes.create_string_buffer(chunk_len)
        read = wintypes.DWORD(0)
        ok = kernel32.ReadFile(handle, buffer, chunk_len, ctypes.byref(read), None)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        if read.value <= 0:
            raise RuntimeError("pipe closed while reading")
        data.extend(buffer.raw[: read.value])
    return bytes(data)


def _write_all(handle, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        chunk = data[offset:]
        written = wintypes.DWORD(0)
        buffer = ctypes.create_string_buffer(chunk)
        ok = kernel32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value <= 0:
            raise RuntimeError("pipe closed while writing")
        offset += written.value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send commands to RomesteadLiveBridge.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="Check whether the live bridge is loaded.")
    sub.add_parser("get-inventory", help="Read the current in-game inventory through the bridge.")

    add = sub.add_parser("add-item", help="Add an item through the game's cheat message.")
    add.add_argument("item_id", help="Real game item ID, for example material:wood.")
    add.add_argument("count", type=int, help="Amount to add.")
    add.add_argument("--aura-id", default="", help="Optional item aura ID.")

    remove = sub.add_parser("remove-slot", help="Remove item amount from a live inventory slot.")
    remove.add_argument("section", choices=["inventory", "equipment", "secondary"])
    remove.add_argument("slot", type=int)
    remove.add_argument("amount", type=int)
    remove.add_argument("--expected-id", default="", help="Optional expected item instance GUID.")

    args = parser.parse_args(argv)

    if args.command == "ping":
        status, message = send_command(["ping"])
    elif args.command == "get-inventory":
        try:
            data = get_live_inventory()
        except Exception as exc:
            print(f"ERR: {exc}")
            return 2
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    elif args.command == "add-item":
        status, message = send_command(["add_item", args.item_id, str(args.count), args.aura_id])
    elif args.command == "remove-slot":
        status, message = remove_live_slot(args.section, args.slot, args.amount, args.expected_id)
    else:
        parser.error("unknown command")

    print(f"{status}: {message}")
    return 0 if status == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
