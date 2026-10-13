"""Environment for an isolated, unprivileged Ubuntu graphics runtime."""

import json
import os
from pathlib import Path

DEFAULT_PREFIX = Path.home() / ".local" / "share" / "igv-remote"


def environment(prefix):
    prefix = Path(prefix).expanduser().resolve()
    manifest = json.loads((prefix / "runtime.json").read_text())
    graphics = Path(manifest.get("graphicsPrefix", str(prefix)))
    pixi = manifest.get("manager") == "pixi"
    conda = Path(manifest["condaPrefix"]) if pixi else None
    if pixi and not Path(manifest["python"]).is_file():
        raise RuntimeError("Pixi environment is missing; run pixi install and pixi run setup-remote")
    manifest.setdefault("java", str(prefix / "jdk" / "bin" / "java"))
    manifest.setdefault("javaHome", str(prefix / "jdk"))
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    if pixi:
        env.pop("PYTHONPATH", None)
        env["CONDA_PREFIX"] = str(conda)
        env["LD_LIBRARY_PATH"] = str(conda / "lib")
    else:
        env["PYTHONPATH"] = str(prefix / "python")
        env["LD_LIBRARY_PATH"] = os.pathsep.join([
            str(graphics / "root" / "usr" / "lib" / "x86_64-linux-gnu"),
            str(graphics / "root" / "lib" / "x86_64-linux-gnu"),
            str(graphics / "root" / "usr" / "lib"),
        ])
    env.update({
        "PATH": os.pathsep.join([str(conda / "bin") if pixi else str(prefix / "jdk" / "bin"),
                                 str(graphics / "root" / "usr" / "bin"), env.get("PATH", "")]),
        "JAVA_HOME": manifest["javaHome"],
        "PYTHONNOUSERSITE": "1",
        "FONTCONFIG_FILE": str(prefix / "fonts.conf"),
        "FONTCONFIG_PATH": str(graphics / "root" / "etc" / "fonts"),
        "XKB_CONFIG_ROOT": str(graphics / "root" / "usr" / "share" / "X11" / "xkb"),
        "XLOCALEDIR": str(graphics / "root" / "usr" / "share" / "X11" / "locale"),
        "PROOT_NO_SECCOMP": "1",
        "XDG_DATA_DIRS": str(graphics / "root" / "usr" / "share") + ":/usr/local/share:/usr/share",
    })
    return prefix, manifest, env


def binary(prefix, *names):
    manifest = json.loads((prefix / "runtime.json").read_text())
    graphics = Path(manifest.get("graphicsPrefix", str(prefix)))
    for name in names:
        path = graphics / "root" / "usr" / "bin" / name
        if path.is_file():
            return str(path)
    raise RuntimeError("Missing runtime executable: " + "/".join(names))


def native_environment(prefix, env):
    """Keep Ubuntu graphics libraries out of the Pixi Python/Java processes."""
    manifest = json.loads((prefix / "runtime.json").read_text())
    graphics = Path(manifest.get("graphicsPrefix", str(prefix)))
    result = env.copy()
    result["LD_LIBRARY_PATH"] = os.pathsep.join([
        str(graphics / "root" / "usr" / "lib" / "x86_64-linux-gnu"),
        str(graphics / "root" / "lib" / "x86_64-linux-gnu"),
        str(graphics / "root" / "usr" / "lib"),
    ])
    return result
