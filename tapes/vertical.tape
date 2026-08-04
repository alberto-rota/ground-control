# Ground Control - vertical layout
# Regenerate from the repository root:  vhs tapes/vertical.tape
# Conventions and requirements: tapes/README.md

Output assets/vertical.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 13
Set Width 860
Set Height 1000
Set Padding 10
Set Framerate 12

Require gc

Env XDG_CONFIG_HOME "/tmp/gc-vhs-vertical"

Hide
Type@5ms "rm -rf /tmp/gc-vhs-vertical && clear"
Enter
Type@5ms "for i in $(seq 48); do timeout 60 yes > /dev/null & done"
Enter
Type@5ms "clear"
Enter
Type@5ms "gc --cpu --ram --net"
Enter
Sleep 6s
Type "a"
Sleep 14s
Show

Sleep 3s
Type "v"
Sleep 12s

Hide
Type "q"
Sleep 1s
Type@5ms "pkill -u $USER -x yes"
Enter
Sleep 1s
