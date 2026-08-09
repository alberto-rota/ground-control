# t cycles the 20 built-in themes, repainting plots and bars live.
Output assets/themes.gif

# Padding 0 on purpose: the app repaints its own background as the theme
# changes, but VHS's padding is painted by the terminal and would stay at
# whatever this tape set -- a mismatched frame around every other theme.
Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 21
# Smaller canvas than the other grid tapes on purpose: a theme change repaints
# every cell on screen, and vhs silently drops capture frames it cannot keep up
# with -- which does not stretch the GIF, it makes it play fast. Fewer pixels
# per frame keeps the timing honest.
Set Width 2040
Set Height 1200
Set Padding 0
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

# Themes are cycled in alphabetical order, so starting from gruvbox this run
# covers gruvbox-light, kanagawa, light, light-pastel, monokai, neon and nord
# -- a mix of dark and light palettes. It ends away from gruvbox on purpose:
# this is the one recording whose subject is the *other* palettes, and the GIF
# loops back to gruvbox on its own.
#
# 3s apart, not 2s: each change raises a toast naming the theme, and they stack
# in the bottom-right corner. Any faster and four of them are on screen at once,
# covering the GPU panel.
Sleep 3s
Type "t"
Sleep 3s
Type "t"
Sleep 3s
Type "t"
Sleep 3s
Type "t"
Sleep 3s
Type "t"
Sleep 3s
Type "t"
Sleep 3s
Type "t"
Sleep 4s

Hide
Type "q"
Sleep 1s
