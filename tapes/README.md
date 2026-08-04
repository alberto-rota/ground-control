# Recording the README animations

Every image in the project README is a GIF recorded with [vhs](https://github.com/charmbracelet/vhs).
One tape produces one asset:

| Tape | Asset | Recorded on |
|------|-------|-------------|
| `dashboard.tape` | `assets/dashboard.gif` | inside a running Slurm job |
| `gpu.tape` | `assets/gpu.gif` | inside a running Slurm job |
| `cpu.tape` | `assets/cpu.gif` | local, synthetic CPU load |
| `memory.tape` | `assets/memory.gif` | local, synthetic allocation |
| `temperature.tape` | `assets/temperature.gif` | local, synthetic CPU load |
| `disk.tape` | `assets/disk.gif` | local, synthetic direct I/O |
| `network.tape` | `assets/network.gif` | local, whatever traffic the host has |
| `horizontal.tape` | `assets/horizontal.gif` | local |
| `vertical.tape` | `assets/vertical.gif` | local |
| `settings.tape` | `assets/settings.gif` | local |

Run them **from the repository root**, since every `Output` path is relative to it:

```sh
vhs tapes/cpu.tape          # one asset
for t in tapes/*.tape; do vhs "$t"; done   # all of them
```

## Requirements

- `vhs`, plus its own dependencies `ttyd` and `ffmpeg`. Standalone binaries are
  enough — `brew install vhs` also works but pulls in a large dependency tree.
- **A Nerd Font, installed on the machine that runs vhs.** The tapes ask for
  `JetBrainsMono Nerd Font Mono`. Without it the gauge tips (``, ``) that every
  bar in the app ends with record as tofu boxes. vhs renders through a headless
  Chromium, so this is a *host* font, not a font in your own terminal.
- `gc` on `PATH` (`pip install -e .`), and `srun` for the two job tapes.
- Chromium may need `--no-sandbox` on hosts without unprivileged user
  namespaces; vhs downloads its own browser under `~/.cache/rod`.

## Conventions the tapes share

**A throwaway config dir.** Each tape sets `XDG_CONFIG_HOME` to its own
`/tmp/gc-vhs-*` directory and deletes it before launching, so recordings show the
shipped defaults (palette, refresh rate, thresholds) and not the local config of
whoever is recording — and so `gc` writing its config on quit cannot make the
next run differ from this one. On macOS `platformdirs` ignores
`XDG_CONFIG_HOME`, so recordings there use the real config file.

**Alerts off.** Every tape presses `a` while hidden. Threshold borders and `▲`/`■`
markers are a real feature, but the README does not explain them, so a red panel
in a screenshot reads as a bug rather than as a warning.

**Layout by keystroke.** Single-panel tapes press `h`: in grid mode a lone panel
sits in the middle of an otherwise empty screen, while the single-row layout lets
it fill the frame.

**Synthetic load, on a leash.** An idle machine records as a flat line, so most
tapes generate a little load — `yes` processes, a `bytearray` ramp, `dd` with
`oflag=direct`. Everything is wrapped in `timeout` and killed after the take.
Nothing outlives the recording. Be aware this is deliberately *some* load on
whatever machine you record from; the counts are sized for a large HPC node, so
scale them down on a laptop.

**Everything before the first frame is hidden.** `Hide` … `Show` keeps the setup
commands, the `srun` line and the app's first empty ticks out of the GIF, so the
recording opens on a dashboard that already has plot history.

## The two job tapes

A login node has no GPUs, so `dashboard.tape` and `gpu.tape` do not record
locally. They run

```sh
srun --overlap --jobid=$(squeue -h -u $USER -t R -o %i | head -n1) --pty -n1 bash -lc 'gc'
```

`--overlap` joins an **existing** allocation, so `gc` lands inside the job's
cgroup and sees the job's own CPUs, memory and GPUs — the same mechanism as the
app's own `F` job focus. The tapes pick your *first running job*, so make sure
that job is the one you want on camera (and that it is doing something: an idle
GPU plots as a flat line at 0). With no job at all, `squeue` returns nothing and
`srun` fails; start an interactive allocation first, or record these two on a
workstation that has a GPU.

## Adjusting geometry

`Set Width`/`Set Height` are **pixels**, and they only take effect if every `Set`
comes before the first real command — vhs builds the terminal when it hits that
command, and silently ignores geometry set afterwards. Panels drop content they
cannot fit (the CPU panel prints `too small` before it will squeeze a plot), so
after changing a size, look at the result:

```sh
ffmpeg -sseof -1 -i assets/cpu.gif -vframes 1 /tmp/last-frame.png
```
