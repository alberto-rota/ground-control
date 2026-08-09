# The colour picker: swatch grid, HSV steppers, and a live preview panel.
Output assets/colorpicker.gif

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

Sleep 2s

# s opens Settings with the widget list focused; six tabs reach the colour
# list. (Focus order: widgets, layout, refresh, history, disk filter, themes,
# colours.)
Type "s"
Sleep 2s
Tab
Tab
Tab
Tab
Tab
Tab
Sleep 1s

# Walk down into the CPU group and stop on cpu_bar -- recolouring it repaints
# a whole block of the preview, which is easier to see than a plot line.
Down 10
Sleep 2s

Enter
Sleep 3s

# Tab moves from the picker's own key list to the swatch grid. Every move
# applies immediately: the preview panel on the right is a real CPU widget
# being fed real metrics, not a mock-up.
#
# The grid opens with its cursor on the swatch nearest the *current* colour,
# not at a fixed corner. The grid's hue rows are all at HSL saturation 0.85 and
# gruvbox's cpu_bar (#83A598) is nowhere near that saturated, so the nearest
# swatch is in the greyscale row along the bottom. Hence moving up first: the
# moves below are relative, and going right from there would only walk into
# white.
Tab
Sleep 1s
Up 3
Sleep 2s
Right 3
Sleep 2s
Down 1
Sleep 2s
Left 6
Sleep 2s
Up 2
Sleep 2s

# Then the H/S/V steppers, for the shade the grid does not have.
Tab
Sleep 1s
Right 12
Sleep 2s
Down 1
Right 8
Sleep 2s

# Revert restores the value the key had when the screen opened.
Ctrl+z
Sleep 3s
Escape
Sleep 2s

Hide
Type "q"
Sleep 1s
