"""GUI checks for the WorkBot user binding registry."""

from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QPushButton

from integrations.workbot.models import WorkBotUserLink
from ui.workbot_bindings_dialog import NoWheelComboBox, WorkBotBindingsDialog


class _FakeService:
    def list_user_links(self, _source_path: Path) -> list[WorkBotUserLink]:
        return [
            WorkBotUserLink(
                max_user_id=123456789,
                profile_name="Пользователь MAX",
                match_message="Не сопоставлен",
            )
        ]


def test_action_button_opens_native_max_dialog() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = WorkBotBindingsDialog(_FakeService(), Path("workbot.sqlite3"), [])
    button = dialog.table.cellWidget(0, 4)

    assert isinstance(button, QPushButton)
    assert button.text() == "Открыть диалог в MAX"
    with patch("ui.workbot_bindings_dialog.QDesktopServices.openUrl", return_value=True) as open_url:
        button.click()

    assert open_url.call_args.args[0].toString() == "max://user/123456789"
    dialog.close()
    app.processEvents()


def test_employee_combo_ignores_mouse_wheel() -> None:
    class _WheelEvent:
        ignored = False

        def ignore(self) -> None:
            self.ignored = True

    combo = NoWheelComboBox()
    combo.addItems(["Первый", "Второй"])
    event = _WheelEvent()

    combo.wheelEvent(event)

    assert event.ignored
    assert combo.currentIndex() == 0
