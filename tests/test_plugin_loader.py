from datetime import datetime, timezone

from changelorg.models import Source, TimeWindow
from changelorg.plugin import PluginManager, load_local_plugins


def test_load_local_plugin_from_file(tmp_path) -> None:
    plugin_file = tmp_path / "example.py"
    plugin_file.write_text(
        """
from changelorg.models import ChangeInput

class ExamplePlugin:
    key = "example"
    name = "Example"
    description = "Example plugin"
    config_schema = {}

    def fetch(self, source, window):
        return [ChangeInput(title="Example change", published_at=window.end)]

plugin = ExamplePlugin()
""".strip()
    )

    plugins = load_local_plugins([tmp_path])
    manager = PluginManager(plugins)
    source = Source(
        id=1,
        name="Example",
        plugin="example",
        config={},
        enabled=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    window = TimeWindow(
        start=datetime(2026, 7, 4, tzinfo=timezone.utc),
        end=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )

    changes = manager.get("example").fetch(source, window)

    assert changes[0].title == "Example change"
