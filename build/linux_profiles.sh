# Deployment profiles and command dispatch for build-linux.sh (source only).
MODE="${OWRX_MODE:-dev}"
PREFIX_OVERRIDE=""
WORK_OVERRIDE=""
PORT_OVERRIDE=""
BIND_OVERRIDE=""
BIN_DIR="${OWRX_BIN_DIR:-$HOME/.local/bin}"
LINGER=0
APP_ARGS=()

profile_path(){
  [[ -n "$1" && "$1" != *$'\n'* && "$1" != *$'\r'* ]] || die "invalid empty/multiline path"
  readlink -m -- "$1"
}

configure_build_mode(){
  case "$MODE" in dev|prod) ;; *) die "mode must be dev or prod" ;; esac
  local default_prefix="$HOME/.local/openwebrx-$MODE" default_work="$ROOT/.build-linux" default_port=8073 default_bind=127.0.0.1 default_whisper=8074
  if [[ "$MODE" == prod ]]; then
    default_work="$ROOT/.build-linux-prod"; default_port=8072; default_bind=0.0.0.0; default_whisper=8075
  elif [[ ! -e "$default_prefix" && -d "$HOME/.local/openwebrx-source" ]]; then
    # Keep existing installations/configuration in place. Never silently migrate.
    default_prefix="$HOME/.local/openwebrx-source"
  fi
  PREFIX="$(profile_path "${PREFIX_OVERRIDE:-${OWRX_PREFIX:-$default_prefix}}")"
  WORK="$(profile_path "${WORK_OVERRIDE:-${OWRX_BUILD_ROOT:-$default_work}}")"
  VENV="$(profile_path "${OWRX_VENV:-$PREFIX/venv}")"
  CONF="$(profile_path "${OWRX_CONFIG_FILE:-$PREFIX/etc/openwebrx/openwebrx.conf}")"
  DATA="$(profile_path "${OWRX_DATA_DIR:-$PREFIX/var/lib/openwebrx}")"
  TMP="$(profile_path "${OWRX_TMP_DIR:-$PREFIX/var/tmp}")"
  BIN_DIR="$(profile_path "$BIN_DIR")"
  SRC="$WORK/src"; BLD="$WORK/build"; STATE="$WORK/state"; LOG="$WORK/logs"
  WEB_PORT="${PORT_OVERRIDE:-${OWRX_WEB_PORT:-$default_port}}"
  WEB_BIND="${BIND_OVERRIDE:-${OWRX_BIND_ADDRESS:-$default_bind}}"
  WHISPER_PORT="${OWRX_WHISPER_PORT:-$default_whisper}"
  local port
  for port in "$WEB_PORT" "$WHISPER_PORT"; do
    [[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || die "port must be an integer from 1 to 65535"
  done
  [[ "$((10#$WEB_PORT))" != "$((10#$WHISPER_PORT))" ]] || die "web and Whisper ports must differ"
  [[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "OWRX_JOBS must be positive"
  WHISPER_DIR="$PREFIX/share/whisper"
  WHISPER_MODEL="$WHISPER_DIR/ggml-$WHISPER_MODEL_NAME.bin"
  WHISPER_URL="http://127.0.0.1:$WHISPER_PORT/inference"
  RUNTIME_HOME="$PREFIX/share/openwebrx"
  case "$PROFILE" in full|core|decoders|receivers) ;; *) die "profile must be full|core|decoders|receivers" ;; esac
  # Configuration values must not inject additional INI sections or shell lines.
  [[ -n "$WEB_BIND" && "$WEB_BIND" != *$'\n'* && "$WEB_BIND" != *$'\r'* ]] || die "invalid bind address"
  "$PYTHON" -S -c 'import ipaddress, sys; ipaddress.ip_address(sys.argv[1])' "$WEB_BIND" >/dev/null 2>&1 || die "bind address must be an IPv4 or IPv6 address"
  [[ "$WHISPER_MODEL_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid Whisper model name"
  local path
  for path in "$PREFIX" "$WORK"; do
    [[ "$path" != / && "$path" != "$HOME" && "$path" != "$ROOT" && "$path" != /usr && "$path" != /usr/local && "$path" != /opt && "$path" != /home ]] || die "unsafe prefix/build root: $path"
    [[ "$ROOT/" != "$path/"* && "$HOME/" != "$path/"* ]] || die "prefix/build root cannot contain the checkout or home directory"
    if [[ -f "$path/.owrx-mode" && "$(cat "$path/.owrx-mode")" != "$MODE" ]]; then
      die "$path belongs to another mode; choose a separate prefix/build root (check inherited OWRX_* variables)"
    fi
  done
  [[ "$PREFIX/" != "$WORK/"* && "$WORK/" != "$PREFIX/"* ]] || die "prefix and build root must be separate, non-nested directories"
  # Production must not depend on mutable files inside the build checkout.
  if [[ "$MODE" == prod ]]; then
    for path in "$PREFIX" "$VENV" "$CONF" "$DATA" "$TMP"; do
      [[ "$path/" != "$ROOT/"* && "$path/" != "$WORK/"* ]] || die "production paths must be outside the checkout and build root: $path"
    done
  fi
}

prepare_build_mode(){
  # Reject relabeling an old editable installation as production.
  if [[ "$MODE" == prod && ! -f "$PREFIX/.owrx-mode" && ( -e "$PREFIX/env.sh" || -d "$VENV" || -f "$CONF" ) ]]; then
    die "unmarked existing installation at $PREFIX; use a fresh production prefix"
  fi
  mkdir -p "$PREFIX" "$WORK"
  printf '%s\n' "$MODE" >"$PREFIX/.owrx-mode"
  printf '%s\n' "$MODE" >"$WORK/.owrx-mode"
}

deployment_install(){
  "$VENV/bin/python" "$ROOT/build/linux_deploy.py" install \
    --mode "$MODE" --root "$ROOT" --prefix "$PREFIX" --venv "$VENV" \
    --config "$CONF" --data "$DATA" --tmp "$TMP" --state "$STATE" \
    --port "$WEB_PORT" --bind "$WEB_BIND" --model-name "$WHISPER_MODEL_NAME" \
    --whisper-port "$WHISPER_PORT" --jobs "$JOBS" --bin-dir "$BIN_DIR"
}

production_required(){
  [[ "$MODE" == prod ]] || die "this command requires prod mode"
  [[ -f "$RUNTIME_HOME/runtime.json" && -x "$PREFIX/bin/openwebrx" ]] || die "run build prod first"
}

service_command(){
  production_required
  [[ ${EUID:-$(id -u)} -ne 0 ]] || die "run user-service commands as the account that built prod, not with sudo"
  command -v systemctl >/dev/null || die "systemd/systemctl is required"
  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user" unit_name=openwebrx-prod.service
  case "$1" in
    install-service)
      "$VENV/bin/python" -I "$RUNTIME_HOME/linux_deploy.py" service "$RUNTIME_HOME/runtime.json" "$unit_dir/$unit_name"
      systemctl --user daemon-reload
      if [[ "$LINGER" == 1 ]]; then
        command -v loginctl >/dev/null || die "loginctl is required for boot startup"
        sudo_run loginctl enable-linger "$(id -un)"
      fi
      systemctl --user enable --now "$unit_name"
      info "Service enabled. Boot without login requires: $0 install-service prod --linger"
      ;;
    remove-service)
      "$VENV/bin/python" -I "$RUNTIME_HOME/linux_deploy.py" check-service "$RUNTIME_HOME/runtime.json" "$unit_dir/$unit_name"
      systemctl --user disable --now "$unit_name"
      rm -- "$unit_dir/$unit_name"
      systemctl --user daemon-reload
      info "Service removed. User-wide lingering is unchanged."
      ;;
  esac
}

remove_build_files(){
  local path
  for path in "$@"; do
    [[ ! -e "$path" ]] && continue
    [[ -f "$path/.owrx-mode" && "$(cat "$path/.owrx-mode")" == "$MODE" ]] || die "refusing to delete an unmarked or different-mode directory: $path"
  done
  for path in "$@"; do rm -rf -- "$path"; done
}

profile_usage(){ cat <<'HELP'
Usage: ./build-linux.sh [options] COMMAND [dev|prod] [-- application arguments]
Commands: build (default), run, doctor, feature-report, diagnostics, failures,
          check-refs, check-patches, env, settings, clean, uninstall,
          install-launcher, install-service, remove-service, help

  --profile full|core|decoders|receivers   Dependency selection (default: full)
  --prefix PATH                          Override installation prefix
  --build-root PATH                      Override private source/build cache
  --port PORT                            Initial web port (existing config preserved)
  --bind-address IP                      Initial bind address (existing config preserved)
  --bin-dir PATH                         Production launcher directory (~/.local/bin)
  --linger                               With install-service prod: start at boot
  --latest | --force | --strict | --no-system-packages

Default mode: dev (or OWRX_MODE). Fresh defaults:
  dev  ~/.local/openwebrx-dev   127.0.0.1:8073  Whisper 127.0.0.1:8074
  prod ~/.local/openwebrx-prod  0.0.0.0:8072    Whisper 127.0.0.1:8075
Existing ~/.local/openwebrx-source is reused for dev when no new dev prefix exists.
Production listens on all IPv4 interfaces; use http://<server-IP>:8072.
Builds preserve existing configuration. Edit its [web] section to change bindings.
Service installation is opt-in and uses systemd --user, never a root SDR process.
See build/DEV-PROD.md for migration, service startup, and network precautions.
HELP
}

build_linux_main(){
  local command_seen=0 mode_seen=0 option
  CMD=build
  while [[ $# -gt 0 ]]; do
    case "$1" in
      build|run|doctor|feature-report|diagnostics|failures|check-refs|check-patches|env|settings|clean|uninstall|install-launcher|install-service|remove-service|help)
        [[ "$command_seen" == 0 ]] || die "specify only one command"
        CMD="$1"; command_seen=1; shift ;;
      dev|prod)
        [[ "$mode_seen" == 0 ]] || die "specify only one mode"
        MODE="$1"; mode_seen=1; shift ;;
      --profile|--prefix|--build-root|--port|--bind-address|--bin-dir)
        option="$1"
        [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "$option requires a value"
        case "$option" in
          --profile) PROFILE="$2" ;; --prefix) PREFIX_OVERRIDE="$2" ;;
          --build-root) WORK_OVERRIDE="$2" ;; --port) PORT_OVERRIDE="$2" ;;
          --bind-address) BIND_OVERRIDE="$2" ;; --bin-dir) BIN_DIR="$2" ;;
        esac
        shift 2 ;;
      --latest) LATEST=1; shift ;; --force) FORCE=1; shift ;;
      --strict) STRICT=1; shift ;; --no-system-packages) INSTALL_SYS=0; shift ;;
      --linger) LINGER=1; shift ;;
      -h|--help) profile_usage; return 0 ;;
      --) shift; APP_ARGS=("$@"); break ;;
      *) die "unknown argument: $1" ;;
    esac
  done
  [[ "$CMD" != help ]] || { profile_usage; return 0; }
  [[ ${#APP_ARGS[@]} -eq 0 || "$CMD" == run ]] || die "application arguments are only valid with run"
  [[ "$LINGER" == 0 || "$CMD" == install-service ]] || die "--linger requires install-service prod"
  configure_build_mode
  case "$CMD" in
    build) build_all ;;
    run) run_app ;;
    doctor) doctor ;;
    feature-report) feature_report ;;
    diagnostics) diagnostics ;;
    failures) failure_report ;;
    check-refs) check_refs ;;
    check-patches) prepare_build_mode; check_patches ;;
    env) cat "$PREFIX/env.sh" ;;
    settings) printf 'Mode: %s\nPrefix: %s\nBuild root: %s\nConfig: %s\nInitial web listener: %s:%s\nWhisper: %s\n' "$MODE" "$PREFIX" "$WORK" "$CONF" "$WEB_BIND" "$WEB_PORT" "$WHISPER_URL" ;;
    install-launcher)
      production_required
      "$VENV/bin/python" -I "$RUNTIME_HOME/linux_deploy.py" launcher "$RUNTIME_HOME/runtime.json" "$BIN_DIR" ;;
    install-service|remove-service) service_command "$CMD" ;;
    clean) remove_build_files "$WORK" ;;
    uninstall)
      if [[ "$MODE" == prod ]]; then
        [[ ! -e "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/openwebrx-prod.service" ]] || die "remove the production service before uninstalling"
        "$PYTHON" "$ROOT/build/linux_deploy.py" unlink-launcher "$PREFIX" "$BIN_DIR"
      fi
      remove_build_files "$PREFIX" "$WORK" ;;
  esac
}
