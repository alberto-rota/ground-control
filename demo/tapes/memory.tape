# Memory: RAM above the axis, SWAP below, and the centre bar they meet at.
Output assets/memory.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 22
Set Width 1936
Set Height 764
Set Padding 18
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme gruvbox --layout horizontal --refresh 1 --history 120 --no-alerts"
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
