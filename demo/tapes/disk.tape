# Disk: read above the axis, write below, capacity bar underneath.
Output assets/disk.gif

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

Type "gc --disk"
Enter
Sleep 3s

Hide
Sleep 122s
Show

# The generator alternates write and read passes, dropping its own page cache
# between them so the reads are real block-layer I/O -- which is why the two
# halves of the plot take turns.
Sleep 18s

Hide
Type "q"
Sleep 1s
