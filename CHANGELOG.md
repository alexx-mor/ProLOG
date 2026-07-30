# Changelog

## 2026-07-30 - Product tracking in reports and analytics

### Added

- Added product selection to the employee work-log form.
- Added product columns to employee work history, report viewer and Excel exports.
- Added a product filter and a dedicated `По изделиям` analytics table with employees, entries, hours, person-hours and payroll.

### Changed

- Renamed the work-time calendar directory to `Производственный календарь`.
- Removed colored cell backgrounds from the payment directory table.

### Verified

- Python syntax check passed for the changed modules.
- SQLite smoke test passed for saving work-log entries with products and calculating product analytics.
- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.

## 2026-07-30 - Calendar navigation lock

### Fixed

- Blocked mouse-wheel events on the internal calendar month viewports.
- Blocked keyboard navigation keys inside yearly calendar month widgets so months cannot shift accidentally.

### Verified

- Python syntax check passed for the changed modules.
- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.

## 2026-07-30 - Calendar read-only view and payroll table grouping

### Added

- Added month titles to the yearly work calendar.
- Added read-only selected-day explanation for the work calendar: workday, day off, holiday name, shortened day or working weekend.
- Added local names for the main Russian federal holidays when showing production calendar details.

### Changed

- Disabled mouse-wheel month scrolling inside calendar month widgets.
- Hidden dates outside each displayed month in the yearly calendar.
- Removed day-type editing and notes from the work calendar directory view.
- Redesigned the main payment directory table as an Excel-like multi-row view with merged position, pay type and KTU cells.
- Updated production calendar import request to distinguish holidays when the provider supports it.

### Verified

- Python syntax check passed for the changed modules.
- Production calendar provider smoke test passed for holiday, day off and shortened workday codes.
- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.

## 2026-07-29 - Setup objects, far-trip pay and yearly calendar

### Added

- Added full object-card editing to the initial setup wizard object step.
- Added separate far business trip pay amounts per position category.
- Added 12-month yearly calendar layout to the work calendar directory.

### Changed

- Far business trip payroll now uses a separate pay amount instead of a coefficient.
- Payment editing now follows the Excel-like layout: position, grade, base pay and far business trip pay.
- Double click in the initial setup wizard directories opens editing instead of toggling active state.

### Verified

- Python syntax check passed for the changed modules.
- SQLite and analytics smoke test passed for category-based far business trip pay.
- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.

## 2026-07-29 - Directory editing and production calendar view

### Added

- Added object filter to the product directory.
- Added visual production calendar view with highlighted weekends, holidays, working weekends and shortened workdays.
- Added production calendar synchronization foundation through a separate `production_calendar.py` provider module using the public `isdayoff.ru` API.

### Changed

- Double click now opens editing in all table-based directories instead of toggling active state.
- Removed active-status controls from the product edit dialog.
- Redesigned the payment edit dialog as an Excel-like table by position and grade.
- Renamed the payment KTU column `КД` to `Командировка дальняя (КД)`.

### Verified

- Python syntax check passed for the changed modules.
- SQLite smoke test passed for products and calendar day saving.
- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.

## 2026-07-29 - Product directory and pay table redesign

### Added

- Added product directory with required object binding, serial number, name, code, status, readiness percent, production start date and release date.
- Added contract number to object cards and object storage.
- Added overdue highlighting for object due dates in the object edit dialog and directory table.

### Changed

- Redesigned payment directory as a position-based summary table with category 0-3 amount columns and KTU coefficient columns.
- Payment amounts are displayed in human-readable money format, for example `100 000,00`.
- Object edit dialog now shows contract fields before customer and classification fields.
- Renamed object contract field label from `Тип` to `Предмет договора`.

### Verified

- Python syntax check passed for the changed modules.
- SQLite schema smoke test passed for `Objects` and `Products`.
- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.

## 2026-07-29 - Calendar, analytics and object lifecycle

### Added

- Added a work-time calendar directory for configured working days, days off, holidays, working Saturdays and working Sundays/holidays.
- Added object lifecycle status: planned, in progress, paused, delivered and closed.
- Added object status editing to the object card and object directory table.
- Added date-based analytics alongside object, employee and work-type analytics.
- Added calendar-aware payroll calculations for working Saturdays and holidays.

### Changed

- Analytics payroll now uses calendar day type before falling back to weekend detection.
- Help now mentions the work-time calendar and analytics workflow.

### Verified

- Python syntax check passed for the changed modules.
- SQLite schema smoke test passed for `Objects`, `PayRates` and `WorkCalendarDays`.
- Payroll smoke test passed for working Saturday coefficient calculation.
- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.

## 2026-07-10 - Import progress and compact about dialog

### Added

- Added project-card fields for objects: project/request number, customer, contract type, object type, subtype, signing date and due date.
- Added automatic days-left display for object due dates.
- Added pay coefficients for far business trips, near business trips, Sundays/holidays and Saturdays in the payment directory.
- Added active/inactive indicator and activation button inside the position edit dialog.

### Changed

- Moved legacy Excel analysis to a background Qt worker thread so the import window remains responsive.
- Added an indeterminate progress bar and status text while checking old Excel reports.
- Temporarily disables import-window actions during analysis to prevent duplicate checks.
- Made the About dialog more compact by reducing logo size, width and internal spacing.
- Position rows now open the edit dialog on double click.
- Payroll analytics now applies payment coefficients by location and weekend day.

### Verified

- Python syntax check passed for the changed UI modules.
- SQLite schema smoke test passed for expanded `Objects` and `PayRates` tables.
- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.

## 2026-07-07 - Legacy Excel report importer

### Added

- Added a plugin-like `legacy_import` adapter package for old secretary-maintained Excel reports.
- Added parsing of legacy workbooks where each sheet represents an employee and dated rows contain work text, hours, object and old location data.
- Added import preview with row statuses, errors, warnings, missing-report notes and new-object notes.
- Added audit tables `ImportBatches` and `ImportRows` to keep import history and row-level import explanations.
- Added duplicate-file protection by SHA-256 hash for completed legacy imports.
- Added `File - Импорт старых отчетов Excel` menu action, available only after the initial setup wizard is complete.
- Added help text for legacy report import.
- Added interactive row resolution in the legacy import preview: users can fix employee mapping, object, location, work type, description and hours directly in the import window.
- Added suggested resolution hints for common legacy import discrepancies, including missing employees, empty descriptions, invalid hours and empty objects.

### Changed

- Legacy absences are normalized into current ProLOG locations: vacation, unpaid leave, sick leave, study leave and absence.
- Legacy `выходной` rows are skipped and shown in the import audit instead of becoming work log entries.
- Legacy work rows are imported as `WorkLogEntry` records through the business service layer, preserving old sheet, row, position and location in comments.
- Legacy import validation is recalculated after manual corrections before allowing commit.

### Built

- Rebuilt the portable Windows executable with PyInstaller `6.19.0`.
- Build output:
  `dist\ProLOG\ProLOG.exe`

### Verified

- Python syntax check passed for the changed modules using available Python 3.12.
- SQLite schema smoke test passed: import audit tables are created on database initialization.
- Normalization smoke test passed for old absence spellings and employee-initial matching.
- Legacy Excel parser smoke test passed on `Отчеты Июнь 2026 АСУТП.xlsx`: 900 rows, 5764 hours, period 2026-06-01 to 2026-06-30.
- Executable startup smoke test passed: `ProLOG.exe` stayed alive for 6 seconds and was stopped manually.
- Interactive legacy import correction UI was included in the rebuilt executable and passed Python syntax check.

## 2026-07-03 - MVP foundation

### Added

- Created the initial ProLOG project structure.
- Added a layered architecture: UI, business services, SQLite repositories.
- Added domain models with `WorkLogEntry` as the central business entity.
- Added SQLite schema for employees, departments, objects, work types, locations, units, work log entries, and settings.
- Added editable directory management through the settings dialog.
- Added employee Excel import and employee export.
- Added Excel export for work reports and shift assignments.
- Added GitHub Releases update checker.
- Added PySide6 MVP UI:
  - employee directory on the left;
  - selected employee work log workspace on the right;
  - daily work log table;
  - top report filters and export actions.
- Added PyInstaller spec file for portable Windows build.

### Verified

- Python interpreter found at `C:\Users\sasha\AppData\Local\Programs\Python\Python313\python.exe`.
- `PySide6` and `openpyxl` are installed.
- Python syntax check passed with `py_compile`.
- Smoke test passed for SQLite initialization, employee creation, work log creation, and work log reading.

### Notes

- The current MVP keeps all directories user-editable while seeding initial values on first launch.
- UI does not access SQLite directly; database operations go through service and repository layers.

## 2026-07-03 - Windows executable build

### Added

- Added this `CHANGELOG.md` as the working project change journal.

### Built

- Built the first portable Windows executable with PyInstaller `6.19.0`.
- Build command:
  `C:\Users\sasha\AppData\Local\Programs\Python\Python313\python.exe -m PyInstaller ProLOG.spec --noconfirm --clean`
- Build output:
  `dist\ProLOG\ProLOG.exe`

### Verified

- `dist\ProLOG\ProLOG.exe` was created successfully.
- Build folder contains the executable and `_internal` runtime dependencies.
- PyInstaller warnings were reviewed; listed missing modules are optional/platform-specific for the current Windows build.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-03 - UI and reporting simplification

### Changed

- Removed organization structure fields from employee and work log workflows.
- Renamed employee category to rank in the user interface and Excel files.
- Removed unit and quantity from the work log workflow.
- Kept reports focused on text work descriptions, hours, and comments.
- Replaced the top action toolbar with a Windows-style menu:
  - File: report requisites, employee import/export, exit.
  - Settings: directories and update check.
  - Export: work report and shift assignment.
  - Help: user help and about dialog.
- Added report requisites:
  - leader full name;
  - department name;
  - organization name.
- Added a status bar for operation results and errors.
- Made employee and work log table columns manually resizable.
- Added persistence for window size, splitter position, and table column widths.
- Redesigned directory management with left navigation, search, table view, rename, disable, and restore actions.

### Verified

- Python syntax check passed with `py_compile`.
- SQLite smoke test passed for the simplified work log model.
- Excel export smoke test passed for work report and shift assignment.
- Rebuilt `dist\ProLOG\ProLOG.exe` with PyInstaller `6.19.0`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Portable data path verified: runtime data is created next to `ProLOG.exe`, not inside `_internal`.
- Distribution folder cleaned after smoke test; it contains only `ProLOG.exe` and `_internal`.

## 2026-07-04 - UX polish and directories update

### Changed

- Replaced employee table column `Разряд` with `Категория`.
- Removed the visible employee status column from the employee table.
- Hidden inactive employees from the main employee list after disabling.
- Made work hours integer-only in the work log form and table.
- Improved date input display format, vertical alignment, and calendar styling.
- Improved ComboBox popup rendering to avoid the old Windows Classic look.
- Replaced standard dialog buttons with Russian labels in application dialogs.
- Added right-click context menu to the employee table.
- Added employee positions as an editable directory.
- Seeded initial positions:
  - электромонтажник;
  - слесарь;
  - слесарь-электромонтажник;
  - инженер АСУТП;
  - специалист по снабжению;
  - помощник руководителя;
  - ведущий инженер АСУТП;
  - сварщик;
  - мастер;
  - оператор ЧПУ;
  - сварщик-аргонщик.
- Fixed directory table shrinking after disabling an item.

### Verified

- Python syntax check passed with `py_compile`.
- SQLite smoke test passed for seeded positions and integer work hours.
- Rebuilt `dist\ProLOG\ProLOG.exe` with PyInstaller `6.19.0`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Distribution folder checked; it contains only `ProLOG.exe` and `_internal`.

## 2026-07-04 - Startup crash fix

### Fixed

- Fixed application startup crash caused by missing `QMenu` import in the employee table context menu.

### Verified

- Python syntax check passed with `py_compile`.
- UI modules import check passed.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Distribution folder cleaned after smoke test.

## 2026-07-04 - Native controls rollback

### Changed

- Removed custom-drawn ComboBox and DateEdit arrows to restore stable system rendering.
- Removed custom button/input styling that made standard controls visually fragile.
- Kept only light table and section styling.
- Replaced work hours spin box with a simple integer ComboBox from 0 to 24.
- Removed the `Время` column from the selected-date work log table.
- Replaced employee disabling with real employee deletion.
- Changed right-click employee table action from `Отключить сотрудника` to `Удалить сотрудника`.

### Verified

- Python syntax check passed with `py_compile`.
- UI modules import check passed.
- SQLite smoke test passed for employee deletion rules.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Distribution folder cleaned after smoke test.

## 2026-07-04 - Requisites and date usability

### Added

- Added local private requisites file: `private\requisites.json`.
- Added `.gitignore` rules to prevent private requisites, config, local databases, logs, exports, build, and dist artifacts from being uploaded to Git.
- Added required startup requisites dialog: the application cannot be used until organization, department, and leader are selected.
- Added `Сегодня` button next to the work date field.
- Added date field click handling that opens the built-in `QDateEdit` calendar popup via the standard keyboard command.

### Changed

- Requisites are now selected only from dropdown lists and cannot be typed manually.
- Work hours are now entered in a text field with an integer validator from 0 to 24.
- Menu bar background was aligned with the main window background to remove the white strip.
- PyInstaller spec now includes the private requisites file when it exists locally.

### Verified

- Python syntax check passed with `py_compile`.
- UI modules import check passed.
- Private requisites file load check passed.
- Work log widget and requisites dialog constructor smoke test passed.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Verified bundled private requisites file placement under `_internal\private`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Distribution folder cleaned after smoke test while keeping bundled private requisites data.

## 2026-07-04 - Application icon and About dialog

### Added

- Converted `icon.png` to `resources\app.ico` for the Windows executable.
- Added `resources\app_logo.png` for in-app branding.
- Added the application icon to the main window.
- Added a branded `О программе` dialog with logo, version, short product purpose, and legal ownership wording.

### Changed

- Updated PyInstaller spec to use `resources\app.ico` as the executable icon.
- Updated resource path handling so bundled resources are loaded correctly from PyInstaller `_internal`.

### Verified

- Python syntax check passed with `py_compile`.
- `О программе` dialog constructor smoke test passed.
- Icon and logo resource existence check passed.
- Rebuilt `dist\ProLOG\ProLOG.exe` with the new executable icon.
- Verified bundled resources under `_internal\resources`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Distribution folder cleaned after smoke test while keeping bundled resources and private requisites data.

## 2026-07-05 - Position directory and authorization refinements

### Changed

- Renamed user-facing `Реквизиты` to `Авторизация`.
- Updated private authorization department list:
  - `Цех композитных изделий` replaced with `Участок композитных материалов`.
- Fixed employee table column widths:
  - `№` is fixed;
  - `Категория` is fixed.
- Updated position directory seed data:
  - position names are capitalized;
  - positions are sorted alphabetically;
  - missing requested positions were added.
- Added `Категория` column to the position directory.
- Added position category defaults:
  - most positions: `1-3`;
  - masters and leading automation engineer: `—`;
  - storekeeper, supply specialist, and assistant manager: `1-2`.
- Renamed directory action button `Восстановить` to `Активировать`.
- Added status icons in directory tables.

### Verified

- Python syntax check passed with `py_compile`.
- UI modules import check passed.
- Position directory SQLite smoke test passed.
- Employee table and directory dialog constructor smoke test passed.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Verified bundled private authorization file contains the updated production site name.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Distribution folder cleaned after smoke test.

## 2026-07-06 - Work table date separation and editable dictionaries

### Added

- Added editable JSON dictionary files under `dictionaries`:
  - `locations.json`;
  - `objects.json`;
  - `positions.json`;
  - `work_types.json`.
- Added runtime dictionary file support:
  - source defaults are bundled with the application;
  - editable copies are created next to `ProLOG.exe` on first launch.
- Added Windows executable version resource metadata:
  - file description;
  - file version;
  - product name;
  - product version;
  - copyright;
  - Russian language translation metadata.

### Changed

- Removed the text from the `Новая запись` group frame and added a separate section heading above the form.
- Separated the work table date selector from the new-entry date selector.
- Work log table refresh and Excel exports now use the `Работы за выбранную дату` date.
- New work entry saving and duplicate-last preparation now use the `Новая запись` date.
- Rebuilt `resources\app_logo.png` and `resources\app.ico` from `icon.png` with a transparent outer background.
- PyInstaller spec now bundles editable dictionary defaults and applies `version_info.txt`.

### Verified

- Python syntax check passed with `py_compile`.
- Work log widget constructor smoke test passed.
- JSON dictionary loading check passed.
- SQLite dictionary seeding smoke test passed on a temporary database.
- Verified `resources\app_logo.png` has transparent corner alpha values.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Verified Windows executable properties contain description, versions, product name, copyright, and Russian language.
- Verified bundled default dictionaries under `_internal\dictionaries`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Verified editable runtime dictionaries were created next to `ProLOG.exe`.

## 2026-07-06 - Directory workflow and first-run setup

### Added

- Added first-run setup wizard after authorization:
  - employee import/manual employee entry;
  - position review;
  - work type review;
  - object review;
  - final welcome-to-work step.
- Added `initial_setup_done` setting to show the setup wizard only on the first configured launch.
- Added directory item deletion in the directory dialog and setup wizard.
- Added right-click context menus for directory tables.
- Added double-click status toggle for directory rows.
- Added department-based dictionary activation from JSON metadata.
- Added employee category validation based on the selected position category rule:
  - `1-2` means only categories `1` and `2`;
  - `1-3` means only categories `1`, `2`, and `3`;
  - empty category is rejected for ranged positions.

### Changed

- Renamed the work table section to `Работы на выбранную дату`.
- Aligned main window section margins for employees, new entry, and daily work table blocks.
- Updated the location dictionary:
  - `Больничный`;
  - `Отпуск`;
  - `Без содержания`;
  - `Прогул`;
  - `Командировка ближняя (КБ)`;
  - `Командировка дальняя (КД)`;
  - `Учебный отпуск`;
  - `Производство`;
  - `Офис`.
- Updated the work type dictionary to the requested production-specific list.
- Added editable `departments` bindings to position and work type JSON dictionaries.
- Existing obsolete locations, positions, and work types are disabled when department defaults are applied.
- Updated help text for first-run setup and employee Excel import with `№`, `ФИО`, `Должность`, `Категория`.

### Verified

- Python syntax check passed with `py_compile`.
- JSON dictionary loading check passed.
- Employee category rule check passed.
- Department-based directory activation smoke test passed on a temporary database.
- Employee save validation smoke test passed for valid and invalid categories.
- Directory dialog, employee dialog, and first-run setup wizard constructor smoke test passed.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Verified executable file properties after rebuild.
- Verified bundled JSON dictionaries under `_internal\dictionaries`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.
- Verified editable runtime dictionaries were created next to `ProLOG.exe`.

## 2026-07-06 - Setup wizard polish and employee import fixes

### Changed

- Renamed first-run setup window to `Мастер настройки ProLOG`.
- Made the setup wizard a standalone application-modal window so it appears independently and can be closed normally.
- Reworked setup wizard welcome page spacing and alignment.
- Added application-wide button sizing and padding for consistent controls across ProLOG.
- Removed the separate `№` column from employee tables; row numbering is now handled by the table row header.
- Improved setup wizard table column sizing to avoid cramped columns.
- Updated work type names to start with uppercase letters.
- Updated dictionary seeding to rename existing entries that differ only by letter case instead of creating duplicates.
- Improved Excel import handling:
  - category `-` or `—` is treated as empty;
  - custom positions imported without category are marked as positions without category;
  - imported Excel workbooks are closed after reading.
- Added compatibility for old saved employee table widths that included the removed `№` column.

### Added

- Added saturated active/inactive status icons in directory tables and setup wizard tables.
- Added disabled-row highlighting for inactive directory items.

### Verified

- Python syntax check passed with `py_compile`.
- Setup wizard constructor and close smoke test passed.
- Directory dialog smoke test passed with status text.
- Employee table smoke test passed with the new 3-column layout.
- Excel import smoke test passed for a custom `Главный энергетик` position with `—` category.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Verified executable file properties after rebuild.
- Verified bundled and editable runtime dictionaries contain uppercase work type names.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Import category zero and setup close behavior

### Changed

- Employee Excel import now preserves numeric zero values from cells.
- Imported employee category `0` is normalized to `0 (студент)`.
- Manual employee entry also normalizes category `0` to `0 (студент)`.
- Worker positions now allow category `0 (студент)` in addition to their configured category range.
- Empty employee categories in Excel no longer block import.
- Closing `Мастер настройки ProLOG` now closes the whole application and leaves setup unfinished.
- On next launch, the setup wizard appears again until completed.
- Employee table columns in the main window now auto-fit to the available width.
- Renamed `Производственный участок Композитных Материалов (г.Зверево)` to `Участок композитных материалов`.
- Added compatibility so old JSON files with the previous department name still match the new department name.

### Verified

- Python syntax check passed with `py_compile`.
- Excel import smoke test passed for:
  - worker category `0`;
  - empty worker category;
  - custom position with `—`.
- Manual employee save smoke test passed for worker category `0`.
- Employee dialog smoke test shows `0 (студент)` for worker positions.
- Setup wizard close smoke test passed.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Verified executable file properties after rebuild.
- Verified bundled and editable runtime dictionaries use `Участок композитных материалов`.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Report tabs and report viewer

### Changed

- Widened `Назад`, `Далее`, and `Готово` buttons in `Мастер настройки ProLOG`.
- Increased fixed width of employee and text input dialogs so long full names and positions fit better.
- Restricted `0 (студент)` to selected positions:
  - `Электромонтажник`;
  - `Слесарь`;
  - `Инженер АСУТП`;
  - `Слесарь-электромонтажник`;
  - `Слесарь КИПиА`.
- Removed `Повторить последнюю` from the work entry form.
- Removed the daily work table from the work entry tab.
- Added selected employee information above the work entry date.
- Reworked the main window into two tabs:
  - `Заполнение отчетов`;
  - `Просмотр отчетов`.

### Added

- Added `Просмотр отчетов` tab with filters by:
  - employee;
  - object;
  - start date;
  - end date.
- Added report viewer table for already entered work log records.
- Added service method for filtered work log search.

### Verified

- Python syntax check passed with `py_compile`.
- Main window smoke test passed with two tabs.
- Report viewer smoke test passed on a temporary database with one work log entry.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Verified executable file properties after rebuild.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Report editing and input validation polish

### Changed

- Employee search is now case-insensitive for Cyrillic text.
- The work entry `Объект` field is now a non-editable dropdown, matching location and work type fields.
- The selected employee information in the work entry form is aligned with the rest of the form.
- `Сохранить запись` and `Очистить форму` are aligned inside the form layout.
- Added visual styling for main window tabs.
- Work entry form validation errors are now shown as normal application warnings instead of PyInstaller unhandled-exception dialogs.
- Removed obsolete manual-object creation prompt from work log saving.

### Added

- Added validation that blocks zero hours for ordinary work entries.
- Zero hours are allowed only for:
  - `Отпуск`;
  - `Без содержания`;
  - `Прогул`;
  - `Больничный`.
- Added double-click opening from `Просмотр отчетов` into `Заполнение отчетов` for editing the selected work log entry.
- Added safe loading of inactive/old directory values when editing existing work log entries.
- Added a global unhandled-exception logger that writes details to `data\prolog.log`.

### Verified

- Python syntax check passed with `py_compile`.
- Case-insensitive employee search smoke test passed.
- Zero-hour validation smoke test passed.
- Report-viewer-to-editor smoke test passed.
- Work entry validation smoke test passed: saving without an employee now emits a controlled warning signal.
- Rebuilt `dist\ProLOG\ProLOG.exe`.
- Verified executable file properties after rebuild.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Work entry layout polish

### Changed

- Removed the separate `Новая запись` section title above the work entry form.
- Moved selected employee data into a highlighted context panel at the top of the form.
- Underlined dynamic employee values: full name, position, and category.
- Increased spacing and minimum widths for `Сегодня`, `Сохранить запись`, and `Очистить форму`.
- Adjusted initial splitter sizing so the right work area receives more space, including when old wide-left settings are saved.

### Verified

- Python syntax check passed with `py_compile`.
- Work entry layout smoke test passed.
- Splitter sizing smoke test passed for old saved sizes.
- Rebuilt real `F:\!Морарь\!RAMOR\MPV - ProLOG\dist\ProLOG\ProLOG.exe`.
- Verified executable version information after rebuild.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Position payroll groundwork and employee filters

### Changed

- Non-work locations no longer require work description or hours:
  - `Отпуск`;
  - `Без содержания`;
  - `Прогул`;
  - `Больничный`;
  - `Учебный отпуск`.
- Work hours are validated in business logic and cannot exceed 24 hours per day.
- `Мастер чистоты` is forced to have no employee category and no `0 (студент)` category.
- Employee category is cleared during import/save when the selected position has no categories.
- In directories, `Переименовать` is now `Редактировать`.
- Removed the separate `Категория` button for positions.

### Added

- Added employee filters by position and by group:
  - `Рабочие`;
  - `ИТР`.
- Extended position directory model and SQLite schema with:
  - category rule;
  - `0 (студент)` permission;
  - employee group;
  - `Ставка/зарплата`.
- Added a position editor dialog for name, categories, zero category, group, and pay/rate groundwork.
- Added the same extended position view/editor to `Мастер настройки ProLOG`.
- Added SQLite migration for new position fields.

### Verified

- Python syntax check passed with `py_compile`.
- Business smoke test passed for:
  - position migration;
  - employee filtering by position and group;
  - non-work entry without description and hours;
  - 24-hour limit;
  - `Мастер чистоты` without category.
- UI smoke test passed for:
  - directory dialog;
  - position editor;
  - employee filters;
  - setup wizard.
- Rebuilt real `F:\!Морарь\!RAMOR\MPV - ProLOG\dist\ProLOG\ProLOG.exe`.
- Verified executable version information after rebuild.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Work form history and table usability polish

### Changed

- Redesigned `О программе` into a more modern branded dialog with logo, version, product purpose, and legal block.
- `Очистить форму` now clears text fields and resets location, object, and work type dropdowns.
- Reduced the height of `Описание работ` by roughly one third.
- In the position directory, renamed the first column from `Название` to `Должность`.
- The position `Должность` column is now manually resizable.
- Compact position directory columns for category, zero category, group, salary/rate, and status.

### Added

- Added a work history table under the new work entry form for the currently selected employee.
- The employee work history table shows:
  - date;
  - object;
  - work type;
  - description;
  - hours.
- Added cell tooltips with full cell text across main tables, report tables, directories, and setup wizard tables.
- Added application tooltip styling.

### Verified

- Python syntax check passed with `py_compile`.
- UI smoke checks passed for:
  - dropdown reset on form clear;
  - selected employee work history table;
  - full-text table cell tooltips;
  - modern About dialog constructor;
  - position directory `Должность` column and resizable mode.
- Rebuilt real `F:\!Морарь\!RAMOR\MPV - ProLOG\dist\ProLOG\ProLOG.exe`.
- Verified executable version information after rebuild.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Position pay type and update status window

### Changed

- In the position editor, split pay settings into:
  - `Тип оплаты`: `Ставка` or `Зарплата`;
  - `Сумма`.
- Renamed the position directory pay column to `Оплата`.
- Position directory now displays pay with a clear suffix:
  - `/ час` for hourly rate;
  - `/ мес` for monthly salary.
- Extended the position model and SQLite schema with `salary_type`.
- Position seeding no longer overwrites existing user-edited position metadata on startup.
- JSON dictionaries now support versioned payloads: `version` plus `items`.
- Dictionary loader remains compatible with older array-only JSON files.

### Added

- Added a dedicated `Проверка обновлений` dialog.
- The update dialog shows status for:
  - ProLOG core;
  - locations;
  - objects;
  - positions;
  - work types.
- Added dictionary comparison by item names.
- Added safe dictionary merge: bundled updates only add missing items and preserve user custom records/settings.

### Verified

- Python syntax check passed with `py_compile`.
- Smoke test passed for:
  - hourly and monthly pay display;
  - dictionary version/status reading;
  - SQLite save/read of monthly `salary_type`.
- Rebuilt real `F:\!Морарь\!RAMOR\MPV - ProLOG\dist\ProLOG\ProLOG.exe`.
- Verified executable version information after rebuild.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Analytics tab and faster object entry

### Changed

- Reduced the `Описание работ` text box height in the work entry form.
- In `Проверка обновлений`, fixed the `Компонент` column width and expanded `Доступная версия` and `Статус`.
- Added `monthly_hours_norm` to configuration as groundwork for monthly salary calculations.

### Added

- Added the `Аналитика` tab with filters by employee, object, and date period.
- Added summary metrics:
  - employees count;
  - entries count;
  - hours;
  - person-hours;
  - payroll cost.
- Added analytics tables:
  - by objects;
  - by employees;
  - by work types.
- Payroll analytics uses hourly rates directly and monthly salaries proportionally to monthly hours norm.
- Added `+ Добавить объект...` action inside the work entry object dropdown.
- Selecting this action opens `Справочники` directly on the `Объекты` section.

### Verified

- Python syntax check passed with `py_compile`.
- Smoke test passed for:
  - analytics payroll calculation;
  - analytics widget constructor and result rendering;
  - object dropdown quick-add action;
  - update dialog default column widths.
- Rebuilt real `F:\!Морарь\!RAMOR\MPV - ProLOG\dist\ProLOG\ProLOG.exe`.
- Verified executable version information after rebuild.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-06 - Pay rates directory by position category

### Changed

- Removed pay/rate editing from the visible position directory UI.
- Position directory now defines only:
  - position name;
  - category rule;
  - student category permission;
  - employee group;
  - active status.
- Analytics payroll calculation now uses the dedicated pay rate for employee position and category.
- Employee analytics table now includes the employee category column.

### Added

- Added the `Оплата` directory section.
- Added SQLite `PayRates` table linked to `Positions` by `position_id`.
- `Оплата` automatically displays only active positions and categories configured in `Должности`.
- For category ranges such as `1-3`, pay rows are generated per category.
- For positions without categories, a single `—` pay row is generated.
- For positions with student category enabled, `0 (студент)` is included.
- Added pay rate editing by double click or `Редактировать`:
  - `Ставка`;
  - `Зарплата`;
  - amount.
- Existing hidden position salary values are used as initial values for generated pay rows.

### Verified

- Python syntax check passed with `py_compile`.
- Smoke test passed for:
  - PayRates table creation;
  - automatic pay rows from position/category rules;
  - hourly payroll calculation: `hours * rate`;
  - `Оплата` directory constructor;
  - analytics rendering with pay rates.
- Category synchronization smoke test passed:
  - `1-2` plus student category creates `0 (студент)`, `1`, `2`;
  - changing back to `1-3` without student category shows `1`, `2`, `3`.
- Rebuilt real `F:\!Морарь\!RAMOR\MPV - ProLOG\dist\ProLOG\ProLOG.exe`.
- Verified executable version information after rebuild.
- Executable startup smoke test passed: process stayed alive for 5 seconds and was then stopped manually.

## 2026-07-07 - GitHub project tracking

### Added

- Installed and enabled Git for Windows for ProLOG development workflow.
- Initialized local Git repository on branch `main`.
- Connected repository to GitHub remote:
  - `https://github.com/alexx-mor/ProLOG.git`.
- Created MVP baseline commit before legacy Excel import work:
  - `749de31 checkpoint: MVP baseline before legacy import`.
- Created and pushed MVP checkpoint tag:
  - `mvp-checkpoint-2026-07-07`.
- Pushed current `main` branch to GitHub.

### Notes

- Runtime and confidential files are intentionally excluded from GitHub:
  - `private/`;
  - `config.json`;
  - `data/`;
  - `dist/`;
  - `build/`;
  - `backups/`;
  - root Excel files.

