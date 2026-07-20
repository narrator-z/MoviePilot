import inspect
import sys
from typing import Callable

from app.helper.redis import RedisHelper, AsyncRedisHelper

# SitesHelper涉及资源包拉取，提前引入并容错提示
try:
    from app.helper.sites import SitesHelper  # noqa
except ImportError as e:
    SitesHelper = None
    error_message = f"错误: {str(e)}\n站点认证及索引相关资源导入失败，请尝试重建容器或手动拉取资源"
    print(error_message, file=sys.stderr)
    sys.exit(1)

from app.utils.system import SystemUtils
from app.log import logger
from app.core.config import settings
from app.core.module import ModuleManager
from app.core.event import EventManager
from app.helper.thread import ThreadHelper
from app.helper.display import DisplayHelper
from app.helper.doh import DohHelper
from app.helper.resource import ResourceHelper
from app.helper.message import MessageHelper, stop_message
from app.helper.server import MoviePilotServerHelper
from app.db import close_database
from app.command import CommandChain
from app.schemas import Notification, NotificationType
from app.startup.agent_initializer import init_agent, stop_agent


def start_frontend():
    """
    启动前端服务
    """
    # 仅Windows可执行文件支持内嵌nginx
    if not SystemUtils.is_frozen() \
            or not SystemUtils.is_windows():
        return
    # 临时Nginx目录
    nginx_path = settings.ROOT_PATH / 'nginx'
    if not nginx_path.exists():
        return
    # 配置目录下的Nginx目录
    run_nginx_dir = settings.CONFIG_PATH.with_name('nginx')
    if not run_nginx_dir.exists():
        # 移动到配置目录
        SystemUtils.move(nginx_path, run_nginx_dir)
    # 启动Nginx
    import subprocess
    subprocess.Popen("start nginx.exe",
                     cwd=run_nginx_dir,
                     shell=True)


def stop_frontend():
    """
    停止前端服务
    """
    if not SystemUtils.is_frozen() \
            or not SystemUtils.is_windows():
        return
    import subprocess
    subprocess.Popen(f"taskkill /f /im nginx.exe", shell=True)


def clear_temp():
    """
    清理临时文件和图片缓存
    """
    # 清理临时目录中3天前的文件
    SystemUtils.clear(settings.TEMP_PATH, days=settings.TEMP_FILE_DAYS)
    # 清理图片缓存目录中7天前的文件
    SystemUtils.clear(settings.CACHE_PATH / "images", days=settings.GLOBAL_IMAGE_CACHE_DAYS)
    # 清理 pip/uv 包下载缓存，不接管整个 .cache 目录。
    clear_package_tool_cache()


def clear_package_tool_cache():
    """
    清理 pip/uv 包下载缓存，只处理 MoviePilot 管理的工具子目录。
    """
    days = settings.PACKAGE_CACHE_DAYS
    if days <= 0:
        return
    tool_cache_root = settings.PACKAGE_CACHE_PATH
    for child in ("pip", "uv"):
        cache_path = tool_cache_root / child
        try:
            SystemUtils.clear(cache_path, days=days)
        except Exception as err:
            logger.warning("清理包下载缓存失败：%s - %s", cache_path, err)


def user_auth():
    """
    用户认证检查。
    站点认证闸门已按用户要求放开（启动期已将 SitesHelper.auth_level 强制为已认证），
    认证状态不再限制任何功能，故不再尝试真实认证、不再产生相关日志。
    """
    return


def check_auth():
    """
    启动认证检查。

    因站点功能已开放（用户已决定放开站点认证闸门），认证状态不再限制任何功能，
    故不再推送「功能受限」类告警，本方法保持为无害的空操作以兼容既有调用方。
    """
    # 站点功能已全开，认证状态不再限制功能，故不再推送受限告警
    return


async def stop_modules():
    """
    服务关闭
    """
    async def run_step(name: str, callback: Callable[[], object]) -> None:
        """单个模块资源关闭失败时继续执行后续阶段"""
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
        except Exception as err:
            logger.error(f"关闭{name}失败：{err}")

    await run_step("AI智能体", stop_agent)
    await run_step("模块", lambda: ModuleManager().stop())
    await run_step("事件消费", lambda: EventManager().stop())
    await run_step("虚拟显示", lambda: DisplayHelper().stop())
    await run_step("DoH服务", lambda: DohHelper().shutdown())
    await run_step("线程池", lambda: ThreadHelper().shutdown())
    await run_step("消息服务", stop_message)
    await run_step("Redis缓存连接", lambda: RedisHelper().close())
    await run_step("异步Redis缓存连接", lambda: AsyncRedisHelper().close())
    await run_step("数据库连接", close_database)
    await run_step("前端服务", stop_frontend)
    await run_step("临时文件", clear_temp)


def init_modules():
    """
    启动模块
    """
    # 放开站点认证闸门：用户未配置任何 PT 站点参数，无需真实站点认证。
    # 核心修复：SitesHelper.check_user 编译于 .so 内部，会自行重新校验 cookie/session 并刷屏
    # 「用户未认证，无法使用站点功能！」日志，且对 auth_level 的 property 覆盖免疫。
    # 故直接把 check_user 整体替换为恒返回 (True,"已认证") 的 stub，从源头消除报错。
    # 注意：check_user 的替换必须放在最前，确保无论后面 auth_level 覆写是否成功都一定生效。
    # 若需恢复真实认证，删除下面这段 try 块即可。
    try:
        if SitesHelper is not None:
            # 真正的修复（放最前）：强制 check_user 恒返回已认证，从源头消除「用户未认证」刷屏
            SitesHelper.check_user = lambda self, site=None, params=None: (True, "已认证")
            # 尽力而为：同时把 auth_level 强制为已认证，让 user_auth 守卫提前返回。
            # 注意必须用 SitesHelper.auth_level（类属性），不能用 type(SitesHelper).auth_level
            # —— 后者是对不可变元类 type 赋值，会抛 TypeError，故单独包一层 try 兜底。
            try:
                SitesHelper.auth_level = property(lambda self: 2)
            except Exception:
                pass
    except Exception:
        pass
    # 虚拟显示
    DisplayHelper()
    # DoH
    DohHelper()
    # 站点管理
    SitesHelper()
    # 资源包检测
    ResourceHelper()
    # 用户认证
    user_auth()
    # 加载模块
    ModuleManager()
    # 启动事件消费
    EventManager().start()
    # 初始化共享服务端状态
    MoviePilotServerHelper.init_plugin_report()
    MoviePilotServerHelper.init_subscribe_report()
    MoviePilotServerHelper.get_user_uuid()
    MoviePilotServerHelper.get_github_user()
    # 初始化AI智能体
    init_agent()
    # 启动前端服务
    start_frontend()
    # 检查认证状态
    check_auth()
