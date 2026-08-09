# Ground Control - the full dashboard (README banner + grid layout)
# Regenerate from the repository root:  vhs tapes/dashboard.tape
#
# Recorded *inside a running Slurm job* rather than on the login node, because a
# login node has no GPUs and nothing to plot: `srun --overlap` joins the existing
# allocation, so this is the job's own CPU, memory and GPU load. See
# tapes/README.md for what to do when you have no job to attach to.

Output assets/dashboard.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 13
Set Width 1600
Set Height 1000
Set Padding 10
Set Framerate 10

Require gc
Require srun

Env XDG_CONFIG_HOME "/tmp/gc-vhs-dashboard"

Hide
Type@5ms "clear"
Enter
# First running job of the current user; --pty because Textual needs a terminal.
Type@5ms "srun --overlap --jobid=$(squeue -h -u $USER -t R -o %i | head -n1) --pty -n1 bash -lc 'rm -rf $XDG_CONFIG_HOME; gc'"
Enter
# Longer than the local tapes: a job step has to be created before gc starts.
Sleep 14s
# Alerts off. A job sitting close to its memory limit is legitimately critical,
# but a red border the README never explains reads as a bug.
Type "a"
Sleep 16s
Show

Sleep 14s

Hide
Type "q"
Sleep 2s
