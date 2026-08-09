# The colour picker: swatch grid, HSV steppers, and a live preview panel.
Output assets/colorpicker.gif

Source demo/tapes/themes/everforest.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 14
Set Width 1500
Set Height 900
Set Padding 12
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme everforest --layout grid --refresh 1 --history 120"
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
# not at a fixed corner -- for everforest's cpu_bar that is the greyscale row
# along the bottom. Hence moving up first: the moves below are relative, and
# going right from there would only walk into white.
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
