# Ground Control - Disk panels
# Regenerate from the repository root:  vhs tapes/disk.tape
# Conventions and requirements: tapes/README.md

Output assets/disk.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 13
Set Width 1500
Set Height 800
Set Padding 10
Set Framerate 12

Require gc

Env XDG_CONFIG_HOME "/tmp/gc-vhs-disk"

Hide
Type@5ms "rm -rf /tmp/gc-vhs-disk /tmp/gc-vhs-io && clear"
Enter
# Read/write throughput is a delta, so the panel needs traffic to draw. Direct
# I/O keeps it off the page cache, which is what makes the plot move at all.
Type@5ms "timeout 55 bash -c 'while :; do dd if=/dev/zero of=/tmp/gc-vhs-io bs=1M count=400 oflag=direct status=none; dd if=/tmp/gc-vhs-io of=/dev/null bs=1M iflag=direct status=none; done' &"
Enter
Type@5ms "clear"
Enter
Type@5ms "gc --disk"
Enter
Sleep 6s
Type "a"
Sleep 14s
Show

Sleep 14s

Hide
Type "q"
Sleep 1s
Type@5ms "rm -f /tmp/gc-vhs-io"
Enter
Sleep 1s
