# Bento: span a panel over more rows / columns, change the column count, reset.
Output assets/bento.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 10
Set Width 1126
Set Height 676
Set Padding 9
Set BorderRadius 5
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme gruvbox --layout bento --refresh 1 --history 120 --no-alerts"
Enter
Sleep 2s
Type "clear"
Enter
Type "gc"
Enter
Sleep 122s
Show

# The default spans: CPU two columns wide, the GPU two by two.
Sleep 4s

# vhs 0.11 has no Shift+<arrow>, so the span keys are sent as the xterm
# sequence they produce: ESC [ 1 ; 2 <A-D>, which Textual parses as one key
# (shift+down, shift+right). See demo/README.md, "Adding a tape".
#
# ] focuses the CPU panel; shift+down makes it two rows tall.
Type "]"
Sleep 1s
Escape
Type@0ms "[1;2B"
Sleep 4s

# ] moves on to Memory; shift+right makes it two columns wide.
Type "]"
Sleep 1s
Escape
Type@0ms "[1;2C"
Sleep 4s

# > and < change how many columns the bento has; the panels repack.
Type ">"
Sleep 4s
Type "<"
Sleep 3s

# z puts the spans back, so the GIF loops onto its own first frame.
Type "z"
Sleep 4s

Hide
Type "q"
Sleep 1s
