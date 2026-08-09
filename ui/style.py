"""Application-wide Qt stylesheet."""

APP_STYLESHEET = """
QMainWindow, QDialog { background: #f4f6f8; }
QWidget { font-size: 10pt; }
QMenuBar { background: #f4f6f8; border-bottom: 1px solid #d7dde3; }
QMenuBar::item { padding: 6px 10px; background: transparent; }
QMenuBar::item:selected { background: #e8eef5; }
QTabWidget::pane {
    border: 1px solid #d7dde3;
    background: #f4f6f8;
}
QTabBar::tab {
    background: #e9eef3;
    border: 1px solid #d7dde3;
    padding: 7px 16px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #ffffff;
    border-bottom-color: #ffffff;
    font-weight: 600;
}
QGroupBox {
    border: 1px solid #d7dde3;
    border-radius: 6px;
    margin-top: 4px;
    padding: 12px;
    background: #ffffff;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
    min-height: 30px;
    padding: 4px 12px;
}
QLineEdit, QComboBox, QDateEdit {
    min-height: 28px;
}
QLineEdit {
    background: #ffffff;
    border: 1px solid #98a4af;
    padding: 2px 6px;
}
QLineEdit:hover {
    border: 1px solid #6f7b86;
}
QLineEdit:focus {
    border: 1px solid #2f80ed;
}
QLineEdit:disabled, QLineEdit:read-only {
    background: #eef2f6;
    color: #52606d;
    border: 1px solid #cfd7df;
}
QGroupBox#AuthGroupBox {
    border: 1px solid #d7dde3;
    border-radius: 6px;
    margin-top: 18px;
    padding: 18px 14px 14px 14px;
    background: #ffffff;
}
QGroupBox#AuthGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    top: 6px;
    padding: 0 6px;
    background: #f4f6f8;
    color: #1f2328;
    font-weight: 600;
}
QLabel#AuthHint {
    color: #687483;
    font-size: 9pt;
}
QTableWidget {
    background: #ffffff;
    border: 1px solid #d7dde3;
    gridline-color: #e5e9ef;
    selection-background-color: #d8ebff;
    selection-color: #1f2328;
}
QHeaderView::section {
    background: #eef2f6;
    border: 0;
    border-right: 1px solid #d7dde3;
    border-bottom: 1px solid #d7dde3;
    padding: 6px;
    font-weight: 600;
}
QLabel#SectionTitle, QLabel#DialogTitle {
    font-size: 12pt;
    font-weight: 600;
    color: #1f2328;
    padding: 4px 0;
}
QLabel#WizardTitle {
    font-size: 18pt;
    font-weight: 700;
    color: #1f2328;
}
QLabel#WizardSubtitle {
    font-size: 10.5pt;
    color: #4f5b67;
}
QLabel#EmployeeInfo {
    color: #1f2328;
    padding: 3px 0;
    font-weight: 500;
}
QLabel#AnalyticsMetricTitle {
    color: #5b6672;
    font-weight: 600;
}
QLabel#AnalyticsMetricValue {
    color: #1f2328;
    font-size: 13pt;
    font-weight: 700;
    padding: 2px 18px 2px 0;
}
QFrame#EmployeeContext {
    background: #f7fafc;
    border: 1px solid #d7dde3;
    border-radius: 4px;
}
QFrame#ProductionHeader {
    background: #f8fafc;
    border: 1px solid #d7dde3;
    border-radius: 6px;
}
QLabel#ProductionModeNote {
    color: #425466;
    background: #eef5fb;
    border: 1px solid #cbdbe8;
    border-radius: 4px;
    padding: 8px;
}
QToolTip {
    background: #ffffff;
    color: #1f2328;
    border: 1px solid #c9d2dc;
    padding: 6px 8px;
}
QFrame#AboutHeader {
    background: #ffffff;
    border: 1px solid #d7dde3;
    border-radius: 8px;
}
QLabel#AboutTitle {
    font-size: 18pt;
    font-weight: 700;
    color: #1f2328;
}
QLabel#AboutVersion {
    color: #52606d;
    font-weight: 600;
}
QLabel#AboutNote {
    color: #5d6b78;
}
QLabel#AboutDescription {
    color: #27313b;
}
QFrame#AboutLegal {
    background: #f8fafc;
    border: 1px solid #d7dde3;
    border-radius: 6px;
}
QLabel#AboutLegalTitle {
    font-weight: 700;
    color: #1f2328;
}
QLabel#AboutRights {
    color: #35414d;
}
"""
