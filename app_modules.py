"""Application module access rules."""

from __future__ import annotations

from auth import ROLE_ADMIN, ROLE_USER

MODULE_REPORT_FILLING = "report_filling"
MODULE_REPORT_VIEWING = "report_viewing"
MODULE_REPORT_EXPORT = "report_export"
MODULE_EMPLOYEE_ADMIN = "employee_admin"
MODULE_DIRECTORIES = "directories"
MODULE_LEGACY_IMPORT = "legacy_import"
MODULE_PAYROLL = "payroll"
MODULE_UPDATES = "updates"
MODULE_USERS = "users"

ADMIN_MODULES = {
    MODULE_REPORT_FILLING,
    MODULE_REPORT_VIEWING,
    MODULE_REPORT_EXPORT,
    MODULE_EMPLOYEE_ADMIN,
    MODULE_DIRECTORIES,
    MODULE_LEGACY_IMPORT,
    MODULE_PAYROLL,
    MODULE_UPDATES,
    MODULE_USERS,
}
USER_MODULES = {
    MODULE_REPORT_FILLING,
    MODULE_REPORT_VIEWING,
    MODULE_REPORT_EXPORT,
    MODULE_DIRECTORIES,
}
ROLE_MODULES = {
    ROLE_ADMIN: ADMIN_MODULES,
    ROLE_USER: USER_MODULES,
}


def role_can_access(role: str, module: str) -> bool:
    return module in ROLE_MODULES.get(role, set())
