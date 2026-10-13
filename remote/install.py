#!/usr/bin/env python3
"""Prepare private Ubuntu graphics assets for the active Pixi environment."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

from runtime import DEFAULT_PREFIX

# Ubuntu codename and package providing tigervncpasswd/vncpasswd.
UBUNTU_RELEASES = {"20.04": ("focal", "tigervnc-common"),
                   "22.04": ("jammy", "tigervnc-tools"),
                   "24.04": ("noble", "tigervnc-tools")}
GRAPHICS = ["tigervnc-standalone-server", "openbox", "proot",
            "xauth", "x11-xkb-utils", "x11-utils", "xkb-data", "fontconfig", "fonts-dejavu-core"]
# Keep the host loader/glibc together. Other libraries are installed privately.
HOST_PACKAGES = {"libc6", "libc-bin", "libgcc-s1", "gcc-9-base", "gcc-10-base", "gcc-11-base",
                 "gcc-12-base", "gcc-13-base",
                 "gcc-14-base", "debconf", "dpkg", "ucf", "init-system-helpers"}


def run(args, **kwargs):
    env = kwargs.pop("env", os.environ.copy())
    env["LC_ALL"] = "C"
    if args[0] in ("apt-get", "apt-cache", "dpkg-deb"):
        env.pop("LD_LIBRARY_PATH", None)
        env.pop("LD_PRELOAD", None)
    kwargs["env"] = env
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def check_host():
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("Supported host: Ubuntu 20.04/22.04/24.04 x86_64")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                   if "=" in line)
    if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') not in UBUNTU_RELEASES:
        raise RuntimeError("Supported host: Ubuntu 20.04/22.04/24.04 x86_64")
    for command in ("apt-cache", "apt-get", "dpkg-deb"):
        if not shutil.which(command):
            raise RuntimeError("Required base tool missing: " + command)
    return release["VERSION_ID"].strip('"')


def apt_options(prefix, ubuntu):
    """Use private indexes, sources and caches, including on minimal servers."""
    import pwd
    apt = prefix / "apt"
    for child in ("lists/partial", "archives/partial", "empty"):
        (apt / child).mkdir(parents=True, exist_ok=True)
    codename = UBUNTU_RELEASES[ubuntu][0]
    keyring = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
    if not keyring.is_file():
        raise RuntimeError("Ubuntu archive keyring is missing: " + str(keyring))
    sources = apt / "sources.list"
    sources.write_text("\n".join(
        "deb [arch=amd64 signed-by=" + str(keyring) + "] " + url + " " + suite + " main universe"
        for url, suite in [("https://archive.ubuntu.com/ubuntu", codename),
                           ("https://archive.ubuntu.com/ubuntu", codename + "-updates"),
                           ("https://security.ubuntu.com/ubuntu", codename + "-security")]) + "\n")
    return ["-o", "Dir::State::lists=" + str(apt / "lists"),
            "-o", "Dir::Cache::archives=" + str(apt / "archives"),
            "-o", "Dir::Cache::pkgcache=" + str(apt / "pkgcache.bin"),
            "-o", "Dir::Cache::srcpkgcache=" + str(apt / "srcpkgcache.bin"),
            "-o", "Dir::Etc::sourcelist=" + str(sources),
            "-o", "Dir::Etc::sourceparts=" + str(apt / "empty"),
            "-o", "Dir::Etc::parts=" + str(apt / "empty"),
            "-o", "Dir::Etc::main=" + str(apt / "empty.conf"),
            "-o", "APT::Sandbox::User=" + pwd.getpwuid(os.getuid()).pw_name,
            "-o", "Acquire::Languages=none"]


def deb_packages(options, ubuntu):
    graphics = [*GRAPHICS, UBUNTU_RELEASES[ubuntu][1]]
    output = run(["apt-cache", *options, "depends", "--recurse", "--no-recommends", "--no-suggests",
                  "--no-conflicts", "--no-breaks", "--no-replaces", "--no-enhances", *graphics])
    # Non-indented lines are real package headings; virtual alternatives are <...>.
    names = {line.strip().split(":")[0] for line in output.splitlines()
             if line and not line[0].isspace() and re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::amd64)?", line)}
    names.update(graphics)
    return sorted(names - HOST_PACKAGES)


def install_debs(prefix, cache, options, ubuntu, previous=None):
    records = []
    pinned = {entry["package"]: entry for entry in (previous or {}).get("debs", [])}
    for name in sorted(pinned) if pinned else deb_packages(options, ubuntu):
        if name in pinned:
            version = pinned[name]["version"]
        else:
            policy = run(["apt-cache", *options, "policy", name])
            match = re.search(r"Candidate:\s+(\S+)", policy)
            if not match or match[1] == "(none)":
                raise RuntimeError("No APT candidate for " + name)
            version = match[1]
        if name in pinned:
            fields = {"Filename": pinned[name]["filename"]}
        else:
            stanza = run(["apt-cache", *options, "show", name + "=" + version]).split("\n\n")[0]
            fields = dict(line.split(": ", 1) for line in stanza.splitlines() if ": " in line and not line.startswith(" "))
        expected = pinned[name]["sha256"] if name in pinned else fields.get("SHA256")
        if not expected:
            raise RuntimeError("APT metadata has no SHA256 for " + name)
        filename = Path(fields["Filename"]).name
        archive = cache / filename
        if not archive.exists() or digest(archive) != expected:
            with tempfile.TemporaryDirectory(prefix="deb-", dir=cache) as staging:
                run(["apt-get", *options, "download", name + "=" + version], cwd=staging)
                candidates = list(Path(staging).glob("*.deb"))
                if len(candidates) != 1 or digest(candidates[0]) != expected:
                    raise RuntimeError("APT SHA256 mismatch: " + name)
                candidates[0].replace(archive)
        run(["dpkg-deb", "--extract", str(archive), str(prefix / "root")])
        records.append({"package": name, "version": version, "sha256": expected, "filename": filename})
    return records


def pixi_environment():
    """Validate actual executables instead of silently selecting system tools."""
    value = os.environ.get("CONDA_PREFIX")
    if not value or not os.environ.get("PIXI_PROJECT_ROOT"):
        raise RuntimeError("Run 'pixi install' then 'pixi run setup-remote' from the repository")
    conda = Path(value).expanduser().resolve()
    if not Path(sys.executable).resolve().is_relative_to(conda):
        raise RuntimeError("Python is outside the active Pixi environment")
    java_home = conda / "lib" / "jvm"
    java = java_home / "bin" / "java"
    if not java.is_file() or not (java_home / "bin" / "javac").is_file():
        raise RuntimeError("Pixi openjdk package is missing; run pixi install")
    result = subprocess.run([str(java), "-version"], text=True, capture_output=True, check=True)
    if not re.search(r'version "21[.\"]', result.stderr + result.stdout):
        raise RuntimeError("Java 21 is required in the Pixi environment")
    import websockify  # Verify Pixi installed the Python bridge before preparing graphics.
    return conda, java_home, java


def install(prefix):
    ubuntu = check_host()
    conda, java_home, java = pixi_environment()
    prefix = prefix.expanduser().resolve()
    if prefix == Path.home().resolve() or prefix == Path("/"):
        raise RuntimeError("Choose a dedicated installation directory")
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    graphics = conda / "share" / "igv-remote"
    graphics.mkdir(parents=True, exist_ok=True)
    cache = graphics / "cache"
    cache.mkdir(exist_ok=True)
    previous = json.loads((prefix / "runtime.json").read_text()) if (prefix / "runtime.json").exists() else None
    if previous and previous.get("ubuntu") != ubuntu:
        raise RuntimeError("This prefix belongs to a different Ubuntu release; use a new prefix")
    pinned = json.loads((graphics / "graphics.json").read_text()) if (graphics / "graphics.json").exists() else None
    if pinned and pinned.get("ubuntu") != ubuntu:
        raise RuntimeError("Graphics assets belong to a different Ubuntu release; recreate the Pixi environment")
    options = apt_options(graphics, ubuntu)
    if not pinned:
        print("Refreshing private Ubuntu package indexes…", flush=True)
        run(["apt-get", *options, "update"])
    print("Installing private graphics libraries…", flush=True)
    records = install_debs(graphics, cache, options, ubuntu, pinned)
    (graphics / "graphics.json").write_text(json.dumps({"ubuntu": ubuntu, "debs": records}, indent=2))
    (prefix / "fonts.conf").write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd"><fontconfig>'
        '<dir>' + escape(str(conda / "fonts")) + '</dir><dir>'
        + escape(str(conda / "share" / "fonts")) + '</dir><dir>'
        + escape(str(graphics / "root" / "usr" / "share" / "fonts")) + '</dir>'
        '<cachedir>' + escape(str(prefix / "font-cache"))
        + '</cachedir><include ignore_missing="yes">'
        + escape(str(graphics / "root" / "etc" / "fonts" / "conf.d")) + '</include></fontconfig>')
    manifest = {"schemaVersion": 2, "manager": "pixi", "ubuntu": ubuntu,
                "python": str(Path(sys.executable).absolute()), "java": str(java),
                "javaHome": str(java_home), "condaPrefix": str(conda), "graphicsPrefix": str(graphics),
                "pixiManifest": str(Path(os.environ["PIXI_PROJECT_ROOT"]) / "pixi.toml")}
    (prefix / "runtime.json").write_text(json.dumps(manifest, indent=2))
    print("Installed runtime: " + str(prefix))
    run([sys.executable, str(Path(__file__).with_name("session.py")), "diagnose", "--prefix", str(prefix)])
    print("Dependency diagnostics: OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=DEFAULT_PREFIX)
    args = parser.parse_args()
    try:
        install(args.prefix)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        print("Installation failed: " + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
