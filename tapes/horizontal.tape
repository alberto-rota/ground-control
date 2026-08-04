# Ground Control - horizontal layout
# Regenerate from the repository root:  vhs tapes/horizontal.tape
# Conventions and requirements: tapes/README.md

Output assets/horizontal.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 13
Set Width 1800
Set Height 540
Set Padding 10
Set Framerate 12

Require gc

Env XDG_CONFIG_HOME "/tmp/gc-vhs-horizontal"

Hide
Type@5ms "rm -rf /tmp/gc-vhs-horizontal && clear"
Enter
Type@5ms "for i in $(seq 48); do timeout 60 yes > /dev/null & done"
Enter
Type@5ms "clear"
Enter
# Four panels: a single row is what this layout is for, and every panel still
# gets enough columns to draw its plot.
Type@5ms "gc --cpu --ram --temp --net"
Enter
Sleep 6s
Type "a"
Sleep 14s
Show

# The layout switch is the point of this one, so it happens on camera.
Sleep 3s
Type "h"
Sleep 12s

Hide
Type "q"
Sleep 1s
Type@5ms "pkill -u $USER -x yes"
Enter
Sleep 1s
