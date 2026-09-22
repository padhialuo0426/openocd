#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise native OpenOCD RAM, registers, AP1 and software breakpoints.

Connect to an otherwise idle OpenOCD Tcl port. Borrows 64 bytes below the live
SRAM stack and restores them. Never writes Flash. Requires a new output directory
for the recovery snapshot. A failed check leaves the CPU halted.
"""

import argparse
import json
from pathlib import Path
import socket


class Tcl:
    def __init__(self, port):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=30)

    def call(self, command):
        request = "set rc [catch {" + command + '} result]; format "%d %s" $rc $result'
        self.socket.sendall(request.encode() + b"\x1a")
        reply = bytearray()
        while b"\x1a" not in reply:
            part = self.socket.recv(65536)
            if not part:
                raise RuntimeError("OpenOCD closed the Tcl connection")
            reply.extend(part)
        status, _, text = reply.split(b"\x1a", 1)[0].decode().partition(" ")
        if status != "0":
            raise RuntimeError(text)
        return text

    def read(self, target, address, width, count):
        reply = self.call(f"{target} read_memory {address:#x} {width} {count}")
        return b"".join(int(x, 0).to_bytes(width // 8, "little") for x in reply.split())

    def write(self, address, data):
        self.call(
            f"WS63.cpu write_memory {address:#x} 8 {{"
            + " ".join(str(x) for x in data)
            + "}"
        )

    def registers(self, names):
        items = self.call("WS63.cpu get_reg -force {" + " ".join(names) + "}").split()
        return {items[i]: int(items[i + 1], 0) for i in range(0, len(items), 2)}

    def put(self, values):
        self.call(
            "WS63.cpu set_reg {"
            + " ".join(f"{k} {v:#x}" for k, v in values.items())
            + "}"
        )


def check(client, output):
    names = ["zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2", "fp", "s1"]
    names += [f"a{i}" for i in range(8)] + [f"s{i}" for i in range(2, 12)]
    names += ["t3", "t4", "t5", "t6", "pc", "mstatus", "dcsr", "ft0"]
    if client.call("target current") != "WS63.cpu":
        raise RuntimeError("select WS63.cpu first")
    if client.call("bp").strip() or client.call("wp").strip():
        raise RuntimeError("remove existing breakpoints/watchpoints first")
    running = client.call("WS63.cpu curstate") == "running"
    client.call("halt")
    before = client.registers(names)
    address = (before["sp"] - 128) & ~3
    if not 0xA00000 <= address < address + 64 <= 0xA88000:
        raise RuntimeError("requires an SRAM task stack")
    old = client.read("WS63.cpu", address, 8, 64)
    (output / "recovery.json").write_text(
        json.dumps(
            {
                "registers": before,
                "address": address,
                "ram": old.hex(),
                "running": running,
            },
            indent=2,
        )
    )
    success = False
    try:
        expected = client.read("WS63.ap1", 0x1003F8, 8, 80)
        for width in (8, 16, 32):
            assert client.read("WS63.ap1", 0x1003F8, width, 640 // width) == expected
        client.write(address + 1, b"\x01\x23\x45\x67\x89")
        assert (
            client.read("WS63.cpu", address, 8, 64)
            == old[:1] + b"\x01\x23\x45\x67\x89" + old[6:]
        )
        client.write(address, old)
        assert client.read("WS63.ap1", address, 8, 64) == old
        client.put({"mstatus": before["mstatus"] | 0x2000, "ft0": 0x3F800000})
        assert client.registers(["ft0"])["ft0"] == 0x3F800000
        client.put({"ft0": before["ft0"], "mstatus": before["mstatus"]})
        assert client.registers(names) == before
        print("AP1 TAR boundary / RAM coherency / FPR / CSR / GPR preservation: PASS")
        code = bytes.fromhex("1305b0076f000000") + bytes(8)
        client.write(address, code)
        client.put({"mstatus": before["mstatus"] & ~8, "pc": address})
        client.call(f"bp {address:#x} 4")
        assert client.read("WS63.ap1", address, 8, 4) == bytes.fromhex("73001000")
        client.call("resume")
        client.call("wait_halt 2000")
        hit = client.registers(["pc", "dcsr"])
        assert hit["pc"] == address and (hit["dcsr"] >> 6) & 7 == 1
        client.call("step")
        hit = client.registers(["pc", "a0", "dcsr"])
        assert (
            hit["pc"] == address + 4
            and hit["a0"] == 123
            and (hit["dcsr"] >> 6) & 7 == 4
        )
        client.call(f"rbp {address:#x}")
        assert client.read("WS63.cpu", address, 8, 16) == code
        print(
            "RAM software breakpoint / displaced step / instruction restoration: PASS"
        )
        success = True
    finally:
        client.call("halt")
        client.call(f"catch {{rbp {address:#x}}}")
        client.write(address, old)
        client.put(before)
        client.call("ws63_sync_code")
        assert client.read("WS63.cpu", address, 8, 64) == old
        actual = client.registers(names)
        # dcsr.cause is read-only: the test necessarily changes the last halt cause.
        actual["dcsr"] &= ~0x1C0
        expected = dict(before)
        expected["dcsr"] &= ~0x1C0
        assert actual == expected, (actual, expected)
        if success and running:
            client.call("resume")
        print(
            "RAM and CPU state restored; CPU "
            + ("running" if success and running else "halted")
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=6666)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    client = Tcl(args.port)
    try:
        check(client, args.output)
    finally:
        client.socket.close()


if __name__ == "__main__":
    main()
