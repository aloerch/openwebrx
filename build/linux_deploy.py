#!/usr/bin/env python3
"""Install profile metadata, launch the installed app, and render user units.

Only install writes runtime assets. Launch/report never invoke pip, build code,
start systemd, or consult a source checkout. This module needs only the stdlib.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile


MANAGED = "# Managed by OpenWebRX+ build-linux.sh"


def atomic_write(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing to overwrite symlink: {path}")
    fd, temporary = tempfile.mkstemp(prefix=".owrx-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def systemd_quote(value, command=False):
    """systemd specifiers expand even inside quotes; ExecStart also expands $."""
    value = str(value)
    if any(character in value for character in "\n\r\0"):
        raise ValueError("Multiline/NUL paths are not supported")
    value = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def service_text(manifest):
    prefix = Path(manifest["prefix"])
    # KillMode=mixed lets the supervisor stop only its own children gracefully.
    # The launcher sets its own working directory, avoiding unit path quoting.
    # Do not hide SDR devices with PrivateDevices or require root privileges.
    return f"""{MANAGED}
# Prefix: {prefix}
[Unit]
Description=OpenWebRX+ production receiver

[Service]
Type=simple
ExecStart=/bin/sh {systemd_quote(prefix / 'bin/openwebrx', command=True)}
Restart=on-failure
RestartSec=5
SuccessExitStatus=130
KillMode=mixed
TimeoutStopSec=30
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=default.target
"""


def check_service(manifest, path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"No owned service unit at {path}")
    text = path.read_text()
    ownership = f"{MANAGED}\n# Prefix: {manifest['prefix']}\n"
    if not text.startswith(ownership):
        raise ValueError(f"Refusing to replace/remove another installation's service: {path}")


def install_launcher(manifest, directory):
    target = Path(manifest["prefix"]) / "bin/openwebrx"
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    link = directory / "openwebrx"
    if os.path.lexists(link):
        if link.is_symlink() and link.resolve() == target.resolve():
            return
        raise ValueError(f"Not replacing existing {link}; use --bin-dir or run {target} directly")
    link.symlink_to(target)
    print(f"Production command: {link}")
    if str(directory) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add this directory to your shell PATH: export PATH={shlex.quote(str(directory))}:\"$PATH\"")


def runtime_environment(manifest):
    env = dict(os.environ)
    prefix, venv = Path(manifest["prefix"]), Path(manifest["venv"])
    # A development venv/PYTHONPATH/loader path must not leak into production.
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "SOAPY_SDR_PLUGIN_PATH", "SOAPY_SDR_ROOT"):
        env.pop(key, None)
    env.update({
        "OWRX_MODE": "prod", "OWRX_PREFIX": str(prefix),
        "VIRTUAL_ENV": str(venv), "PYTHONNOUSERSITE": "1",
        "PATH": f"{venv}/bin:{prefix}/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "LD_LIBRARY_PATH": f"{prefix}/lib:{prefix}/lib64",
    })
    return env


def read_manifest(path):
    manifest = json.loads(Path(path).read_text())
    if manifest.get("mode") != "prod" or manifest.get("schema") != 1:
        raise ValueError("Not an installed production runtime manifest; run build prod")
    for key in ("prefix", "venv", "config", "logs", "state", "model", "runtime_home"):
        value = manifest[key]
        if not Path(value).is_absolute() or any(character in value for character in "\r\n\0"):
            raise ValueError(f"Invalid production path: {key}")
    return manifest


def launch(manifest, action="run", application_args=()):
    env = runtime_environment(manifest)
    os.chdir(manifest["prefix"])
    application_args = list(application_args)
    if application_args[:1] == ["--"]:
        application_args.pop(0)
    # CLI administration/help should not start a radio, CodecServer, or Whisper.
    cli_only = {"admin", "config", "-h", "--help", "-v", "--version"}
    if action == "run" and cli_only.intersection(application_args):
        binary = str(Path(manifest["venv"]) / "bin/openwebrx")
        os.execve(binary, [binary, "-c", manifest["config"], *application_args], env)
    python = str(Path(manifest["venv"]) / "bin/python")
    command = [python, "-I", str(Path(manifest["runtime_home"]) / "source_runtime.py"), action,
               "--mode", "prod", "--root", manifest["prefix"]]
    for key in ("prefix", "venv", "config", "logs", "state", "model", "model_name", "speech_url", "jobs"):
        command.extend(["--" + key.replace("_", "-"), str(manifest[key])])
    command.extend(["--app-args", *application_args])
    os.execve(python, command, env)


def install(args):
    prefix, venv = Path(args.prefix), Path(args.venv)
    address = ipaddress.ip_address(args.bind)
    config = Path(args.config)
    config.parent.mkdir(parents=True, exist_ok=True)
    # All user settings (including their listener) survive subsequent builds.
    if not config.exists():
        text = (f"[core]\ndata_directory = {args.data}\ntemporary_directory = {args.tmp}\nlog_level = INFO\n\n"
                f"[web]\nport = {args.port}\nipv6 = {'true' if address.version == 6 else 'false'}\nbind_address = {address}\n\n"
                f"[aprs]\nsymbols_path = {prefix}/share/aprs-symbols/png\n")
        # Exclusive creation also avoids following a dangling symlink.
        with config.open("x") as output:
            output.write(text)
        config.chmod(0o600)
    else:
        print(f"Preserving existing configuration: {config}")
    exports = {"OWRX_MODE": args.mode, "OWRX_PREFIX": str(prefix)}
    text = MANAGED + "\n" + "".join(f"export {key}={shlex.quote(value)}\n" for key, value in exports.items())
    for key, value in {
        "PATH": f"{prefix}/bin:{venv}/bin", "CMAKE_PREFIX_PATH": str(prefix),
        "PKG_CONFIG_PATH": f"{prefix}/lib/pkgconfig:{prefix}/lib64/pkgconfig:{prefix}/share/pkgconfig",
        "LD_LIBRARY_PATH": f"{prefix}/lib:{prefix}/lib64",
    }.items():
        text += f'export {key}={shlex.quote(value)}"${{{key}:+:${key}}}"\n'
    atomic_write(prefix / "env.sh", text)
    if args.mode == "dev":
        return
    home = prefix / "share/openwebrx"
    for name in ("source_runtime.py", "linux_deploy.py"):
        atomic_write(home / name, (Path(args.root) / "build" / name).read_text())
    state = prefix / "var/lib/openwebrx-build"
    state.mkdir(parents=True, exist_ok=True)
    # Retain diagnostics outside the checkout too; never copy source caches.
    for stale in state.glob("*.skip"):
        stale.unlink()
    for skip in Path(args.state).glob("*.skip"):
        shutil.copy2(skip, state / skip.name)
    logs = prefix / "var/log/openwebrx"
    logs.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": 1, "mode": "prod", "prefix": str(prefix), "venv": str(venv),
        "runtime_home": str(home), "config": args.config, "logs": str(logs), "state": str(state),
        "model_name": args.model_name,
        "model": str(prefix / "share/whisper" / f"ggml-{args.model_name}.bin"),
        "speech_url": f"http://127.0.0.1:{args.whisper_port}/inference", "jobs": args.jobs,
    }
    manifest_file = home / "runtime.json"
    atomic_write(manifest_file, json.dumps(manifest, indent=2) + "\n")
    command = [str(venv / "bin/python"), "-I", str(home / "linux_deploy.py"), "launch", str(manifest_file), "--"]
    launcher = '#!/bin/sh\n' + MANAGED + '\nexec ' + ' '.join(map(shlex.quote, command)) + ' "$@"\n'
    target = prefix / "bin/openwebrx"
    if target.exists() and MANAGED not in target.read_text():
        raise ValueError(f"Refusing to overwrite an unmanaged launcher: {target}")
    atomic_write(target, launcher, 0o755)
    try:
        install_launcher(manifest, args.bin_dir)
    except ValueError as exc:
        # The build is usable via its explicit prefix even when a command exists.
        print(f"[WARN] {exc}", file=sys.stderr)
    atomic_write(home / "openwebrx-prod.service", service_text(manifest))
    print(f"Production runtime installed in {prefix}; no service was enabled.")
    print("Default network access: http://<server-IP>:8072 (see your config for overrides).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install")
    setup.add_argument("--mode", choices=("dev", "prod"), required=True)
    for key in ("root", "prefix", "venv", "config", "data", "tmp", "state", "bind", "model-name", "bin-dir"):
        setup.add_argument("--" + key, required=True)
    for key in ("port", "whisper-port", "jobs"):
        setup.add_argument("--" + key, type=int, required=True)
    for name in ("launch", "runtime", "launcher", "service", "check-service"):
        sub = commands.add_parser(name)
        sub.add_argument("manifest")
        if name == "launch":
            sub.add_argument("application_args", nargs=argparse.REMAINDER)
        elif name == "runtime":
            sub.add_argument("action", choices=("configure", "report", "diagnostics", "run"))
        else:
            sub.add_argument("destination")
    unlink = commands.add_parser("unlink-launcher")
    unlink.add_argument("prefix")
    unlink.add_argument("directory")
    args = parser.parse_args(argv)
    if args.command == "install":
        if not 1 <= args.port <= 65535 or not 1 <= args.whisper_port <= 65535 or args.jobs < 1:
            parser.error("ports must be 1..65535 and jobs must be positive")
        install(args)
        return
    if args.command == "unlink-launcher":
        link = Path(args.directory) / "openwebrx"
        if link.is_symlink() and link.resolve() == (Path(args.prefix) / "bin/openwebrx").resolve():
            link.unlink()
        return
    manifest = read_manifest(args.manifest)
    if args.command == "launch":
        launch(manifest, application_args=args.application_args)
    elif args.command == "runtime":
        launch(manifest, action=args.action)
    elif args.command == "launcher":
        install_launcher(manifest, args.destination)
    elif args.command in ("service", "check-service"):
        path = Path(args.destination)
        if args.command == "check-service" or os.path.lexists(path):
            check_service(manifest, path)
        if args.command == "service":
            atomic_write(path, service_text(manifest))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print(f"[FAIL] deployment: {exc}", file=sys.stderr)
        raise SystemExit(1)
