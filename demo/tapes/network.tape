# Network: upload and download rates meeting at a centre bar, the summary
# line under it, then the Links and Ports tabs.
Output assets/network.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 11
Set Width 968
Set Height 382
Set Padding 9
Set BorderRadius 5
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme gruvbox --layout horizontal --refresh 1 --history 120 --no-alerts"
Enter
Sleep 2s
Type "clear"
Enter
Show

Type "gc --net"
Enter
Sleep 3s

Hide
Sleep 122s
Show

Sleep 10s

# Panel-scoped keys need the panel focused first. 2 is the interfaces, 3 the
# listening ports, 1 back to the plot so the loop starts where it ends.
Type "]"
Sleep 1s
Type "2"
Sleep 6s
Type "3"
Sleep 8s
Type "1"
Sleep 3s

Hide
Type "q"
Sleep 1s
