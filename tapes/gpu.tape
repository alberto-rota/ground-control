# Ground Control - GPU panels
# Regenerate from the repository root:  vhs tapes/gpu.tape
#
# Recorded inside a running Slurm job (see dashboard.tape for why). The
# GPU shown is the first one the job holds, with the job's own load on it.

Output assets/gpu.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 15
Set Width 1040
Set Height 520
Set Padding 10
Set Framerate 12

Require gc
Require srun

Env XDG_CONFIG_HOME "/tmp/gc-vhs-gpu"

Hide
Type@5ms "clear"
Enter
Type@5ms "srun --overlap --jobid=$(squeue -h -u $USER -t R -o %i | head -n1) --pty -n1 bash -lc 'rm -rf $XDG_CONFIG_HOME; gc -g --gpu-index 0'"
Enter
Sleep 14s
Type "h"
Type "a"
Sleep 16s
Show

Sleep 14s

Hide
Type "q"
Sleep 2s
