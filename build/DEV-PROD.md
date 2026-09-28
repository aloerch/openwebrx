# Linux development and production installations

The source builder supports separate deployment modes. Dependency selection is
still controlled independently by `--profile full|core|decoders|receivers`.
Both modes retain the existing native dependency recipes and Release compiler
settings. The differences are installation, runtime isolation, and networking.

| Setting | Development | Production |
| --- | --- | --- |
| Command | `./build-linux.sh build dev` | `./build-linux.sh build prod` |
| Fresh prefix | `~/.local/openwebrx-dev` | `~/.local/openwebrx-prod` |
| Application | Editable, uses checkout | Non-editable installed copy, including web assets |
| Default listener | `127.0.0.1:8073` | `0.0.0.0:8072` |
| Local Whisper listener | `127.0.0.1:8074` | `127.0.0.1:8075` |
| Build cache | Checkout's `.build-linux` | Checkout's `.build-linux-prod` |
| Automatic debug logging | Yes | No |

Configuration, virtual environments, application data, temporary files, and
CodecServer sockets live under the selected prefix by default. Production also
installs its runtime supervisor and diagnostic state inside its prefix; running
production does not need the checkout or its build cache. Runtime helper logs
are in `<prod-prefix>/var/log/openwebrx`; build logs stay in the selected cache.

## Build and run

Run the builder as your normal account, not `sudo ./build-linux.sh`. Its existing
system-prerequisite installation may request sudo. Use `--no-system-packages`
when prerequisites are already installed and no package-manager calls are wanted.

```bash
./build-linux.sh build dev
./build-linux.sh run dev
# Browser on the same computer: http://localhost:8073

./build-linux.sh build prod
export PATH="$HOME/.local/bin:$PATH"
openwebrx
# Browser on the LAN: http://<server-IPv4-address>:8072
```

Production creates `<prod-prefix>/bin/openwebrx` and attempts to link it as
`~/.local/bin/openwebrx`. Put `~/.local/bin` on your shell's persistent PATH as
appropriate for your shell. No shell startup files are edited automatically.
An existing unrelated executable/symlink is never replaced: use the prefix's
launcher, `./build-linux.sh run prod`, or choose another `--bin-dir PATH`.
`./build-linux.sh install-launcher prod` explicitly retries installation.

Production's launcher supplies the selected core configuration and its runtime
environment. Help, version, and administration commands do not start managed
helper services:

```bash
openwebrx --version
openwebrx admin --help
./build-linux.sh run prod -- admin --help
./build-linux.sh run dev -- --debug
```

A fresh production install starts with its own settings and administrator data;
no development accounts/settings are silently copied. Use the administration
command's help to create the desired account. Do not blindly copy settings
containing development data paths, speech URLs, or CodecServer sockets.

Running without a mode remains development by default (`build` is also the
default command). `OWRX_MODE=prod` changes that default; an explicit mode wins.
The doctor, reports, diagnostics, failures, env, clean, and uninstall commands
also accept `dev` or `prod`. Use `settings` to inspect resolved installation
paths and *initial* listener defaults without building anything.

## Existing installations and customization

The preceding builder's actual default was `~/.local/openwebrx-source`. When
that directory exists and `~/.local/openwebrx-dev` does not, development reuses
the old prefix in place. Nothing is moved or deleted, and existing listener
settings (including the previous IPv6 loopback setting) are preserved.

```bash
./build-linux.sh settings dev
./build-linux.sh settings prod
./build-linux.sh build prod --prefix "$HOME/.local/my-radio-prod" \
  --port 8072 --bind-address 192.168.1.20
```

`--port` and `--bind-address` set defaults **only when creating a new config**.
On later builds, edit `[web]` in the existing file instead:

```ini
# ~/.local/openwebrx-prod/etc/openwebrx/openwebrx.conf
[web]
port = 8072
ipv6 = false
bind_address = 0.0.0.0
```

Restart the running instance after editing. An IPv6 bind address requires
`ipv6 = true`. All previously supported `OWRX_*` overrides remain available;
new defaults can also be set with `OWRX_WEB_PORT`, `OWRX_BIND_ADDRESS`, and
`OWRX_BIN_DIR`. Explicit `--prefix` and `--build-root` override their environment
counterparts. Custom prefixes must be supplied consistently for later commands.

Do not carry a sourced development `env.sh` into a production build: it exports
`OWRX_PREFIX` and loader/compiler paths. Use a fresh shell. Mode markers reject
reuse of a marked prefix/cache by the other mode. Intentional custom VENV,
configuration, data, temporary-directory, and port overrides must also remain
separate. Production refuses runtime paths inside the checkout or build cache.
Do not rename/move an installed virtual environment; rebuild at its new prefix.

## Optional systemd autostart

Service installation is explicit. Builds never enable or start a service.
Stop a foreground production instance before enabling the service; a per-prefix
lock rejects competing supervised server instances.

```bash
# Start now and automatically when this user's systemd manager starts:
./build-linux.sh install-service prod

# Alternatively, enable startup at boot even without an interactive login:
./build-linux.sh install-service prod --linger
```

This installs `openwebrx-prod.service` under
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user` and enables it with
`systemctl --user enable --now`. The service runs as the account that built
production, not root. `--linger` explicitly invokes `loginctl enable-linger`
through sudo so that the user's manager can run without a login. The home
and installation directories must be available at boot; an encrypted home
unlocked only on login cannot supply a pre-login service installation.
Existing unrelated service units are not overwritten.

```bash
systemctl --user status openwebrx-prod.service
journalctl --user -u openwebrx-prod.service -f
systemctl --user stop openwebrx-prod.service
systemctl --user start openwebrx-prod.service
./build-linux.sh remove-service prod
```

Removing the service disables/stops it and removes only this installation's
unit. It does **not** disable account-wide lingering, since other user services
may depend on it. The service uses graceful supervisor shutdown followed by
systemd cgroup cleanup, restarts on failure, and does not hide SDR devices.
Device access still requires suitable host udev/group permissions. Test those
permissions without an interactive desktop session before relying on boot use.

## Updating, cleanup, and networking

Stop production before rebuilding it; updating files in an active installation
is not an atomic deployment. After building successfully, start it again:

```bash
systemctl --user stop openwebrx-prod.service
./build-linux.sh build prod
./build-linux.sh feature-report prod
systemctl --user start openwebrx-prod.service
```

`clean prod` removes only its marked build cache, not the installed application.
`uninstall prod` removes the selected marked prefix/cache and its owned command
link, so it also removes that prefix's application data: back up first. Remove
the service before uninstalling. Use the original `--bin-dir` when uninstalling
a custom command link. Deletion refuses unmarked/different-mode directories.
A legacy development prefix receives its ownership marker on the next build.

`0.0.0.0` is a listen address, not the address to enter in the browser. Use the
server's IPv4 address. Production listens on **all** IPv4 interfaces unless
restricted in config, including a public interface when one exists. No firewall
rules, router forwarding, or TLS settings are changed. Permit TCP 8072 only from
trusted networks as appropriate for the host's firewall policy. For remote
Internet use, arrange suitable access controls and HTTPS rather than treating
this profile as an Internet-hardening feature. Existing application certificate
behavior is unchanged.

Distinct software ports do not allow both processes to own the same physical
SDR simultaneously. Configure separate devices or stop the competing instance.

## Offline regression checks

```bash
bash -n build-linux.sh build/linux_profiles.sh
python3 -m unittest discover -s test/source_build -p test_profiles.py -v
python3 -m unittest discover -s test/source_build -p test_source_runtime.py -v
```

These exercise profile selection, config preservation, safe launcher/service
installation, isolated production execution after deleting a fixture checkout,
helper lifecycle and locks, and generated-unit validation when
`systemd-analyze` is available. They use fake installed application/runtime
fixtures: they are not a full native SDR build or a live boot/service test.
