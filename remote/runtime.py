"""Environment for an isolated, unprivileged Ubuntu graphics runtime."""

import json
import os
from pathlib import Path

DEFAULT_PREFIX = Path.home() / ".local" / "share" / "igv-remote"


def environment(prefix):
    prefix = Path(prefix).expanduser().resolve()
    manifest = json.loads((prefix / "runtime.json").read_text())
    env = os.environ.copy()
    env.update({
        "PATH": os.pathsep.join([str(prefix / "jdk" / "bin"),
                                 str(prefix / "root" / "usr" / "bin"), env.get("PATH", "")]),
        "LD_LIBRARY_PATH": os.pathsep.join([
            str(prefix / "root" / "usr" / "lib" / "x86_64-linux-gnu"),
            str(prefix / "root" / "lib" / "x86_64-linux-gnu"),
            str(prefix / "root" / "usr" / "lib"),
        ]),
        "PYTHONPATH": str(prefix / "python"),
        "PYTHONNOUSERSITE": "1",
        "FONTCONFIG_FILE": str(prefix / "fonts.conf"),
        "FONTCONFIG_PATH": str(prefix / "root" / "etc" / "fonts"),
        "XKB_CONFIG_ROOT": str(prefix / "root" / "usr" / "share" / "X11" / "xkb"),
        "XLOCALEDIR": str(prefix / "root" / "usr" / "share" / "X11" / "locale"),
        "PROOT_NO_SECCOMP": "1",
        "XDG_DATA_DIRS": str(prefix / "root" / "usr" / "share") + ":/usr/local/share:/usr/share",
    })
    return prefix, manifest, env


def binary(prefix, *names):
    for name in names:
        path = prefix / "root" / "usr" / "bin" / name
        if path.is_file():
            return str(path)
    raise RuntimeError("Missing runtime executable: " + "/".join(names))
