# Ground Control - Network panel
# Regenerate from the repository root:  vhs tapes/network.tape
# Conventions and requirements: tapes/README.md

Output assets/network.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 15
Set Width 1040
Set Height 520
Set Padding 10
Set Framerate 12

Require gc

Env XDG_CONFIG_HOME "/tmp/gc-vhs-network"

Hide
Type@5ms "rm -rf /tmp/gc-vhs-network && clear"
Enter
Type@5ms "gc --net"
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
