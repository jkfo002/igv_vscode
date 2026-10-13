#!/usr/bin/env python3
"""Own an isolated Java IGV desktop, with a ten-minute reconnect lease."""

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time

from runtime import DEFAULT_PREFIX, binary, environment, native_environment

LEASE_SECONDS = 600
OWNER_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def private_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
    temporary.replace(path)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, process, timeout=20, cancel=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Remote startup cancelled")
        if process.poll() is not None:
            raise RuntimeError("Remote component exited; inspect its session log")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Remote component did not become ready within " + str(timeout) + " seconds")


def stop_process(process, grace=5):
    # Every child is created in its own process group. Never discover/kill by name.
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except ProcessLookupError:
        pass


class Lease:
    def __init__(self, seconds=LEASE_SECONDS, clock=time.monotonic):
        self.seconds = seconds
        self.clock = clock
        self.last_heartbeat = clock()

    def renew(self):
        self.last_heartbeat = self.clock()

    def expired(self):
        return self.clock() - self.last_heartbeat >= self.seconds


class Controller:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.lease = Lease()
        self.stop = threading.Event()

    def handler(self):
        controller = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def dispatch(self):
                expected = "Bearer " + controller.descriptor["token"]
                if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected.encode()):
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.command == "GET" and self.path == "/status":
                    result = {"sessionId": controller.descriptor["sessionId"], "state": "running"}
                elif self.command == "POST" and self.path == "/heartbeat":
                    controller.lease.renew()
                    result = {"ok": True}
                elif self.command == "POST" and self.path == "/stop":
                    controller.stop.set()
                    result = {"ok": True}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = dispatch
            do_POST = dispatch

        return Handler


def reserve_display(directory):
    import fcntl
    # Kernel-held locks survive no process; Xvnc's X locks protect other users.
    for number in range(100, 1000):
        if Path("/tmp/.X" + str(number) + "-lock").exists() or Path("/tmp/.X11-unix/X" + str(number)).exists():
            continue
        lock = directory.parent / ("display-" + str(number) + ".lock")
        stream = lock.open("a+")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            stream.seek(0)
            stream.truncate()
            stream.write(str(os.getpid()))
            stream.flush()
            return number, stream
        except BlockingIOError:
            stream.close()
            continue
    raise RuntimeError("No available virtual display")


def desktop_config(directory):
    config = directory / "openbox.xml"
    config.write_text('''<?xml version="1.0"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <focus><focusNew>yes</focusNew></focus>
  <desktops><number>1</number></desktops>
  <applications><application type="normal" title="*IGV*">
    <maximized>yes</maximized>
  </application></applications>
</openbox_config>
''')
    return config


def serve(directory):
    directory = Path(directory).resolve()
    config = json.loads((directory / "request.json").read_text())
    prefix, manifest, env = environment(config["prefix"])
    env.update({"XAUTHORITY": str(directory / "Xauthority"),
                "XDG_CONFIG_HOME": str(directory / "config"),
                "XDG_CACHE_HOME": str(directory / "cache"),
                "XDG_RUNTIME_DIR": str(directory / "runtime"),
                "PROOT_TMP_DIR": str(directory / "runtime")})
    for child in ("config", "cache", "runtime", "igv", "preferences"):
        (directory / child).mkdir(mode=0o700, exist_ok=True)
    children, logs = [], []
    server = None
    lock = None
    descriptor = {"sessionId": config["owner"], "directory": str(directory),
                  "token": secrets.token_hex(32), "vncPassword": secrets.token_urlsafe(6),
                  "state": "starting"}
    controller = Controller(descriptor)

    def launch(name, command, native=False):
        if controller.stop.is_set():
            raise RuntimeError("Remote startup cancelled")
        log = (directory / (name + ".log")).open("ab")
        logs.append(log)
        process = subprocess.Popen(command, env=native_environment(prefix, env) if native else env,
                                   cwd=config["cwd"], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        children.append(process)
        return process

    def request_stop(_signum, _frame):
        controller.stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        display, lock = reserve_display(directory)
        env["DISPLAY"] = ":" + str(display)
        descriptor["display"] = env["DISPLAY"]
        graphics_env = native_environment(prefix, env)
        vnc_port = free_port()
        password = directory / "vnc.passwd"
        password.write_bytes(subprocess.run(
            [binary(prefix, "tigervncpasswd", "vncpasswd"), "-f"],
            input=(descriptor["vncPassword"] + "\n").encode(),
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=graphics_env, timeout=10).stdout)
        password.chmod(0o600)
        try:
            auth = subprocess.run([binary(prefix, "xauth"), "-f", env["XAUTHORITY"], "add", env["DISPLAY"],
                            ".", secrets.token_hex(16)], env=graphics_env, capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            raise RuntimeError("xauth timed out") from None
        if auth.returncode:
            raise RuntimeError("xauth failed: " + auth.stderr.decode(errors="replace"))
        xvnc = binary(prefix, "Xtigervnc", "Xvnc")
        help_text = subprocess.run([xvnc, "-help"], env=graphics_env, capture_output=True, text=True, timeout=10)
        extra = []
        if "-xkbdir" in help_text.stdout + help_text.stderr:
            extra = ["-xkbdir", env["XKB_CONFIG_ROOT"]]
        # Ubuntu's X server can invoke /usr/bin/xkbcomp by absolute path. PRoot
        # maps only those private XKB files for this process, without root/mounts.
        display_command = [binary(prefix, "proot"),
                           "-b", binary(prefix, "xkbcomp") + ":/usr/bin/xkbcomp",
                           "-b", env["XKB_CONFIG_ROOT"] + ":/usr/share/X11/xkb", xvnc]
        vnc = launch("display", [*display_command, env["DISPLAY"], "-geometry", "1600x900", "-depth", "24",
                    "-localhost", "yes", "-rfbport", str(vnc_port), "-SecurityTypes", "VncAuth",
                    "-PasswordFile", str(password), "-auth", env["XAUTHORITY"], "-nolisten", "tcp",
                    "-fp", "built-ins", "-NeverShared", "yes", "-DisconnectClients", "yes", "-FrameRate", "30", *extra], native=True)
        wait_port(vnc_port, vnc, cancel=controller.stop)
        launch("openbox", [binary(prefix, "openbox"), "--config-file", str(desktop_config(directory))], native=True)
        prefs = directory / "igv.properties"
        prefs.write_text("PORT_ENABLED=false\n")
        launch("igv", [manifest["java"], "-Xmx" + config["memory"],
                     "@" + str(Path(config["igvDir"]) / "igv.args"), "-Djava.awt.headless=false",
                     "-Dsamjdk.snappy.disable=true", "-Digv.remote.loopback=true", "-Djava.net.preferIPv4Stack=true",
                     "-Djava.util.prefs.userRoot=" + str(directory / "preferences"),
                     "-cp", str(Path(config["igvDir"]) / "lib" / "*"), "org.igv.ui.Main",
                     "--igvDirectory", str(directory / "igv"), "-o", str(prefs)])
        ws_port = free_port()
        descriptor["websocketPort"] = ws_port
        tokens = directory / "tokens.txt"
        tokens.write_text(descriptor["token"] + ": 127.0.0.1:" + str(vnc_port) + "\n")
        tokens.chmod(0o600)
        bridge = launch("websocket", [manifest["python"], str(Path(__file__).with_name("websocket_bridge.py")),
                        "--token-plugin", "TokenFile", "--token-source", str(tokens),
                        "127.0.0.1:" + str(ws_port)])
        wait_port(ws_port, bridge, cancel=controller.stop)
        # Catch early JVM/WM failures; the IGV splash/loading screen is itself viewable.
        time.sleep(2)
        if any(child.poll() is not None for child in children):
            raise RuntimeError("A desktop component exited during startup; inspect the session logs")
        server = ThreadingHTTPServer(("127.0.0.1", 0), controller.handler())
        server.daemon_threads = True
        descriptor.update({"controlPort": server.server_port, "state": "running"})
        private_json(directory / "session.json", descriptor)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        controller.lease.renew()
        while not controller.stop.wait(1):
            if controller.lease.expired() or any(child.poll() is not None for child in children):
                break
    except Exception as error:
        descriptor.update({"state": "failed", "error": str(error)})
        private_json(directory / "session.json", descriptor)
        raise
    finally:
        if server:
            server.shutdown()
            server.server_close()
        for child in reversed(children):
            stop_process(child)
        for log in logs:
            log.close()
        if lock:
            # Do not unlink a shared lock inode while another starter can open it.
            lock.close()
        for name in ("vnc.passwd", "tokens.txt", "Xauthority", "request.json"):
            (directory / name).unlink(missing_ok=True)
        # Keep logs/config/autosaves for recovery; invalidate all session credentials.
        private_json(directory / "session.json", {"state": descriptor.get("state") if descriptor.get("state") == "failed" else "stopped",
                     "sessionId": config["owner"], "directory": str(directory),
                     "error": descriptor.get("error")})


def start(args):
    if sys.platform != "linux":
        raise RuntimeError("Remote sessions must run on Linux")
    if not OWNER_PATTERN.fullmatch(args.owner):
        raise RuntimeError("Invalid owner identifier")
    if not re.fullmatch(r"[1-9][0-9]*[mMgG]", args.memory):
        raise RuntimeError("Memory must be a positive size, for example 4g")
    prefix, manifest, _env = environment(args.prefix)
    igv = args.igv_dir.expanduser().resolve()
    if not (igv / "lib" / "igv.jar").is_file() or not (igv / "igv.args").is_file():
        raise RuntimeError("IGV distribution missing; run gradlew createDist and select build/IGV-dist")
    cwd = args.cwd.expanduser().resolve()
    if not cwd.is_dir():
        raise RuntimeError("Remote working directory does not exist")
    directory = prefix / "run" / args.owner
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700)  # Never reuse or overwrite another session.
    private_json(directory / "request.json", {"prefix": str(prefix), "igvDir": str(igv),
                 "owner": args.owner, "cwd": str(cwd), "memory": args.memory})
    with (directory / "manager.log").open("ab") as log:
        manager = subprocess.Popen([manifest["python"], str(Path(__file__).resolve()), "serve", str(directory)],
                      stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            descriptor_path = directory / "session.json"
            if descriptor_path.exists():
                descriptor = json.loads(descriptor_path.read_text())
                if descriptor["state"] == "running":
                    print(json.dumps(descriptor))
                    return
                if descriptor["state"] == "failed":
                    raise RuntimeError(descriptor.get("error", "Startup failed") + "; logs: " + str(directory))
            if manager.poll() is not None:
                raise RuntimeError("Session manager exited; logs: " + str(directory))
            time.sleep(0.2)
        raise RuntimeError("Startup timed out; logs: " + str(directory))
    except BaseException:
        stop_process(manager, grace=30)
        raise


def diagnose(prefix, igv_dir=None):
    prefix, manifest, env = environment(prefix)
    checks = [([manifest["java"], "-version"], "Java 21"),
              ([binary(prefix, "Xtigervnc", "Xvnc"), "-version"], "TigerVNC"),
              ([binary(prefix, "openbox"), "--version"], "Openbox"),
              ([binary(prefix, "xauth"), "-V"], "xauth"),
              ([binary(prefix, "xkbcomp"), "-version"], "xkbcomp"),
              ([binary(prefix, "proot"), "--version"], "PRoot"),
              ([binary(prefix, "proot"), "-b", binary(prefix, "xkbcomp") + ":/usr/bin/xkbcomp",
                "/usr/bin/xkbcomp", "-version"], "PRoot/XKB user-space mapping"),
              ([manifest["python"], "-c", "import websockify; import ssl; import numpy"], "Python/websockify")]
    for command, name in checks:
        tool_env = env if name in ("Java 21", "Python/websockify") else native_environment(prefix, env)
        result = subprocess.run(command, env=tool_env, text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError(name + " failed: " + result.stdout + result.stderr)
        print(name + ": OK")
    fonts = subprocess.run([binary(prefix, "fc-list"), "-f", "%{family}\n"], env=native_environment(prefix, env),
                           text=True, capture_output=True, check=True, timeout=20).stdout
    if "DejaVu" not in fonts:
        raise RuntimeError("Bundled DejaVu fonts are unavailable; check fonts.conf")
    print("Fonts: OK")
    if igv_dir:
        if not (igv_dir.expanduser() / "lib" / "igv.jar").is_file():
            raise RuntimeError("IGV distribution missing")
        print("IGV distribution: OK")
    print("Runtime: " + str(prefix))
    print("Run a remote IGV session to verify display, fonts and window interaction.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    starting = commands.add_parser("start")
    starting.add_argument("--prefix", type=Path, default=DEFAULT_PREFIX)
    starting.add_argument("--igv-dir", type=Path, required=True)
    starting.add_argument("--owner", required=True)
    starting.add_argument("--cwd", type=Path, default=Path.cwd())
    starting.add_argument("--memory", default="4g")
    serving = commands.add_parser("serve")
    serving.add_argument("directory", type=Path)
    diagnostic = commands.add_parser("diagnose")
    diagnostic.add_argument("--prefix", type=Path, default=DEFAULT_PREFIX)
    diagnostic.add_argument("--igv-dir", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "start":
            start(args)
        elif args.command == "serve":
            serve(args.directory)
        else:
            diagnose(args.prefix, args.igv_dir)
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
