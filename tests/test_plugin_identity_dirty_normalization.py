"""v3.0.3 存量迁移写出的矛盾身份在读取时被归一化，避免插件页与启动迁移 500。

这些测试直接驱动 app.db.adapters.pluginidentity._to_record，不依赖数据库，
覆盖 NAS pluginidentity 表中实际出现的脏数据形态：
- 未绑定身份（unknown）却带 bound_at / 非 legacy_unbound 依据；
- 已绑定身份（third_party 等）却缺少可信来源键。
"""

from app.application.plugin.declaration import PluginDeclaredMetadata
from app.application.plugin.identity import (
    PluginBindingBasis,
    PluginPayloadSourceType,
    TrustedPluginSourceType,
)
from app.db.adapters.pluginidentity import _to_record
from app.db.models.pluginidentity import PluginIdentity as PluginIdentityModel

ISO = "2026-08-31T10:19:45+00:00"


def _model(**overrides) -> PluginIdentityModel:
    """构造一行 pluginidentity 持久化模型（不落库）。"""
    defaults = dict(
        plugin_id="DemoPlugin",
        normalized_plugin_id="demoplugin",
        trusted_source_type="unknown",
        trusted_source_key=None,
        binding_basis="legacy_unbound",
        payload_source_type="unknown",
        payload_source_key=None,
        declared_version=None,
        package_generation=None,
        declared_metadata=None,
        payload_receipt=None,
        revision=1,
        created_at=ISO,
        updated_at=ISO,
        bound_at=None,
        payload_applied_at=None,
    )
    defaults.update(overrides)
    return PluginIdentityModel(**defaults)


def test_unknown_with_bound_at_is_normalized() -> None:
    """未绑定身份携带 bound_at 且依据非法时，清空绑定时间并降级为 legacy_unbound。"""
    record = _to_record(
        _model(
            trusted_source_type="unknown",
            binding_basis="tofu",
            bound_at=ISO,
        )
    )
    assert record.trusted_source_type is TrustedPluginSourceType.UNKNOWN
    assert record.bound_at is None
    assert record.binding_basis is PluginBindingBasis.LEGACY_UNBOUND
    assert record.payload_source_type is PluginPayloadSourceType.UNKNOWN
    assert record.trusted_source_key is None


def test_unknown_with_source_key_is_normalized() -> None:
    """未绑定身份携带可信来源键时，清空来源键并降级为依据。"""
    record = _to_record(
        _model(
            trusted_source_type="unknown",
            trusted_source_key="github:example/foo",
            binding_basis="tofu",
            bound_at=ISO,
        )
    )
    assert record.trusted_source_type is TrustedPluginSourceType.UNKNOWN
    assert record.trusted_source_key is None
    assert record.bound_at is None
    assert record.binding_basis is PluginBindingBasis.LEGACY_UNBOUND


def test_bound_without_source_key_is_downgraded_to_unknown() -> None:
    """已绑定身份缺少可信来源键时，整体降级为未绑定交由后续迁移自愈。"""
    record = _to_record(
        _model(
            trusted_source_type="third_party",
            trusted_source_key=None,
            binding_basis="tofu",
            bound_at=ISO,
        )
    )
    assert record.trusted_source_type is TrustedPluginSourceType.UNKNOWN
    assert record.trusted_source_key is None
    assert record.bound_at is None
    assert record.binding_basis is PluginBindingBasis.LEGACY_UNBOUND


def test_bound_without_bound_at_is_downgraded_to_unknown() -> None:
    """已绑定身份缺少绑定时间时同样降级为未绑定。"""
    record = _to_record(
        _model(
            trusted_source_type="third_party",
            trusted_source_key="github:example/foo",
            binding_basis="tofu",
            bound_at=None,
        )
    )
    assert record.trusted_source_type is TrustedPluginSourceType.UNKNOWN
    assert record.bound_at is None
    assert record.binding_basis is PluginBindingBasis.LEGACY_UNBOUND


def test_valid_unknown_row_is_preserved() -> None:
    """合法的未绑定存量身份不被改动。"""
    record = _to_record(
        _model(
            trusted_source_type="unknown",
            binding_basis="legacy_unbound",
        )
    )
    assert record.trusted_source_type is TrustedPluginSourceType.UNKNOWN
    assert record.binding_basis is PluginBindingBasis.LEGACY_UNBOUND


def test_local_identity_is_preserved() -> None:
    """本地插件身份（trusted=unknown + payload=local 携带本地载荷）不被归一化破坏。"""
    record = _to_record(
        _model(
            trusted_source_type="unknown",
            trusted_source_key=None,
            binding_basis="local_only",
            payload_source_type="local",
            payload_source_key=None,
            declared_version="1.0.0",
            package_generation="v3",
            declared_metadata=PluginDeclaredMetadata.from_package(
                {
                    "name": "Demo",
                    "description": "Demo plugin",
                    "v3": True,
                    "v3t": False,
                    "release": True,
                },
                declaration_version="1.0.0",
                manifest_matches_payload=True,
            ).to_json(),
            payload_receipt="sha256:" + "0" * 64,
            payload_applied_at=ISO,
        )
    )
    assert record.trusted_source_type is TrustedPluginSourceType.UNKNOWN
    assert record.binding_basis is PluginBindingBasis.LOCAL_ONLY
    assert record.payload_source_type is PluginPayloadSourceType.LOCAL
    assert record.declared_version == "1.0.0"


def test_valid_official_row_is_preserved() -> None:
    """合法的官方绑定身份不被改动。"""
    record = _to_record(
        _model(
            trusted_source_type="official",
            trusted_source_key="github:jxxghp/moviepilot-plugins",
            binding_basis="official_default",
            payload_source_type="official",
            payload_source_key="github:jxxghp/moviepilot-plugins",
            declared_version="1.0.0",
            package_generation="v3",
            declared_metadata=PluginDeclaredMetadata.from_package(
                {
                    "name": "Demo",
                    "description": "Demo plugin",
                    "v3": True,
                    "v3t": False,
                    "release": True,
                },
                declaration_version="1.0.0",
                manifest_matches_payload=True,
            ).to_json(),
            payload_receipt="sha256:" + "0" * 64,
            bound_at=ISO,
            payload_applied_at=ISO,
        )
    )
    assert record.trusted_source_type is TrustedPluginSourceType.OFFICIAL
    assert record.trusted_source_key == "github:jxxghp/moviepilot-plugins"
    assert record.bound_at is not None
