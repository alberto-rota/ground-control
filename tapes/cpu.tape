# Ground Control - CPU panel
# Regenerate from the repository root:  vhs tapes/cpu.tape
# Conventions and requirements: tapes/README.md

Output assets/cpu.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 15
Set Width 1040
Set Height 520
Set Padding 10
Set Framerate 12

Require gc

# A throwaway config dir keeps the recording independent of whoever runs it:
# palette, refresh rate and thresholds are the shipped defaults, not local taste.
Env XDG_CONFIG_HOME "/tmp/gc-vhs-cpu"

Hide
# Wiped every run so the keystrokes below always start from the same state.
Type@5ms "rm -rf /tmp/gc-vhs-cpu && clear"
Enter
# An idle machine records as a flat line, so put some cores to work for the
# length of the take. `timeout` is the leash: nothing outlives the recording.
Type@5ms "for i in $(seq 48); do timeout 60 yes > /dev/null & done"
Enter
Type@5ms "clear"
Enter
Type@5ms "gc --cpu"
Enter
Sleep 6s
# One panel fills the frame in the single-row layout; in grid it would sit in
# the middle of an otherwise empty screen.
Type "h"
# Alerts off: a threshold marker the README never explains reads as a bug.
Type "a"
Sleep 14s
Show

Sleep 14s

Hide
Type "q"
Sleep 1s
Type@5ms "pkill -u $USER -x yes"
Enter
Sleep 1s
