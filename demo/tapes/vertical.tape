# Vertical layout, in the narrow split it is actually meant for.
Output assets/vertical.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 20
# Tall and narrow, i.e. one side of a vertical tmux split. Six panels in a
# column need the height: in a landscape terminal they get ~7 rows each and the
# plots collapse, which is what layouts.tape avoids showing.
Set Width 1324
Set Height 1846
Set Padding 18
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme gruvbox --layout vertical --refresh 1 --history 120 --no-alerts"
Enter
Sleep 2s
Type "clear"
Enter
Type "gc"
Enter
Sleep 122s
Show

Sleep 18s

Hide
Type "q"
Sleep 1s
