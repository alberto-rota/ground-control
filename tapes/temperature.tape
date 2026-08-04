# Ground Control - Temperature panel
# Regenerate from the repository root:  vhs tapes/temperature.tape
# Conventions and requirements: tapes/README.md

Output assets/temperature.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 15
Set Width 1040
Set Height 520
Set Padding 10
Set Framerate 12

Require gc

Env XDG_CONFIG_HOME "/tmp/gc-vhs-temperature"

Hide
Type@5ms "rm -rf /tmp/gc-vhs-temperature && clear"
Enter
# The load is here for the same reason as in cpu.tape, but for a different
# reading: package temperature only moves once the cores have something to do.
Type@5ms "for i in $(seq 48); do timeout 60 yes > /dev/null & done"
Enter
Type@5ms "clear"
Enter
Type@5ms "gc --temp"
Enter
Sleep 6s
Type "h"
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
