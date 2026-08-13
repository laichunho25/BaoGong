import time
import uuid

from apps.core.models import BaseModel, uuid7


class TestUuid7:
    def test_returns_stdlib_uuid(self):
        value = uuid7()
        assert isinstance(value, uuid.UUID)
        assert type(value) is uuid.UUID

    def test_version_is_7(self):
        assert uuid7().version == 7

    def test_values_are_unique(self):
        assert len({uuid7() for _ in range(1000)}) == 1000

    def test_values_are_time_ordered(self):
        first = uuid7()
        time.sleep(0.01)
        second = uuid7()
        assert first.hex < second.hex


class TestBaseModel:
    def test_is_abstract(self):
        assert BaseModel._meta.abstract is True

    def test_declares_uuid_pk_and_timestamps(self):
        fields = {f.name for f in BaseModel._meta.fields}
        assert {"id", "created_at", "updated_at"} <= fields
        assert BaseModel._meta.get_field("id").primary_key is True
        assert BaseModel._meta.get_field("id").default is uuid7
