"""Build a pinned user-space PRoot with the seccomp-compatible syscall avoider."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

VERSION = "5.4.0"
SOURCE_URL = "https://codeload.github.com/proot-me/proot/tar.gz/refs/tags/v5.4.0"
SOURCE_SHA256 = "29248aac2a7ce10c3bd5ee5602742ec33b2532310ff9cf73b79f3c133e5a5f68"
BUILD_RECIPE_VERSION = 1


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install_proot(graphics, cache, conda):
    private = graphics / "proot"
    private.mkdir(exist_ok=True)
    built = private / "proot"
    state_path = private / "build.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    if not (state.get("sourceSha256") == SOURCE_SHA256 and state.get("recipeVersion") == BUILD_RECIPE_VERSION and built.is_file()
            and sha256(built) == state.get("binarySha256")):
        archive = cache / ("proot-v" + VERSION + ".tar.gz")
        if not archive.is_file() or sha256(archive) != SOURCE_SHA256:
            print("PRoot: downloading pinned upstream source v" + VERSION, flush=True)
            temporary = archive.with_suffix(".download")
            try:
                with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
                if sha256(temporary) != SOURCE_SHA256:
                    raise RuntimeError("PRoot source SHA256 mismatch")
                temporary.replace(archive)
            finally:
                temporary.unlink(missing_ok=True)
        tools = {"CC": "x86_64-conda-linux-gnu-gcc", "OBJCOPY": "x86_64-conda-linux-gnu-objcopy",
                 "OBJDUMP": "x86_64-conda-linux-gnu-objdump", "STRIP": "x86_64-conda-linux-gnu-strip"}
        for tool in ["make", *tools.values()]:
            if not (conda / "bin" / tool).is_file():
                raise RuntimeError("Pixi build tool missing: " + tool + "; run pixi install")
        root = graphics / "root"
        if not (root / "usr" / "include" / "talloc.h").is_file():
            raise RuntimeError("Private libtalloc-dev is missing; rerun setup-remote")
        print("PRoot: building v" + VERSION + " once (no sudo)", flush=True)
        with tempfile.TemporaryDirectory(prefix="build-", dir=private) as directory:
            with tarfile.open(archive) as source:
                source.extractall(directory, filter="data")
            src = Path(directory) / ("proot-" + VERSION) / "src"
            if "#define SYSCALL_AVOIDER ((word_t) -1)" not in (src / "arch.h").read_text():
                raise RuntimeError("PRoot source lacks the expected syscall compatibility fix")
            includes = Path(directory) / "include"
            includes.mkdir()
            shutil.copy2(root / "usr" / "include" / "talloc.h", includes / "talloc.h")
            flags = "-D_FILE_OFFSET_BITS=64 -D_GNU_SOURCE -I. -I" + shlex.quote(str(includes))
            libraries = root / "usr" / "lib" / "x86_64-linux-gnu"
            command = [str(conda / "bin" / "make"), "-C", str(src), "-j2", "proot",
                       "GIT_VERSION=v" + VERSION, "GIT_COMMIT=bd5a5f6", "HAS_SWIG=", "HAS_PYTHON_CONFIG=",
                       "CPPFLAGS=" + flags, "CFLAGS=-O2 -Wall -Wextra",
                       # Ubuntu's Talloc resolves against the host libc, not the older compiler SDK.
                       "LDFLAGS=-Wl,-z,noexecstack -Wl,--allow-shlib-undefined -L" + shlex.quote(str(libraries)) + " -ltalloc",
                       *[key + "=" + shlex.quote(str(conda / "bin" / value)) for key, value in tools.items()]]
            env = os.environ.copy()
            env.pop("LD_PRELOAD", None)
            with (private / "build.log").open("w") as log:
                result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            if result.returncode:
                raise RuntimeError("PRoot build failed; inspect " + str(private / "build.log"))
            temporary = built.with_suffix(".tmp")
            shutil.copy2(src / "proot", temporary)
            temporary.chmod(0o755)
            temporary.replace(built)
        state = {"version": VERSION, "recipeVersion": BUILD_RECIPE_VERSION, "sourceUrl": SOURCE_URL, "sourceSha256": SOURCE_SHA256,
                 "binarySha256": sha256(built)}
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2))
        temporary.replace(state_path)
    else:
        print("PRoot: reusing verified build v" + VERSION, flush=True)
    # Keep the existing executable location so installed VSIX launchers also benefit.
    target = graphics / "root" / "usr" / "bin" / "proot"
    if not target.is_file() or sha256(target) != state["binarySha256"]:
        original = private / "proot.ubuntu"
        if target.is_file() and not original.exists():
            shutil.copy2(target, original)
        temporary = target.with_suffix(".tmp")
        shutil.copy2(built, temporary)
        temporary.replace(target)
    return state
