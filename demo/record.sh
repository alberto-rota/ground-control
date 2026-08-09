#!/usr/bin/env bash
#
# Render the README's demo GIFs with VHS.
#
#   demo/record.sh                # render every tape
#   demo/record.sh cpu gpu        # render demo/tapes/cpu.tape and gpu.tape
#   demo/record.sh --list         # show what tapes exist
#   demo/record.sh --no-load      # skip the synthetic load (flat plots)
#   demo/record.sh --no-optimize  # keep the raw GIFs, do not run gifsicle
#   demo/record.sh --no-retime    # leave short GIFs short instead of stretching
#
# Each tape is self-contained: it prepares its own throwaway config (so your
# real ~/.config/ground-control is never touched), starts the app, drives it,
# and quits. What this script owns is the parts a tape cannot express -- the
# background load generator, and the environment every tape needs.
#
# Requirements: vhs (which needs ttyd, ffmpeg and a Chromium binary), a
# JetBrains Mono Nerd Font, and optionally nvcc for the GPU load.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$REPO_ROOT/demo"
TAPE_DIR="$DEMO_DIR/tapes"
OUT_DIR="$REPO_ROOT/assets"
WORK_DIR="${GC_DEMO_WORK:-$DEMO_DIR/.work}"

PROFILE="${GC_DEMO_PROFILE:-wave}"
WITH_LOAD=1
LIST_ONLY=0
OPTIMIZE=1
RETIME=1

TAPES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-load) WITH_LOAD=0 ;;
    --no-optimize) OPTIMIZE=0 ;;
    --no-retime) RETIME=0 ;;
    --list) LIST_ONLY=1 ;;
    --profile) PROFILE="$2"; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "record.sh: unknown flag $1" >&2; exit 2 ;;
    *) TAPES+=("$1") ;;
  esac
  shift
done

if [[ $LIST_ONLY -eq 1 ]]; then
  for t in "$TAPE_DIR"/*.tape; do
    name="$(basename "$t" .tape)"
    [[ $name == _* ]] && continue
    printf '  %-16s %s\n' "$name" "$(sed -n 's/^# *//p' "$t" | head -1)"
  done
  exit 0
fi

if [[ ${#TAPES[@]} -eq 0 ]]; then
  for t in "$TAPE_DIR"/*.tape; do
    name="$(basename "$t" .tape)"
    [[ $name == _* ]] && continue   # _theme-*.tape are Source'd, not rendered
    TAPES+=("$name")
  done
fi

# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #

mkdir -p "$WORK_DIR" "$OUT_DIR"

# Refuse to run twice at once. Concurrent renders halve each other's capture
# rate, and the symptom is a set of GIFs that are all exactly half as long as
# their tapes ask for -- which reads as a machine-too-slow problem rather than
# the self-inflicted one it is.
exec 9>"$WORK_DIR/record.lock"
if command -v flock >/dev/null && ! flock -n 9; then
  echo "record.sh: another render is already running (lock: $WORK_DIR/record.lock)" >&2
  exit 1
fi

# A venv holding *this checkout* rather than whatever `gc` happens to be on
# PATH -- otherwise the GIFs advertise a released version's behaviour.
VENV="$WORK_DIR/venv"
if [[ ! -x "$VENV/bin/gc" ]]; then
  echo "==> creating demo venv"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install -e "$REPO_ROOT"
fi
PYTHON="$VENV/bin/python"

# rod (the browser driver inside vhs) has no prebuilt Chromium for some
# platforms, and distro Chromium is often a snap it cannot reach. Point
# GC_DEMO_CHROMIUM at any Chromium/Chrome binary to override.
if [[ -n "${GC_DEMO_CHROMIUM:-}" ]]; then
  SHIM_DIR="$WORK_DIR/bin"
  mkdir -p "$SHIM_DIR"
  cat > "$SHIM_DIR/chromium" <<EOF
#!/bin/sh
# --no-sandbox: this Chromium only ever renders our own local terminal page,
# and unprivileged user namespaces are AppArmor-restricted on modern Ubuntu.
exec "$GC_DEMO_CHROMIUM" --no-sandbox --disable-dev-shm-usage --disable-gpu "\$@"
EOF
  chmod +x "$SHIM_DIR/chromium"
  export PATH="$SHIM_DIR:$PATH"
  export ROD_BIN="$SHIM_DIR/chromium"
fi

command -v vhs >/dev/null || { echo "record.sh: vhs not found on PATH" >&2; exit 1; }

# Tapes call `gc` and `gc-prepare`; both resolve to this checkout.
SHIM_DIR="$WORK_DIR/bin"
mkdir -p "$SHIM_DIR"
cat > "$SHIM_DIR/gc" <<EOF
#!/bin/sh
exec "$VENV/bin/gc" "\$@"
EOF
cat > "$SHIM_DIR/gc-prepare" <<EOF
#!/bin/sh
# Writes the throwaway config for the current recording. XDG_CONFIG_HOME is
# already redirected by record.sh, so this cannot touch a real config.
exec "$VENV/bin/python" "$DEMO_DIR/prepare_config.py" "\$@"
EOF
chmod +x "$SHIM_DIR/gc" "$SHIM_DIR/gc-prepare"
export PATH="$SHIM_DIR:$PATH"

# Every recording gets a fresh config tree. Nothing under the user's real
# ~/.config is read or written for the whole run.
export XDG_CONFIG_HOME="$WORK_DIR/config"
export XDG_STATE_HOME="$WORK_DIR/state"
export XDG_CACHE_HOME="$WORK_DIR/cache"

# Textual honours COLUMNS/LINES over the pty in some paths; let ttyd decide.
unset COLUMNS LINES

# --------------------------------------------------------------------------- #
# Background load
# --------------------------------------------------------------------------- #

LOAD_PID=""
cleanup() {
  if [[ -n "$LOAD_PID" ]] && kill -0 "$LOAD_PID" 2>/dev/null; then
    kill -TERM "$LOAD_PID" 2>/dev/null || true
    wait "$LOAD_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ $WITH_LOAD -eq 1 ]]; then
  echo "==> starting load generator (profile=$PROFILE)"
  # These numbers exist to leave the capture pipeline enough headroom. vhs does
  # not slow down when it cannot keep up -- it drops frames and writes the
  # survivors at a fixed delay, so the GIF comes out short and plays fast. The
  # check after each render catches that; if it fires, turn these down.
  #
  # CPU is held lower than the rest for a different reason: it is what pushes
  # the motherboard sensor towards its own 80C warning line, and a recording in
  # which every panel is alerting demonstrates nothing.
  #
  # Do not run two of these at once. Two concurrent renders halve each other's
  # capture rate, and the symptom -- every GIF exactly half its intended length
  # -- looks convincingly like a load problem instead of what it is.
  "$PYTHON" "$DEMO_DIR/loadgen.py" --profile "$PROFILE" \
    --workdir "$WORK_DIR/load" --intensity "0.8,cpu=0.55" \
    --max-mem-gb 12 --vram-gb 30 --gpu-workers 3 &
  LOAD_PID=$!
  # Let the plots fill with history before the first frame is captured;
  # otherwise every GIF opens on an empty chart that fills in as it plays.
  echo "==> priming metric history (25s)"
  sleep 25
fi

# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #

# Sum of a tape's visible Sleeps: what the GIF *should* last. vhs drops capture
# frames when the machine cannot keep up and writes the survivors at a fixed
# delay anyway, so a dropped frame does not stretch the GIF -- it makes it play
# fast, silently. Comparing the two catches that.
expected_seconds() {
  awk '
    /^[[:space:]]*Hide[[:space:]]*$/ { visible = 0; next }
    /^[[:space:]]*Show[[:space:]]*$/ { visible = 1; next }
    !visible && NR > 1 { next }
    /^[[:space:]]*Sleep/ {
      if (!visible && NR > 1) next
      v = $2
      if (v ~ /ms$/)     { sub(/ms$/, "", v); total += v / 1000 }
      else if (v ~ /s$/) { sub(/s$/,  "", v); total += v }
      else               { total += v }
    }
    END { printf "%.1f", total }
  ' visible=1 "$1"
}

failed=()
short=()
for name in "${TAPES[@]}"; do
  tape="$TAPE_DIR/$name.tape"
  if [[ ! -f $tape ]]; then
    echo "record.sh: no such tape: $name" >&2
    failed+=("$name")
    continue
  fi
  echo "==> $name"
  # cwd is the repo root so a tape's `Source demo/tapes/themes/x.tape` and
  # `Output assets/x.gif` both resolve.
  if ! (cd "$REPO_ROOT" && vhs "$tape"); then
    failed+=("$name")
    continue
  fi

  gif="$OUT_DIR/$name.gif"

  # A README that loads 13 animations wants them small. Terminal frames are
  # mostly flat colour, so this is worth 30-40% for no visible change; skipped
  # silently when gifsicle is not installed.
  if [[ $OPTIMIZE -eq 1 ]] && command -v gifsicle >/dev/null && [[ -f $gif ]]; then
    before=$(stat -c %s "$gif")
    if gifsicle -O3 --lossy=80 "$gif" -o "$gif.opt" 2>/dev/null; then
      # Only keep it if it actually helped: gifsicle can grow an already
      # well-packed file.
      if [[ $(stat -c %s "$gif.opt") -lt $before ]]; then
        mv "$gif.opt" "$gif"
      else
        rm -f "$gif.opt"
      fi
    fi
    rm -f "$gif.opt"
  fi

  if command -v ffprobe >/dev/null && [[ -f $gif ]]; then
    actual=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$gif")
    want=$(expected_seconds "$tape")

    # 15% is slack for typing time and startup; beyond that, frames were lost.
    if awk -v a="$actual" -v w="$want" 'BEGIN { exit !(w > 0 && a < w * 0.85) }'; then
      # Stretch the surviving frames back to the intended length. The lost
      # frames are gone either way; this at least stops the GIF from racing
      # through content the tape meant to dwell on.
      if [[ $RETIME -eq 1 ]] && "$PYTHON" "$DEMO_DIR/retime_gif.py" "$gif" "$want" \
           | sed 's/^/    retimed: /'; then
        actual=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$gif")
      else
        short+=("$name")
      fi
    fi

    printf '    %.1fs (tape asks for ~%ss, %s)\n' "$actual" "$want" \
      "$(du -h "$gif" | cut -f1)"
  fi
done

if [[ ${#short[@]} -gt 0 ]]; then
  echo "==> WARNING: these came out short, so they play too fast: ${short[*]}" >&2
  echo "    vhs dropped capture frames and retiming did not run or failed." >&2
  echo "    Shrink the canvas, lower the load (--profile calm), or space the" >&2
  echo "    keystrokes further apart." >&2
fi
if [[ ${#failed[@]} -gt 0 ]]; then
  echo "==> FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "==> done; GIFs are in $OUT_DIR"
