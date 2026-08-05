from models import ProductItem
from ui.dialogs import _sort_products


def test_products_can_be_sorted_by_serial_and_readiness() -> None:
    products = [
        ProductItem(object_id=1, name="ШУ 10", serial_number="10", readiness_percent=30),
        ProductItem(object_id=1, name="ШУ 2", serial_number="2", readiness_percent=80),
        ProductItem(object_id=1, name="ШУ 1", serial_number="1", readiness_percent=50),
    ]

    assert [item.serial_number for item in _sort_products(products, "serial_asc")] == [
        "1",
        "2",
        "10",
    ]
    assert [item.readiness_percent for item in _sort_products(products, "readiness_desc")] == [
        80,
        50,
        30,
    ]
