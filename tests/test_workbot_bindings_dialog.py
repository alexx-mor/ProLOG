"""GUI checks for the WorkBot user binding registry."""

from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QPushButton

from integrations.workbot.models import WorkBotUserLink
from ui.workbot_bindings_dialog import WorkBotBindingsDialog


class _FakeService:
    def list_user_links(self, _source_path: Path) -> list[WorkBotUserLink]:
        return [
            WorkBotUserLink(
                max_user_id=123456789,
                profile_name="Пользователь MAX",
                match_message="Не сопоставлен",
            )
        ]


def test_open_button_uses_max_web_user_link() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = WorkBotBindingsDialog(_FakeService(), Path("workbot.sqlite3"), [])
    button = dialog.table.cellWidget(0, 7)

    assert isinstance(button, QPushButton)
    assert button.text() == "Открыть"
    with patch("ui.workbot_bindings_dialog.QDesktopServices.openUrl", return_value=True) as open_url:
        button.click()

    assert open_url.call_count == 1
    assert open_url.call_args.args[0].toString() == "https://web.max.ru/:push?userId=123456789"
    dialog.close()
    app.processEvents()
