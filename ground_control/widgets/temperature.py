from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.containers import Horizontal
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align


class TemperatureWidget(MetricWidget):
    """Widget for system temperature monitoring."""

    def __init__(self, title: str, id: str = None, history_size: int = 120):
        super().__init__(title=title, color="red", history_size=history_size, id=id)
        self.temperature_histories = {}  # Store history for each sensor
        self.max_temp = 100  # Maximum temperature for scaling
        self.title = title
        self.border_title = title
        self.history_size = history_size

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("", id="temp-plot", classes="metric-plot")
            yield Static("", id="temp-bars", classes="metric-value")

    def get_temp_color(self, temp: float) -> str:
        """Get color based on temperature value."""
        if temp < 30:
            return "cyan"  # Cool - blue
        elif temp < 50:
            return "green"  # Normal - green
        elif temp < 70:
            return "yellow"  # Warm - yellow
        elif temp < 85:
            return "orange3"  # Hot - orange
        else:
            return "red"  # Critical - red

    def create_temperature_bars(self, temperatures: dict, total_width: int = 40) -> str:
        """Create vertical temperature bars for current readings."""
        if not temperatures:
            return "No temperature data available"

        # try:
        # Filter out extremely high or low values (likely sensor errors)
        valid_temps = {k: v for k, v in temperatures.items() if 0 <= v <= 150}

        if not valid_temps:
            return "No valid temperature readings"

        # Sort sensors by temperature (hottest first)
        sorted_temps = sorted(valid_temps.items(), key=lambda x: x[1], reverse=True)

        # Create bars for each sensor
        bars = []
        for sensor_name, temp in sorted_temps[:6]:  # Show max 6 sensors
            # Clean up sensor name for display
            display_name = sensor_name.replace("_", " ").title()
            if len(display_name) > 12:
                display_name = display_name[:12] + "..."

            # Calculate bar length (scale to available width)
            bar_width = min(total_width - 20, 25)  # Reserve space for labels
            temp_percent = min(temp / self.max_temp, 1.0)
            filled_blocks = int(bar_width * temp_percent)
            empty_blocks = bar_width - filled_blocks

            # Get color based on temperature
            color = self.get_temp_color(temp)

            # Create temperature bar
            temp_bar = f"[{color}]{'█' * filled_blocks}[/]{'░' * empty_blocks}"

            # Format temperature value
            temp_str = f"{temp:5.1f}°C"

            # Align sensor name
            sensor_aligned = align(display_name, 12, "left")

            bar_line = f"{sensor_aligned} {temp_str} {temp_bar}"
            bars.append(bar_line)

        return "\n".join(bars)

        # except Exception as e:
        #     return f"Error creating temperature bars: {str(e)}"

    def get_temperature_plot(self, temperatures: dict) -> str:
        """Create a multi-line temperature plot."""
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

        # Get plot dimensions
        plot_height = max(6, getattr(self, "plot_height", 10))
        plot_width = max(20, getattr(self, "plot_width", 40))

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

        # Plot up to 4 most important sensors
        sensor_priorities = {
            "cpu": 1,
            "core": 2,
            "gpu": 3,
            "motherboard": 4,
            "chipset": 5,
            "acpi": 6,
            "temp1": 7,
            "temp2": 8,
            "temp3": 9,
        }

        # Sort sensors by priority and temperature
        sorted_sensors = sorted(
            self.temperature_histories.items(),
            key=lambda x: (
                min(
                    [
                        sensor_priorities.get(key, 10)
                        for key in sensor_priorities.keys()
                        if key in x[0].lower()
                    ]
                    or [10]
                ),  # Provide fallback value
                -max(x[1]) if x[1] else 0,
            ),
        )

        colors = [
            "orange1",
            "green",
            "blue",
            "orange1",
            "green",
            "blue",
            "orange1",
            "green",
            "blue",
            "orange1",
            "green",
            "blue",
        ]

        for i, (sensor_name, history) in enumerate(sorted_sensors[:4]):
            if history:
                color = colors[i % len(colors)]
                # Create short label for legend
                short_name = sensor_name.replace("_", "").replace(" ", "")
                plt.plot(
                    list(history), marker="braille", label=short_name
                )  # , color=color)

        # Set temperature-specific y-axis labels
        num_ticks = min(5, plot_height - 1)
        if num_ticks > 1:
            tick_step = (max_temp - min_temp) / (num_ticks - 1)
            y_ticks = [min_temp + i * tick_step for i in range(num_ticks)]
            y_labels = [f"{temp:.0f}" for temp in y_ticks]
            plt.yticks(y_ticks, y_labels)

        plt.xfrequency(0)

        # Add temperature threshold lines
        if max_temp > 80:
            plt.hline(80, color="red")  # Warning line
        if max_temp > 60:
            plt.hline(60, color="orange1")  # Caution line

        result = ansi2rich(plt.build()).replace("[brown]", "[yellow]").replace("[red]", "[red]").replace("[blue]", "[orange1]").replace("[green]", "[yellow]").replace("[magenta]", "[red]").replace("[yellow]", "[yellow]").replace("\x1b[0m", "").replace("──────┐", "──°C──┐")
        return result

        # except Exception as e:
        #     return f"Error creating temperature plot: {str(e)}"

    def update_content(self, temperatures: dict):
        """Update the widget with new temperature data."""
        if not temperatures:
            # Show a message when no temperature data is available
            try:
                temp_plot = self.query_one("#temp-plot")
                temp_plot.update("No temperature sensors detected\non this system")

                temp_bars = self.query_one("#temp-bars")
                temp_bars.update("Temperature monitoring\nnot available")
            except Exception as e:
                print(f"Error updating empty temperature widget: {e}")
            return

        try:
            # Update the temperature plot
            temp_plot = self.query_one("#temp-plot")
            plot_content = self.get_temperature_plot(temperatures)
            temp_plot.update(plot_content)

            # Update the temperature bars
            temp_bars = self.query_one("#temp-bars")
            bar_width = getattr(self, "plot_width", 40)
            bars_content = self.create_temperature_bars(temperatures, bar_width)
            temp_bars.update(bars_content)

        except Exception as e:
            # Show the actual error for debugging
            try:
                temp_plot = self.query_one("#temp-plot")
                temp_plot.update(f"Temperature widget error:\n{str(e)}")
                temp_bars = self.query_one("#temp-bars")
                temp_bars.update("Error updating\ntemperature data")
            except:
                pass
