# Memory: RAM above the axis, SWAP below, and the centre bar they meet at.
Output assets/memory.gif

Source demo/tapes/themes/rose-pine.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 15
Set Width 1320
Set Height 520
Set Padding 12
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme rose-pine --layout horizontal --refresh 1 --history 120"
Enter
Sleep 2s
Type "clear"
Enter
Show

Type "gc --ram"
Enter
Sleep 3s

Hide
Sleep 122s
Show

# The generator breathes several GB in and out on a slow envelope, so the RAM
# trace climbs and falls instead of sitting on one level.
Sleep 18s

Hide
Type "q"
Sleep 1s
