# WorkBot identity and production-context matching

## Identity source of truth

- `Employees.id` is the permanent ProLOG employee identifier.
- `MaxUserBindings.max_user_id` links one MAX account to one employee.
- A mobile phone belongs to `Employees`, not to a MAX message or alias.
- MAX profile names and report text are hints only; they never replace a saved binding.
- The MAX API payload currently used by WorkBot contains user ID, first name, last name and username, but no phone number. Phones are entered in ProLOG or imported from Excel.

## Product and object resolution

Resolution order:

1. Confirmed product alias.
2. Product serial number.
3. Product code.
4. Product name.
5. Manual operator selection.

Every product belongs to one object. When a product is recognized, ProLOG fills its object automatically. A conflicting object and product combination is blocked for review.

Messages containing several equally strong product references are intentionally not resolved automatically. The operator must select the correct product or create separate work-log entries when the hours need to be distributed between products.

## Future AI layer

The deterministic matcher remains the first layer because production identifiers must be explainable and reproducible. A future AI interpreter may process only unresolved text and return ranked suggestions with confidence and evidence. It must not create employees, objects, products or work-log entries without user confirmation.

For confidential deployment, prefer a local model or an approved enterprise endpoint. The integration boundary is `integrations/workbot`; an AI interpreter can be added there without coupling it to the PySide6 UI or SQLite source adapter.
