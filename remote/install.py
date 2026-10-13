#!/usr/bin/env python3
"""Install an IGV graphics runtime into a user-owned prefix, without sudo."""

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
import tarfile
import tempfile
import urllib.request
from xml.sax.saxutils import escape

from runtime import DEFAULT_PREFIX

PIP_VERSION = "25.1.1"
WEBSOCKIFY_VERSION = "0.13.0"
GRAPHICS = ["tigervnc-standalone-server", "tigervnc-tools", "openbox", "proot",
            "xauth", "x11-xkb-utils", "x11-utils", "xkb-data", "fontconfig", "fonts-dejavu-core", "python3-venv"]
# Keep the host loader/glibc together. Other libraries are installed privately.
HOST_PACKAGES = {"libc6", "libc-bin", "libgcc-s1", "gcc-12-base", "gcc-13-base",
                 "gcc-14-base", "debconf", "dpkg", "ucf", "init-system-helpers"}


def run(args, **kwargs):
    env = kwargs.pop("env", os.environ.copy())
    env["LC_ALL"] = "C"
    kwargs["env"] = env
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def metadata(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def download(url, sha256, target):
    if target.is_file() and digest(target) == sha256:
        return
    part = target.with_suffix(target.suffix + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, part.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if digest(part) != sha256:
            raise RuntimeError("SHA256 mismatch: " + target.name)
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)


def check_host():
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("Supported host: Ubuntu 22.04/24.04 x86_64")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                   if "=" in line)
    if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') not in ("22.04", "24.04"):
        raise RuntimeError("Supported host: Ubuntu 22.04/24.04 x86_64")
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
    codename = {"22.04": "jammy", "24.04": "noble"}[ubuntu]
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


def deb_packages(options):
    output = run(["apt-cache", *options, "depends", "--recurse", "--no-recommends", "--no-suggests",
                  "--no-conflicts", "--no-breaks", "--no-replaces", "--no-enhances", *GRAPHICS])
    # Non-indented lines are real package headings; virtual alternatives are <...>.
    names = {line.strip().split(":")[0] for line in output.splitlines()
             if line and not line[0].isspace() and re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::amd64)?", line)}
    names.update(GRAPHICS)
    return sorted(names - HOST_PACKAGES)


def install_debs(prefix, cache, options, previous=None):
    records = []
    pinned = {entry["package"]: entry for entry in (previous or {}).get("debs", [])}
    for name in sorted(pinned) if pinned else deb_packages(options):
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


def install_jdk(prefix, cache, previous=None):
    if previous:
        record = previous["jdk"]
    else:
        releases = metadata("https://api.adoptium.net/v3/assets/latest/21/hotspot"
                            "?architecture=x64&image_type=jdk&os=linux&vendor=eclipse")
        release = releases[0]
        package = release["binary"]["package"]
        record = {"version": release["release_name"], "sha256": package["checksum"],
                  "url": package["link"], "filename": package["name"]}
    archive = cache / record["filename"]
    download(record["url"], record["sha256"], archive)
    if not (prefix / "jdk" / "bin" / "java").exists():
        with tempfile.TemporaryDirectory(prefix="jdk-", dir=prefix) as staging:
            with tarfile.open(archive) as tar:
                extract_jdk(tar, Path(staging))
            directories = [p for p in Path(staging).iterdir() if p.is_dir()]
            if len(directories) != 1:
                raise RuntimeError("Unexpected JDK archive layout")
            directories[0].rename(prefix / "jdk")
    return record


def extract_jdk(tar, base):
    """Validate before extracting; reject devices and paths outside the prefix."""
    base = base.resolve()
    for member in tar.getmembers():
        target = (base / member.name).resolve()
        if not target.is_relative_to(base) or not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise RuntimeError("Unsafe JDK archive path")
        if member.issym() or member.islnk():
            linked = ((target.parent if member.issym() else base) / member.linkname).resolve()
            if not linked.is_relative_to(base):
                raise RuntimeError("Unsafe JDK archive link")
    tar.extractall(base)


def pip_wheel(cache):
    release = metadata("https://pypi.org/pypi/pip/" + PIP_VERSION + "/json")
    wheel = next(f for f in release["urls"] if f["filename"].endswith(".whl"))
    target = cache / wheel["filename"]
    download(wheel["url"], wheel["digests"]["sha256"], target)
    return target


def install(prefix):
    ubuntu = check_host()
    prefix = prefix.expanduser().resolve()
    if prefix == Path.home().resolve() or prefix == Path("/"):
        raise RuntimeError("Choose a dedicated installation directory")
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache = prefix / "cache"
    cache.mkdir(exist_ok=True)
    previous = json.loads((prefix / "runtime.json").read_text()) if (prefix / "runtime.json").exists() else None
    if previous and previous.get("ubuntu") != ubuntu:
        raise RuntimeError("This prefix belongs to a different Ubuntu release; use a new prefix")
    options = apt_options(prefix, ubuntu)
    if not previous:
        print("Refreshing private Ubuntu package indexes…", flush=True)
        run(["apt-get", *options, "update"])
    print("Installing private graphics libraries…", flush=True)
    records = install_debs(prefix, cache, options, previous)
    jdk = install_jdk(prefix, cache, previous)
    python = prefix / "venv" / "bin" / "python"
    if not python.exists():
        bootstrap = os.environ.copy()
        bootstrap["PYTHONPATH"] = str(prefix / "root" / "usr" / "lib" / ("python" + str(sys.version_info.major) + "." + str(sys.version_info.minor)))
        run([sys.executable, "-m", "venv", "--without-pip", str(prefix / "venv")], env=bootstrap)
    pip = pip_wheel(cache)
    # Resolve transitive wheels once; record their exact versions and hashes for reuse.
    report = prefix / "python-packages.json"
    lock_path = prefix / "requirements.lock"
    requirements = ["--require-hashes", "-r", str(lock_path)] if lock_path.exists() else ["websockify==" + WEBSOCKIFY_VERSION]
    run([str(python), str(pip / "pip"), "install", "--disable-pip-version-check",
         "--only-binary=:all:", "--target", str(prefix / "python"), "--upgrade",
         "--cache-dir", str(cache / "pip"), "--report", str(report),
         *requirements])
    installed = json.loads(report.read_text())
    lock = []
    for item in installed["install"]:
        sha = item["download_info"]["archive_info"]["hashes"]["sha256"]
        lock.append(item["metadata"]["name"] + "==" + item["metadata"]["version"] + " --hash=sha256:" + sha)
    lock_path.write_text("\n".join(sorted(lock)) + "\n")
    (prefix / "fonts.conf").write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd"><fontconfig>'
        '<dir>' + escape(str(prefix / "root" / "usr" / "share" / "fonts")) + '</dir>'
        '<dir>/usr/share/fonts</dir><cachedir>' + escape(str(prefix / "font-cache"))
        + '</cachedir><include ignore_missing="yes">'
        + escape(str(prefix / "root" / "etc" / "fonts" / "conf.d")) + '</include></fontconfig>')
    manifest = {"schemaVersion": 1, "ubuntu": ubuntu, "python": str(python),
                "jdk": jdk, "debs": records, "websockify": WEBSOCKIFY_VERSION}
    (prefix / "runtime.json").write_text(json.dumps(manifest, indent=2))
    print("Installed runtime: " + str(prefix))
    run([str(python), str(Path(__file__).with_name("session.py")), "diagnose", "--prefix", str(prefix)])
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
