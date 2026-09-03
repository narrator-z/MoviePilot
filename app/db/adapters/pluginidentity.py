"""插件来源身份 Application Port 的 SQLAlchemy 实现。"""

from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.plugin.declaration import PluginDeclaredMetadata
from app.application.plugin.identity import (
    BindLocalPluginIdentityCommand,
    BindOnlinePluginIdentityCommand,
    ChangePluginIdentitySourceCommand,
    PluginBindingBasis,
    PluginIdentity,
    PluginIdentityConflictError,
    PluginPayloadSourceType,
    TrustedPluginSourceType,
    WritePluginIdentityCommand,
    normalize_physical_plugin_id,
)
from app.db.models.pluginidentity import PluginIdentity as IdentityModel
from app.db.oper.pluginidentity import PluginIdentityOper
from app.db.uow import SqlAlchemyUnitOfWork
from app.runtime.log import logger


def _parse_datetime(value: str | None) -> datetime | None:
    """把数据库 ISO 时间还原为带时区应用值。"""
    return datetime.fromisoformat(value) if value else None


def _normalize_plugin_id_for_read(plugin_id: str) -> str | None:
    """读取历史安装清单时，将不属于身份合同的条目标记为无身份。"""
    if not isinstance(plugin_id, str):
        return None
    try:
        return normalize_physical_plugin_id(plugin_id)
    except ValueError:
        return None


def _to_record(model: IdentityModel) -> PluginIdentity:
    """把持久化模型映射为已校验的应用身份。

    对 v3.0.3 迁移写出的矛盾存量身份做归一化（不变量见
    PluginIdentity.__post_init__）：未绑定身份不得携带可信来源或绑定时间，
    已绑定身份必须携带规范来源和绑定时间。归一化后的身份可由后续迁移或
    用户操作自然自愈写回，避免插件页与启动迁移因脏数据而 500 崩溃。
    """
    trusted_source_type = TrustedPluginSourceType(model.trusted_source_type)
    trusted_source_key = model.trusted_source_key
    binding_basis = PluginBindingBasis(model.binding_basis)
    bound_at = _parse_datetime(model.bound_at)

    # 归一化可信来源一侧的矛盾：未绑定身份不得携带可信来源或绑定时间，
    # 已绑定身份必须携带规范来源和绑定时间。
    if trusted_source_type is TrustedPluginSourceType.UNKNOWN:
        if trusted_source_key is not None or bound_at is not None:
            trusted_source_key = None
            bound_at = None
        if binding_basis not in {
            PluginBindingBasis.LEGACY_UNBOUND,
            PluginBindingBasis.LOCAL_ONLY,
        }:
            binding_basis = PluginBindingBasis.LEGACY_UNBOUND
    elif trusted_source_key is None or bound_at is None:
        # 已绑定却缺来源或绑定时间 → 降级为未绑定，交给后续迁移或用户操作自愈
        trusted_source_type = TrustedPluginSourceType.UNKNOWN
        trusted_source_key = None
        bound_at = None
        binding_basis = PluginBindingBasis.LEGACY_UNBOUND

    # 载荷一侧保持原值，仅在其自身矛盾时归一化；未绑定身份(local)可合法携带本地载荷。
    payload_source_type = PluginPayloadSourceType(model.payload_source_type)
    payload_source_key = model.payload_source_key
    declared_version = model.declared_version
    package_generation = model.package_generation
    declared_metadata = (
        PluginDeclaredMetadata.from_storage(model.declared_metadata)
        if model.declared_metadata is not None
        else None
    )
    payload_receipt = model.payload_receipt
    payload_applied_at = _parse_datetime(model.payload_applied_at)

    if payload_source_type is PluginPayloadSourceType.UNKNOWN:
        # 未知载荷不得携带任何载荷事实
        payload_source_key = None
        declared_version = None
        package_generation = None
        declared_metadata = None
        payload_receipt = None
        payload_applied_at = None
    elif payload_source_type in {
        PluginPayloadSourceType.OFFICIAL,
        PluginPayloadSourceType.THIRD_PARTY,
    } and payload_source_key is None:
        # 在线载荷必须携带规范来源键；缺失则降级为未知载荷
        payload_source_type = PluginPayloadSourceType.UNKNOWN
        payload_source_key = None
        declared_version = None
        package_generation = None
        declared_metadata = None
        payload_receipt = None
        payload_applied_at = None

    try:
        return PluginIdentity(
            plugin_id=model.plugin_id,
            normalized_plugin_id=model.normalized_plugin_id,
            trusted_source_type=trusted_source_type,
            trusted_source_key=trusted_source_key,
            binding_basis=binding_basis,
            payload_source_type=payload_source_type,
            payload_source_key=payload_source_key,
            declared_version=declared_version,
            package_generation=package_generation,
            declared_metadata=declared_metadata,
            payload_receipt=payload_receipt,
            revision=model.revision,
            created_at=datetime.fromisoformat(model.created_at),
            updated_at=datetime.fromisoformat(model.updated_at),
            bound_at=bound_at,
            payload_applied_at=payload_applied_at,
        )
    except ValueError:
        # 兜底：任何未能识别的存量矛盾数据都降级为未绑定身份，保证读取不崩溃
        logger.warning(
            "插件 %s 的来源身份存在未识别的矛盾字段，已降级为未绑定身份",
            model.plugin_id,
        )
        return PluginIdentity(
            plugin_id=model.plugin_id,
            normalized_plugin_id=model.normalized_plugin_id,
            trusted_source_type=TrustedPluginSourceType.UNKNOWN,
            trusted_source_key=None,
            binding_basis=PluginBindingBasis.LEGACY_UNBOUND,
            payload_source_type=PluginPayloadSourceType.UNKNOWN,
            payload_source_key=None,
            declared_version=None,
            package_generation=None,
            declared_metadata=None,
            payload_receipt=None,
            revision=model.revision,
            created_at=datetime.fromisoformat(model.created_at),
            updated_at=datetime.fromisoformat(model.updated_at),
            bound_at=None,
            payload_applied_at=None,
        )


def _to_model(identity: PluginIdentity) -> IdentityModel:
    """把应用身份映射为不拥有事务的持久化模型。"""
    return IdentityModel(
        plugin_id=identity.plugin_id,
        normalized_plugin_id=identity.normalized_plugin_id,
        trusted_source_type=identity.trusted_source_type.value,
        trusted_source_key=identity.trusted_source_key,
        binding_basis=identity.binding_basis.value,
        payload_source_type=identity.payload_source_type.value,
        payload_source_key=identity.payload_source_key,
        declared_version=identity.declared_version,
        package_generation=identity.package_generation,
        declared_metadata=(
            identity.declared_metadata.to_json()
            if identity.declared_metadata is not None
            else None
        ),
        payload_receipt=identity.payload_receipt,
        revision=identity.revision,
        created_at=identity.created_at.isoformat(),
        updated_at=identity.updated_at.isoformat(),
        bound_at=identity.bound_at.isoformat() if identity.bound_at else None,
        payload_applied_at=(
            identity.payload_applied_at.isoformat()
            if identity.payload_applied_at
            else None
        ),
    )


class _SqlAlchemyIdentityRepository:
    """绑定一个调用方 Session 的来源身份仓储。"""

    def __init__(self, session: Session) -> None:
        """保存由事务适配器拥有的 Session。"""
        self._oper = PluginIdentityOper(session)

    def get(self, plugin_id: str) -> PluginIdentity | None:
        """读取并映射指定来源身份。"""
        model = self._oper.get_by_plugin_id(plugin_id)
        return _to_record(model) if model else None

    def list(self, plugin_ids: Sequence[str]) -> list[PluginIdentity]:
        """批量读取并映射指定来源身份。"""
        return [
            _to_record(model)
            for model in self._oper.list_by_plugin_ids(plugin_ids)
        ]

    def stage_create(self, identity: PluginIdentity) -> None:
        """暂存首次身份。"""
        try:
            self._oper.stage_create(_to_model(identity))
        except IntegrityError as error:
            raise PluginIdentityConflictError(
                f"插件 {identity.plugin_id} 的来源身份已存在"
            ) from error

    def stage_replace(
        self,
        identity: PluginIdentity,
        *,
        expected_revision: int,
    ) -> bool:
        """按 revision 条件暂存替换。"""
        return self._oper.stage_replace(
            _to_model(identity),
            expected_revision=expected_revision,
        )


class TransactionalPluginIdentityStore:
    """为每次来源身份读写创建独占同步数据库会话。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        """保存由组合根提供的同步 Session 工厂。"""
        self._session_factory = session_factory

    def get(self, plugin_id: str) -> PluginIdentity | None:
        """在短会话内读取指定物理插件身份；不合规历史 ID 视为未建立身份。"""
        normalized_id = _normalize_plugin_id_for_read(plugin_id)
        if normalized_id is None:
            return None
        session = self._session_factory()
        try:
            return _SqlAlchemyIdentityRepository(session).get(normalized_id)
        finally:
            session.close()

    def list(self, plugin_ids: Sequence[str]) -> list[PluginIdentity]:
        """批量读取规范化插件身份，并忽略不合规的历史安装清单项。"""
        normalized_ids: list[str] = []
        seen: set[str] = set()
        for plugin_id in plugin_ids:
            normalized_id = _normalize_plugin_id_for_read(plugin_id)
            if normalized_id is None or normalized_id in seen:
                continue
            seen.add(normalized_id)
            normalized_ids.append(normalized_id)
        session = self._session_factory()
        try:
            return _SqlAlchemyIdentityRepository(session).list(tuple(normalized_ids))
        finally:
            session.close()

    def compare_and_set(
        self,
        identity: PluginIdentity,
        *,
        expected_revision: int | None,
    ) -> PluginIdentity:
        """在一个事务内执行首次创建或 revision 条件替换。"""
        session = self._session_factory()
        try:
            return WritePluginIdentityCommand(
                repository=_SqlAlchemyIdentityRepository(session),
                unit_of_work=SqlAlchemyUnitOfWork(session),
            ).execute(identity, expected_revision=expected_revision)
        finally:
            session.close()

    def change_source(
        self,
        identity: PluginIdentity,
        *,
        expected_revision: int,
    ) -> PluginIdentity:
        """在独占事务内提交明确的在线来源转换。"""
        session = self._session_factory()
        try:
            return ChangePluginIdentitySourceCommand(
                repository=_SqlAlchemyIdentityRepository(session),
                unit_of_work=SqlAlchemyUnitOfWork(session),
            ).execute(identity, expected_revision=expected_revision)
        finally:
            session.close()

    def bind_local(
        self,
        identity: PluginIdentity,
        *,
        expected_revision: int,
    ) -> PluginIdentity:
        """在独占事务内提交 legacy_unbound 到 local_only 的转换。"""
        session = self._session_factory()
        try:
            return BindLocalPluginIdentityCommand(
                repository=_SqlAlchemyIdentityRepository(session),
                unit_of_work=SqlAlchemyUnitOfWork(session),
            ).execute(identity, expected_revision=expected_revision)
        finally:
            session.close()

    def bind_online(
        self,
        identity: PluginIdentity,
        *,
        expected_revision: int,
    ) -> PluginIdentity:
        """在独占事务内提交未绑定身份的首次在线来源绑定。"""
        session = self._session_factory()
        try:
            return BindOnlinePluginIdentityCommand(
                repository=_SqlAlchemyIdentityRepository(session),
                unit_of_work=SqlAlchemyUnitOfWork(session),
            ).execute(identity, expected_revision=expected_revision)
        finally:
            session.close()
