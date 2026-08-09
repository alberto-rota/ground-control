# Demo recordings

Everything needed to regenerate the GIFs in the main README, so they can be
refreshed after a UI change instead of being re-shot by hand.

```sh
demo/record.sh              # render every tape into assets/
demo/record.sh cpu gpu      # just those two
demo/record.sh --list       # what tapes exist
demo/record.sh --no-load    # skip the synthetic load
```

## What is here

| File | Purpose |
| --- | --- |
| `tapes/*.tape` | One [VHS](https://github.com/charmbracelet/vhs) tape per GIF |
| `tapes/themes/*.tape` | Generated terminal-colour fragments, one per app theme |
| `record.sh` | Sets up the environment, starts the load, renders tapes |
| `loadgen.py` | Synthetic CPU / memory / disk / network / GPU load |
| `gpu_wave.cu` | The GPU half of it, compiled on demand by `loadgen.py` |
| `prepare_config.py` | Writes each recording's throwaway config |
| `gen_theme_tapes.py` | Regenerates `tapes/themes/` from `ground_control/themes/` |
| `retime_gif.py` | Stretches a GIF that lost frames back to its intended length |

## Requirements

- **vhs**, plus the `ttyd` and `ffmpeg` it shells out to (`brew install vhs`).
- **A Nerd Font.** The tapes ask for *JetBrainsMono Nerd Font Mono*. Without it
  the powerline tips on every gauge bar render as tofu.
- **A Chromium binary.** vhs drives a headless browser, and its downloader has
  no build for every platform (notably Linux arm64). Point it at one:

  ```sh
  export GC_DEMO_CHROMIUM=/path/to/chrome    # record.sh wraps it for you
  ```

  A userspace copy with no root needed:
  `pip install playwright && playwright install chromium`.
- **nvcc**, optionally. Without it the GPU generator is skipped and the GPU
  panel records idle.

## Why there is a load generator

An idle machine makes a dull recording: flat lines, empty bars, `0%` on
everything. `loadgen.py` drives every metric the TUI reads with signals built to
look like real work rather than a test pattern — two sine waves whose periods
are deliberately non-harmonic (23 s and 7.3 s), plus noise and decaying spikes,
so nothing repeats inside a 20-second GIF.

Two details in there are worth knowing before you change them:

- **`PHASE_SPREAD`** controls how far the per-core CPU phases are spread. At
  `1.0` the heatmap ripples beautifully and the *aggregate* CPU trace flattens
  to a straight line, because 20 evenly-spread sines cancel. At `0.0` the
  aggregate has full amplitude and the heatmap has no pattern. It sits at `0.5`.
- **The disk generator drops its own page cache** between the write and read
  passes (`posix_fadvise(DONTNEED)`). Reading back a file you just wrote is
  served from cache and produces exactly zero disk reads, so without this the
  read half of the plot stays empty.

Network traffic goes over loopback, where every byte counts once as sent and
once as received — so upload and download necessarily mirror each other. Real
traffic the machine is already doing rides on top.

## Why each tape prepares its own config

`record.sh` redirects `XDG_CONFIG_HOME` for the whole run, and every tape calls
`gc-prepare` to write a fresh `config.json` with the theme, layout, refresh rate
and thresholds it wants. Your real `~/.config/ground-control` is never read or
written. (`prepare_config.py` refuses to run at all without
`XDG_CONFIG_HOME` set, so this cannot go wrong by accident.)

The same script emits the VHS `Set Theme` block from the app's own theme JSON,
which is what `gen_theme_tapes.py` bakes into `tapes/themes/`. That is why the
terminal background matches the TUI exactly instead of nearly — picking the
closest-named VHS built-in leaves a visible seam at the edge of the frame.

## One palette for the whole README

Every tape records on **gruvbox**, in both halves of the frame: `Source
demo/tapes/themes/gruvbox.tape` sets the terminal's colours and `gc-prepare
--theme gruvbox` sets the app's, and both come from the same
`ground_control/themes/gruvbox.json`. The tapes used to carry a theme each,
which showed off the palettes but meant a reader scrolling the README met a
different colour scheme every two paragraphs — the individual GIFs looked fine
and the page did not.

`themes.tape` is the deliberate exception: its subject *is* the other palettes,
so it starts on gruvbox and cycles away from it. It loops back on its own.

## Why every tape passes `--no-alerts`

Threshold alerts paint a panel's border and prefix its title with `▲`/`■`. They
are a real feature, but the main README does not explain them, so an orange
panel in a recording reads as a bug rather than as a warning.

They also fire for a reason that is an artifact of the rig: the load generator
drives this machine hard enough that the motherboard sensor crosses the shipped
80 °C `temperature_c` threshold, which no idle reader's machine would. So every
tape turns alerting off — except the two whose subject it is. `alerts.tape` and
`snapshot.tape` pass `--alerts-tuned` instead, which lowers the thresholds far
enough that the demo load reliably breaches them (on a healthy machine the
shipped defaults are never crossed, which would make `snapshot.tape` a demo of
`--check` that only ever prints `0`).

## Resolution, and the one rule about geometry

`Width` and `Height` are pixels, but what the app actually lays out against is
the terminal's **cols × rows**, and that is `(Width - 2·Padding) / cellWidth`.
Cell size is a function of `FontSize`. So the rule when resizing a tape is:

> multiply `FontSize`, `Width`, `Height` and `Padding` by the *same* factor.

That buys pixels without changing what fits on screen. Scaling the canvas alone
shrinks the text and adds columns; scaling the font alone drops columns until a
panel gives up and prints `too small`. The current tapes are a 1.5× pass over
the originals (font 14 → 21 for the grid tapes, 15 → 22 for the single-panel
ones), verified by probe: font 14 @1500×900 and font 21 @2250×1350 both give
48 rows.

More pixels per frame is also more work per captured frame, which is the main
way a GIF ends up short — see below. If you scale up much further, expect to
trade frame rate for it.

## Recording inside a Slurm job

A login node has no GPUs and nothing interesting to plot, so the GPU and
all-panels recordings are worth re-shooting on a compute node. `srun --overlap`
joins an **existing** allocation, so the app lands inside the job's cgroup and
sees the job's own CPUs, memory and GPUs — the same mechanism as the app's `F`
job focus:

```sh
srun --overlap --jobid=$(squeue -h -u $USER -t R -o %i | head -n1) \
     --pty -n1 bash -lc 'gc'
```

To put that on film, replace the `Type "gc"` line of `hero.tape` or `gpu.tape`
with the `srun` line above and lengthen the `Sleep` that follows it — a job step
has to be created before `gc` starts, which the local tapes do not pay for. Pick
your *first running job* carefully: the tapes take whatever `squeue` lists first,
and an idle GPU plots as a flat line at zero.

## The hidden warm-up

Each tape starts the app, then sleeps for the full history window behind a
`Hide` before showing anything:

```
Hide
Type "gc"
Enter
Sleep 122s
Show
```

Those frames are not captured. Without it every GIF opens on a run of zeros on
the left of each plot while the history deque fills — an artifact that reads
like a bug in the app.

122 seconds because the history window is two minutes. Note the tapes pass
`--refresh 1 --history 120` to `gc-prepare` rather than something snappier:
the app does not currently honour `refresh_rate` or `history_size` from
config.json (the Settings dropdowns are built before the config loads, and
their initial `Changed` message overwrites the restored values), so asking for
anything else would only put a number on screen that contradicts what the app
is doing. If that gets fixed, drop these to `0.5` / `60` — the plots move twice
as much and the warm-up halves.

## When a GIF comes out short

`record.sh` compares each GIF's length against the sum of its tape's visible
`Sleep`s. They should match within about 15%.

If one is much shorter, vhs dropped capture frames — it does not wait for a
screen it cannot keep up with, it skips ahead and writes the survivors at a
fixed delay, so a short GIF is a *fast* GIF, not a truncated one. Causes, in
the order they have actually bitten:

1. **Two renders at once.** Every GIF comes out at almost exactly half length.
   `record.sh` now takes a lock to prevent this.
2. **A genuinely expensive tape.** `themes.tape` is the standing example: each
   theme change repaints every cell, and the app is busy long enough that vhs
   gets nothing. Nothing recovers those frames, so `record.sh` falls back to
   `retime_gif.py`, which scales the surviving frames' delays back out to the
   intended length — a lower frame rate, but the right pacing.
3. **Too much load.** Turn down `--intensity` in `record.sh`, or record with
   `--profile calm`.

## Adding a tape

1. Copy the closest existing tape and change its `Output` and the flags on its
   `gc-prepare` line. Leave the theme alone: gruvbox in both halves of the
   frame is the house style, and `tapes/themes/` exists for the one tape that
   needs to move off it.
2. Panel-scoped keys need the panel focused first — press `]`. On CPU those are
   `1`/`2`/`3`; on GPU, `1` and `2`. (The in-app help offers `p` for the GPU
   Processes tab, but the widget binds `p` to `show_plot`, so it does nothing
   useful — use `2`.)
3. Settings navigation is by focus order: `s` lands on the widget list, and six
   `Tab`s reach the colour list.
4. `record.sh` picks up new tapes automatically; files under `tapes/themes/` are
   fragments and are skipped.
