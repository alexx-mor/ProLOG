# ProLOG

ProLOG is an MVP Windows desktop application for unified production work logs.

The central entity is `WorkLogEntry`. Reports and shift assignments are derived
from work log entries, so the codebase can later grow into a broader production
platform without rewriting the foundation.

## Stack

- Python 3.12
- PySide6
- SQLite
- openpyxl
- JSON configuration
- PyInstaller

## Run

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Build

```powershell
python -m pip install pyinstaller
pyinstaller ProLOG.spec --noconfirm --clean
```

## WorkBot для MAX

В репозитории также находится WorkBot — бот для сбора ежедневных отчётов из
групп MAX, импорта старой истории и формирования Excel по сотрудникам.

Подробная документация:

- [README WorkBot](workbot/README.md)
- [История изменений WorkBot](workbot/CHANGELOG.md)
- [Принцип работы](workbot/docs/PRINCIPLES.md)
- [План интеграции WorkBot с ProLOG](workbot/docs/PROLOG_INTEGRATION.md)
- [Краткая передача контекста](workbot/docs/HANDOFF_TO_PROLOG.md)

WorkBot использует отдельную базу `data/workbot.sqlite3`. ProLOG читает ее через
адаптер, сопоставляет справочники и помещает отчеты в очередь проверки перед
записью в производственный журнал.

1. Скопируйте `workbot.env.example` в `private/workbot.env`.
2. Перевыпустите опубликованный токен MAX и укажите новый в `WORKBOT_TOKEN`.
3. Узнайте свой числовой MAX ID: `python -m workbot identify`.
4. Запишите ID в `WORKBOT_OWNER_IDS`.
5. Проверьте доступ: `python -m workbot check`.
6. Запустите пилотный режим: `python -m workbot run`.

Каждый сотрудник один раз пишет боту `/register` и передает контакт штатной
кнопкой MAX. WorkBot проверяет подпись контакта, после чего ProLOG автоматически
сопоставляет MAX ID с единственной карточкой сотрудника с тем же телефоном.

Бот должен быть администратором каждой группы, иначе MAX не передаст ему
события сообщений. В личном диалоге команды принимает только от ID владельца.
Посторонним по умолчанию не отвечает. В группах обычные сообщения игнорирует,
а заполненные по шаблону отчёты сохраняет молча.

Шаблон сообщения:

```text
Дата: 30.07.2026
Виды работ: Монтаж и проверка шкафа автоматики
Затраченное время: 8
Объект: Цех № 1
Местонахождение: Производство
```

Основные команды владельца: `/users`, `/chats`, `/bind MAX_ID | ФИО`, `/stats`,
`/errors`, `/excel`, `/missing` и `/template`. Команда `/excel` без дат
выгружает все данные; две даты ограничивают период.

После обнаружения `chat_id` старую историю группы можно импортировать командой:

```powershell
python -m workbot backfill CHAT_ID
```

Long Polling предназначен для пилота и локального запуска. Для постоянной
эксплуатации следует разместить обработчик на сервере с HTTPS и подключить
Webhook MAX.
