"""GUI checks for the WorkBot user binding registry."""

from pathlib import Path
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


def test_action_button_copies_max_id() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = WorkBotBindingsDialog(_FakeService(), Path("workbot.sqlite3"), [])
    button = dialog.table.cellWidget(0, 4)

    assert isinstance(button, QPushButton)
    assert button.text() == "Копировать ID"
    button.click()

    assert QApplication.clipboard().text() == "123456789"
    dialog.close()
    app.processEvents()
