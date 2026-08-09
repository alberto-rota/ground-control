# g / h / v: the three layout modes, on live data.
Output assets/layouts.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 21
Set Width 2250
Set Height 1350
Set Padding 18
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme gruvbox --layout grid --refresh 1 --history 120 --no-alerts"
Enter
Sleep 2s
Type "clear"
Enter
Type "gc"
Enter
Sleep 122s
Show

# Grid, then a single row, then back. Each gets long enough to read the panels,
# not just see them flash past.
#
# Vertical is not in here on purpose: six panels stacked in a landscape terminal
# get about seven rows each, which is small enough that the CPU plot gives up and
# prints "too small". That is honest behaviour but it misrepresents the layout --
# vertical is for a narrow split, and vertical.tape records it in one.
Sleep 5s
Type "h"
Sleep 8s
Type "g"
Sleep 6s

Hide
Type "q"
Sleep 1s
