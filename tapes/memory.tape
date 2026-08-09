# Ground Control - Memory panel
# Regenerate from the repository root:  vhs tapes/memory.tape
# Conventions and requirements: tapes/README.md

Output assets/memory.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 15
Set Width 1040
Set Height 520
Set Padding 10
Set Framerate 12

Require gc

Env XDG_CONFIG_HOME "/tmp/gc-vhs-memory"

Hide
Type@5ms "rm -rf /tmp/gc-vhs-memory && clear"
Enter
# Steady memory records as a straight line pinned to the top of the plot, which
# reads as a broken panel. This ramps ~30 GB in and releases it again, so the
# take shows the shape the panel is actually for. `timeout` bounds the damage.
Type@5ms "timeout 45 python3 -c 'import time; b = [(bytearray(2 * 1024 ** 3), time.sleep(0.7)) for _ in range(15)]; time.sleep(4)' &"
Enter
Type@5ms "clear"
Enter
Type@5ms "gc --ram"
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
