import json
import re
from datetime import UTC, datetime

from spider.core.models import Item, ItemKind, ItemPage, new_ulid

CROCKFORD = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_ulid_is_26_crockford_characters():
    assert CROCKFORD.match(new_ulid())


def test_ulids_are_unique():
    assert len({new_ulid() for _ in range(1000)}) == 1000


def test_ulids_sort_by_creation_time():
    first = new_ulid()
    second = new_ulid()
    assert first < second


def test_item_roundtrips_through_json():
    item = Item(
        id=new_ulid(),
        kind=ItemKind.text,
        name="notes",
        size=5,
        sha256="a" * 64,
        content_type="text/plain; charset=utf-8",
        created_at=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
        source_device="parks-macbook-air",
        preview="hello",
    )
    restored = Item.model_validate(json.loads(item.model_dump_json()))
    assert restored == item


def test_item_kind_is_a_plain_string():
    assert ItemKind.file == "file"
    assert f"{ItemKind.text}" == "text"


def test_item_page_defaults_to_no_cursor():
    page = ItemPage(items=[])
    assert page.next_before is None
