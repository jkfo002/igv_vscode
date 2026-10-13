#!/usr/bin/env python3
"""Linux integration check with a real Xvnc/Openbox/Java IGV session."""

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import DEFAULT_PREFIX, binary, environment


def request(session, route):
    client = http.client.HTTPConnection("127.0.0.1", session["controlPort"], timeout=5)
    client.request("POST", route, headers={"Authorization": "Bearer " + session["token"]})
    response = client.getresponse()
    response.read()
    client.close()
    if response.status != 200:
        raise RuntimeError("Management API returned " + str(response.status))


def websocket_banner(session):
    """Check that token-gated WebSocket actually carries the VNC server banner."""
    with socket.create_connection(("127.0.0.1", session["websocketPort"]), timeout=5) as sock:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(("GET /?token=" + session["token"] + " HTTP/1.1\r\n"
                      "Host: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " + key + "\r\n\r\n").encode())
        headers = b""
        while not headers.endswith(b"\r\n\r\n"):
            byte = sock.recv(1)
            if not byte:
                raise RuntimeError("WebSocket handshake terminated")
            headers += byte
            if len(headers) > 16384:
                raise RuntimeError("Unexpected handshake size")
        if not headers.startswith(b"HTTP/1.1 101"):
            raise RuntimeError("WebSocket upgrade failed")
        data = b""
        while b"RFB 003." not in data and len(data) < 1024:
            chunk = sock.recv(128)
            if not chunk:
                break
            data += chunk
        if b"RFB 003." not in data:
            raise RuntimeError("WebSocket did not carry a VNC banner")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--igv-dir", type=Path, required=True)
    args = parser.parse_args()
    prefix, manifest, env = environment(args.prefix)
    command = [manifest["python"], str(Path(__file__).resolve().parents[1] / "session.py"),
               "start", "--prefix", str(prefix), "--igv-dir", str(args.igv_dir.resolve()),
               "--owner", secrets.token_hex(16), "--memory", "2g"]
    result = subprocess.run(command, check=True, text=True, capture_output=True, timeout=100)
    session = json.loads(result.stdout)
    try:
        env["DISPLAY"] = session["display"]
        env["XAUTHORITY"] = str(Path(session["directory"]) / "Xauthority")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            request(session, "/heartbeat")
            windows = subprocess.run([binary(prefix, "xprop"), "-root", "_NET_CLIENT_LIST"],
                                     env=env, capture_output=True, text=True, check=True).stdout
            ids = re.findall(r"0x[0-9a-fA-F]+", windows)
            titles = "\n".join(subprocess.run([binary(prefix, "xprop"), "-id", wid, "WM_NAME", "_NET_WM_NAME"],
                                             env=env, capture_output=True, text=True).stdout for wid in ids)
            if "IGV" in titles:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Java IGV did not create a managed X window; logs: " + session["directory"])
        websocket_banner(session)
        print("Real IGV X window, authenticated management API and WebSocket/VNC bridge: OK")
    finally:
        request(session, "/stop")
        state_path = Path(session["directory"]) / "session.json"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = json.loads(state_path.read_text())
            if state["state"] == "stopped":
                if "token" in state:
                    raise RuntimeError("Credentials were not invalidated")
                print("Owned remote processes stopped and session credentials invalidated: OK")
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Remote session did not stop")


if __name__ == "__main__":
    main()
