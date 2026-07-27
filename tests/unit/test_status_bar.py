from core.bot_types.base import StatusItem
from ui.components import bot_status_bar_pills


class _Plugin:
    label = "CAF + llama.cpp"

    def status_items(self, _session_state, _project):
        return [
            StatusItem("Target: LOCAL"),
            StatusItem("Backend: llama.cpp"),
            StatusItem("Model: model.gguf"),
            StatusItem("Port: 8080"),
        ]


def test_bot_type_pill_is_first_and_existing_items_keep_order():
    html = bot_status_bar_pills(_Plugin(), {}, None)
    labels = [
        "BOT: CAF + llama.cpp",
        "Target: LOCAL",
        "Backend: llama.cpp",
        "Model: model.gguf",
        "Port: 8080",
    ]
    positions = [html.index(label) for label in labels]
    assert positions == sorted(positions)
