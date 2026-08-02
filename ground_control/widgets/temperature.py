import logging
from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import align, ansi2rich, recolor, substitute_plot_timeframe
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.temperature")


class TemperatureWidget(MetricWidget):
    """Widget for system temperature monitoring."""

    # Sensor bars sit under the plot, one row per plotted sensor. The plot keeps at
    # least PLOT_MIN_ROWS rows; whatever the bars cannot get, they do not show.
    MAX_PLOT_SENSORS = 4
    PLOT_MIN_ROWS = 6
    # Narrowest bar row that still fits a name, a reading and some bar.
    BARS_MIN_WIDTH = 9

    DEFAULT_CSS = """
    TemperatureWidget {
        layout: vertical;
    }
    TemperatureWidget #temp-bars {
        width: 1fr;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    """

    def __init__(self, title: str, id: str = None, history_size: int = 120):
        super().__init__(title=title, color=get_rich_color("temp_critical", "#FF0000"), history_size=history_size, id=id)
        self.temperature_histories = {}  # Store history for each sensor
        self.max_temp = 100  # Maximum temperature for scaling
        self.title = title
        self.border_title = title
        self.history_size = history_size
        self._last_temperatures = {}
        self._bars_rows_applied = None

    def compose(self) -> ComposeResult:
        yield Static("", id="temp-plot", classes="metric-plot")
        yield Static("", id="temp-bars", classes="metric-value")

    def _plotted_sensors(self, temperatures: dict) -> list:
        """Sensors the plot will draw, in plot order (same order the bars use)."""
        return [name for name, _ in self._sensor_priority_order(temperatures)][: self.MAX_PLOT_SENSORS]

    def _bars_rows(self, panel_height: int, n_sensors: int) -> int:
        """Rows for the bar block under the plot; 0 means "no room, hide it"."""
        if panel_height <= self.PLOT_MIN_ROWS or n_sensors <= 0:
            return 0
        return min(n_sensors, self.MAX_PLOT_SENSORS, panel_height - self.PLOT_MIN_ROWS)

    def get_temp_color(self, temp: float) -> str:
        """Get color based on temperature value."""
        if temp < 30:
            return get_rich_color("temp_cool", "#00FFFF")  # Cool - cyan
        elif temp < 50:
            return get_rich_color("temp_normal", "#00FF00")  # Normal - green
        elif temp < 70:
            return get_rich_color("temp_warm", "#FFFF00")  # Warm - yellow
        elif temp < 85:
            return get_rich_color("temp_hot", "#FF8C00")  # Hot - orange
        else:
            return get_rich_color("temp_critical", "#FF0000")  # Critical - red

    # Palette used for the plot lines, in series order; the bars reuse it so a sensor
    # has the same colour in both places.
    def _plot_palette(self) -> list:
        return [
            get_rich_color("temp_plot_1", "#FF8C00"),
            get_rich_color("temp_plot_2", "#00FF00"),
            get_rich_color("temp_plot_3", "#0080FF"),
        ]

    def _sensor_priority_order(self, temperatures: dict) -> list:
        """(name, temp) pairs ordered the way the plot draws them (priority, then hottest)."""
        priorities = {
            "cpu": 1, "core": 2, "gpu": 3, "motherboard": 4, "chipset": 5,
            "acpi": 6, "temp1": 7, "temp2": 8, "temp3": 9,
        }
        valid = {k: v for k, v in temperatures.items() if 0 <= v <= 150}
        return sorted(
            valid.items(),
            key=lambda item: (
                min([p for key, p in priorities.items() if key in item[0].lower()] or [10]),
                -item[1],
            ),
        )

    def create_temperature_bars(self, temperatures: dict, total_width: int, total_height: int) -> str:
        """One row per plotted sensor, in plot order, each exactly ``total_width`` cells.

        A sensor's bar uses the colour of its line in the plot above, so the two views
        read as one; a sensor the plot did not draw gets no row.
        """
        if not temperatures:
            return "No temperature data available"
        if total_width < self.BARS_MIN_WIDTH or total_height <= 0:
            return ""

        ordered = self._sensor_priority_order(temperatures)
        if not ordered:
            return "No valid temperature readings"

        # Line layout: name + " " + "100.0°C" + " " + bar == total_width
        temp_field = 7
        bar_width = max(3, total_width // 3)
        name_width = total_width - bar_width - temp_field - 2
        if name_width < 3:
            name_width = 0
            bar_width = max(1, total_width - temp_field - 1)

        palette = self._plot_palette()
        max_temp = max(1.0, float(self.max_temp))
        bars = []
        for i, (sensor_name, temp) in enumerate(ordered[: min(total_height, self.MAX_PLOT_SENSORS)]):
            # Same colour as this sensor's line in the plot (series order == this order).
            color = palette[i % len(palette)]
            temp_bar = self.build_gauge_bar(bar_width, temp / max_temp, color)
            temp_str = align(f"{temp:.1f}°C", temp_field, "right")

            if name_width:
                display_name = sensor_name.replace("_", " ").title()
                bar_line = f"[{color}]{align(display_name, name_width, 'left')}[/] {temp_str} {temp_bar}"
            else:
                bar_line = f"{temp_str} {temp_bar}"
            bars.append(bar_line)

        return "\n".join(bars)

    def get_temperature_plot(self, temperatures: dict, width: int, height: int) -> str:
        """Create a multi-line temperature plot on a (width, height) canvas."""
        plot_width, plot_height = width, height
        # Update histories for each sensor
        for sensor_name, temp in temperatures.items():
            if sensor_name not in self.temperature_histories:
                self.temperature_histories[sensor_name] = deque(
                    maxlen=self.history_size
                )

            # Filter out unrealistic temperatures
            if 0 <= temp <= 150:
                self.temperature_histories[sensor_name].append(temp)

        # Remove sensors that are no longer present
        current_sensors = set(temperatures.keys())
        for sensor_name in list(self.temperature_histories.keys()):
            if sensor_name not in current_sensors:
                del self.temperature_histories[sensor_name]

        if not self.temperature_histories:
            return "No temperature data to plot"

        if not self.plot_fits(plot_width, plot_height):
            return self.too_small_text(plot_width, plot_height)

        plt.clear_figure()
        plt.plot_size(height=plot_height, width=plot_width)
        plt.theme("pro")

        # Find temperature range for scaling
        all_temps = []
        for history in self.temperature_histories.values():
            all_temps.extend(list(history))

        if not all_temps:
            # If no history data yet, use current temperature values
            all_temps = [temp for temp in temperatures.values() if 0 <= temp <= 150]

        if not all_temps:
            return "No temperature data available"

        min_temp = max(0, min(all_temps) - 5)
        max_temp = min(150, max(all_temps) + 10)
        self.max_temp = max_temp

        plt.ylim(min_temp, max_temp)

        # Draw the most important sensors, in the same order the bars list them.
        # No plotext label: the bars below name every sensor in its plot colour,
        # so a legend would repeat them while eating plot rows.
        for sensor_name in self._plotted_sensors(temperatures):
            history = self.temperature_histories.get(sensor_name)
            if history:
                plt.plot(list(history), marker="braille")

        # Set temperature-specific y-axis labels
        num_ticks = min(5, plot_height - 1)
        if num_ticks > 1:
            tick_step = (max_temp - min_temp) / (num_ticks - 1)
            y_ticks = [min_temp + i * tick_step for i in range(num_ticks)]
            y_labels = [f"{temp:.0f}" for temp in y_ticks]
            plt.yticks(y_ticks, y_labels)

        plt.xfrequency(0)

        # Add temperature threshold lines
        warning_color = get_rich_color("temp_warning_line", "#FF0000")
        caution_color = get_rich_color("temp_caution_line", "#FF8C00")
        if max_temp > 80:
            plt.hline(80, color=warning_color)  # Warning line
        if max_temp > 60:
            plt.hline(60, color=caution_color)  # Caution line

        # Recolour the series to the palette the bars use. Plotext assigns its colours
        # in plot order: 1st -> [blue], 2nd -> [green], 3rd -> [magenta], 4th -> [cyan].
        palette = self._plot_palette()
        yellow_color = get_rich_color("temp_warm", "#FFFF00")
        red_color = get_rich_color("temp_critical", "#FF0000")
        result = recolor(
            ansi2rich(plt.build()).replace("\x1b[0m", ""),
            {
                "blue": palette[0],      # 1st sensor plotted
                "green": palette[1],     # 2nd
                "magenta": palette[2],   # 3rd
                "cyan": palette[0],      # 4th wraps around the palette
                "brown": yellow_color,
                "red": red_color,
                "yellow": yellow_color,
            },
        ).replace("──────┐", "──°C──┐")
        # Show timeframe ticks only when at least one sensor's history is full
        if any(h and len(h) >= self.history_size for h in self.temperature_histories.values()):
            result = substitute_plot_timeframe(result, self.history_size)
        return self.finish_plot(result, plot_width, plot_height)

    def rerender(self) -> None:
        """Re-draw plot and sensor bars for the current region sizes."""
        temperatures = self._last_temperatures
        try:
            temp_plot = self.query_one("#temp-plot")
            temp_bars = self.query_one("#temp-bars")
        except NoMatches:
            return  # DOM not ready yet (e.g. after layout change)

        if not temperatures:
            temp_plot.update("No temperature sensors detected\non this system")
            temp_bars.update("Temperature monitoring\nnot available")
            return

        # Fix the bar block's height (or hide it) so the plot's 1fr region is exact.
        bars_rows = self._bars_rows(
            self.content_size.height, len(self._plotted_sensors(temperatures))
        )
        if bars_rows != self._bars_rows_applied:
            self._bars_rows_applied = bars_rows
            temp_bars.styles.height = bars_rows or None
            temp_bars.display = bool(bars_rows)
            # The plot's region only reflects the new split after this layout pass.
            self.call_after_refresh(self.rerender)

        plot_width, plot_height = self.plot_region("#temp-plot", reserve_height=bars_rows)
        try:
            temp_plot.update(self.get_temperature_plot(temperatures, plot_width, plot_height))
            if bars_rows:
                bars_width, bars_height = self.region_size("#temp-bars")
                temp_bars.update(
                    self.create_temperature_bars(temperatures, bars_width, bars_height)
                )
        except Exception as e:
            logger.debug("Temperature render error: %s", e, exc_info=True)
            temp_plot.update(f"Temperature widget error:\n{str(e)}")
            temp_bars.update("Error updating\ntemperature data")

    def update_content(self, temperatures: dict):
        """Update the widget with new temperature data."""
        if temperatures:
            parts = [f"{k}: {v:.1f}" for k, v in sorted(temperatures.items()) if 0 <= v <= 150]
            if parts:
                logger.info(", ".join(parts))
        self._last_temperatures = temperatures or {}
        self.rerender()
