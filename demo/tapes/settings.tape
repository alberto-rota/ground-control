# The Settings tab: which panels show, and which layout they show in.
Output assets/settings.gif

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

Sleep 3s

# s jumps straight to the widget list, so toggling a panel is two keys.
Type "s"
Sleep 2s
Down 2
Sleep 1s
Space
Sleep 2s
Down 1
Space
Sleep 2s

# Back to the dashboard to show the grid reflow, then return and put the
# panels back.
Type "d"
Sleep 4s
Type "s"
Sleep 2s
Space
Sleep 1s
Up 1
Space
Sleep 2s

# One tab reaches the Layout radio, where the arrow keys switch layout and the
# dashboard behind follows immediately.
#
# The refresh-rate and history dropdowns are deliberately left alone: driving a
# Select blind takes an arrow count that depends on where the highlight starts,
# and getting it wrong parks the recording on "1m" -- a dashboard that visibly
# stops updating. Those two are covered by + and - in the shortcuts table.
Tab
Sleep 1s
Right 1
Sleep 2s
Right 1
Sleep 2s
Left 2
Sleep 2s

# A layout round-trip remounts every panel, so the dashboard comes back empty
# and takes a couple of ticks to repopulate. The long dwell is so the GIF ends
# -- and, since it loops, begins -- on a full dashboard rather than on that gap.
Type "d"
Sleep 10s

Hide
Type "q"
Sleep 1s
