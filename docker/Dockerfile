FROM python:3.14-slim-trixie AS base


# 准备软件包
FROM base AS prepare_package

ENV LANG="C.UTF-8" \
    TZ="Asia/Shanghai" \
    HOME="/moviepilot" \
    CONFIG_DIR="/config" \
    TERM="xterm" \
    DISPLAY=:987 \
    PUID=0 \
    PGID=0 \
    UMASK=000 \
    VENV_PATH="/opt/venv"

ENV PATH="${VENV_PATH}/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    gettext-base \
    locales \
    procps \
    gosu \
    bash \
    ca-certificates \
    curl \
    wget \
    git \
    gh \
    busybox \
    tini \
    cron \
    jq \
    ripgrep \
    less \
    unzip \
    fuse3 \
    rsync \
    openssh-client \
    sshpass \
    iproute2 \
    netcat-openbsd \
    lsof \
    nano \
    unar \
    openssl \
    postgresql-client-18 \
    libchromaprint-tools \
    libjemalloc2 \
    && dpkg-reconfigure --frontend noninteractive tzdata \
    && curl https://rclone.org/install.sh | bash \
    && ln -s /usr/lib/*-linux-gnu/libjemalloc.so.2 /usr/local/lib/libjemalloc.so \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf \
    /tmp/* \
    /var/lib/apt/lists/* \
    /var/tmp/*


# 准备 python 环境
FROM base AS prepare_venv

# 设置环境变量
ENV LANG="C.UTF-8" \
    TZ="Asia/Shanghai" \
    VENV_PATH="/opt/venv"

ENV PATH="${VENV_PATH}/bin:${PATH}"

# 安装系统构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    busybox \
    jq \
    wget

# 安装 Python 构建依赖并创建虚拟环境
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY scripts/uv-pip-compat.sh /usr/local/bin/uv-pip-compat
RUN set -eux; \
    UV_ARCH="$(uname -m)"; \
    case "${UV_ARCH}" in \
        x86_64) UV_ARCH="x86_64" ;; \
        aarch64) UV_ARCH="aarch64" ;; \
        *) echo "unsupported arch ${UV_ARCH}" ; exit 1 ;; \
    esac; \
    curl -LsSf "https://github.com/astral-sh/uv/releases/download/0.12.5/uv-${UV_ARCH}-unknown-linux-gnu.tar.gz" -o /tmp/uv.tar.gz; \
    tar -xzf /tmp/uv.tar.gz -C /tmp; \
    cp "/tmp/uv-${UV_ARCH}-unknown-linux-gnu/uv" /usr/local/bin/uv; \
    chmod +x /usr/local/bin/uv; \
    /usr/local/bin/uv --version; \
    chmod +x /usr/local/bin/uv-pip-compat \
    && uv venv ${VENV_PATH} \
    && UV_PROJECT_ENVIRONMENT=${VENV_PATH} uv sync \
        --locked \
        --no-dev \
        --no-install-project \
        --group runtime-standard \
    && ln -sf /usr/local/bin/uv-pip-compat ${VENV_PATH}/bin/pip \
    && ln -sf /usr/local/bin/uv-pip-compat ${VENV_PATH}/bin/pip3 \
    && ln -sf /usr/local/bin/uv-pip-compat ${VENV_PATH}/bin/pip3.12 \
    && ln -sf /usr/local/bin/uv-pip-compat ${VENV_PATH}/bin/pip-compile \
    && ln -sf /usr/local/bin/uv-pip-compat ${VENV_PATH}/bin/pip-sync \
    && test -x ${VENV_PATH}/bin/python && echo "[venv] python ok: $(${VENV_PATH}/bin/python --version)" \
    && test -x ${VENV_PATH}/bin/playwright || ln -sf ${VENV_PATH}/bin/python ${VENV_PATH}/bin/playwright

# 下载准备代码
FROM prepare_package AS prepare_code

WORKDIR /app

ARG GITHUB_TOKEN

COPY . .
RUN FRONTEND_VERSION=$(sed -n "s/^FRONTEND_VERSION\s*=\s*'\([^']*\)'/\1/p" /app/version.py) \
    && dl() { curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors ${GITHUB_TOKEN:+--header "Authorization: Bearer ${GITHUB_TOKEN}"} "$@"; } \
    && dl "https://github.com/jxxghp/MoviePilot-Frontend/releases/download/${FRONTEND_VERSION}/dist.zip" | busybox unzip -d / - \
    && mv /dist /public \
    && dl "https://github.com/jxxghp/MoviePilot-Plugins/archive/refs/heads/main.zip" | busybox unzip -d /tmp - \
    && cp -rf /tmp/MoviePilot-Plugins-main/plugins.v2/. /app/app/plugins/ \
    && cat /tmp/MoviePilot-Plugins-main/package.json | jq -r 'to_entries[] | select(.value.v2 == true) | .key' | awk '{print tolower($0)}' | \
    while read -r i; do if [ ! -d "/app/app/plugins/$i" ]; then mv "/tmp/MoviePilot-Plugins-main/plugins/$i" "/app/app/plugins/"; else echo "跳过 $i"; fi; done \
    && dl "https://raw.githubusercontent.com/jxxghp/MoviePilot-Resources/main/resources.v3/user.sites.v3.bin" -o /app/app/application/site/user.sites.v3.bin \
    && python_ver=$(python3 -c 'import sys; print(f"cpython-{sys.version_info.major}{sys.version_info.minor}")') \
    && ARCH=$(uname -m) \
    && if [ "$ARCH" = "aarch64" ]; then SUFFIX="aarch64-linux-gnu"; else SUFFIX="x86_64-linux-gnu"; fi \
    && dl "https://raw.githubusercontent.com/jxxghp/MoviePilot-Resources/main/resources.v3/sites.${python_ver}-${SUFFIX}.so" -o "/app/app/application/site/sites.${python_ver}-${SUFFIX}.so"

# 叠加拉取用户自定义插件仓库（narrator-z/MoviePilot-PluginsV2），覆盖官方同名插件，
# 以便镜像直接使用该仓库中最新的 jackettextend / prowlarrextend 等插件。
# 置于官方插件拉取之后执行，确保用户仓库版本优先于官方同名插件。
# 采用「叠加」而非「替换」：官方 jxxghp/MoviePilot-Plugins 已经提供了完整的插件生态打底，
# 这里仅用用户仓库中的同名插件覆盖，官方其他插件（非同名插件）得以完整保留。
# 注意：V2 下载必须钉到具体 commit SHA，不能再用 refs/heads/main.zip。
# 原因：Docker 按 RUN 指令文本做层缓存，main.zip 的 URL 文本不变就会命中旧缓存，
# 导致「改了 V2 插件但镜像里还是旧版」（本次 jackettindexer 6.0.0 不生效即此坑）。
# 纪律：每次修改 narrator-z/MoviePilot-PluginsV2 后，同步把下方 V2_REF 顶到最新 commit，
# 这样 URL 文本变化 → 缓存失效 → 重新拉取最新 V2 覆盖层。
RUN dl() { curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors ${GITHUB_TOKEN:+--header "Authorization: Bearer ${GITHUB_TOKEN}"} "$@"; } \
    && V2_REF=9e03d09bd18e91c250465d3b8a36c8453db6ec3a \
    && dl "https://github.com/narrator-z/MoviePilot-PluginsV2/archive/${V2_REF}.zip" | busybox unzip -d /tmp - \
    && cp -rf /tmp/MoviePilot-PluginsV2-*/plugins.v2/. /app/app/plugins/ \
    && rm -rf /tmp/MoviePilot-PluginsV2-*

# final 阶段: 安装运行时依赖和配置最终镜像
FROM prepare_package AS final

ENV LD_PRELOAD="/usr/local/lib/libjemalloc.so" \
    MOVIEPILOT_DOCKER_KEEPALIVE_ON_FAILURE="true"

# 引入支持 amr 编码的静态 ffmpeg
COPY --from=mwader/static-ffmpeg:8.1.1 /ffmpeg /usr/local/bin/
COPY --from=mwader/static-ffmpeg:8.1.1 /ffprobe /usr/local/bin/

# python 环境
COPY --from=prepare_venv --chmod=777 ${VENV_PATH} ${VENV_PATH}
COPY --from=prepare_venv /usr/local/bin/uv /usr/local/bin/uv
COPY --from=prepare_venv /usr/local/bin/uv-pip-compat /usr/local/bin/uv-pip-compat

# 浏览器运行依赖 (playwright 装在 venv 内，走 venv python -m 避免依赖控制台脚本)
RUN ${VENV_PATH}/bin/python -m playwright install-deps chromium \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf \
    /tmp/* \
    /var/lib/apt/lists/* \
    /var/tmp/*

# 准备运行代码
WORKDIR /app

COPY --from=prepare_code /app /app
COPY --from=prepare_code /public /public

# 资源完整性兜底：prepare_code 阶段可能因 GHA 缓存(type=gha)复用缺 .so 的旧层，
# 导致 final 镜像缺失站点认证扩展、后端 import 即崩(ModuleNotFoundError: app.application.site.sites)。
# 此处强制校验并在缺失时重新下载，确保镜像必然包含认证闸门依赖的 sites.*.so 与 user.sites.v3.bin。
RUN python_ver=$(python3 -c 'import sys; print(f"cpython-{sys.version_info.major}{sys.version_info.minor}")') \
    && ARCH=$(uname -m) \
    && SUFFIX=$([ "$ARCH" = "aarch64" ] && echo aarch64-linux-gnu || echo x86_64-linux-gnu) \
    && BASE="https://raw.githubusercontent.com/jxxghp/MoviePilot-Resources/main/resources.v3" \
    && SO="/app/app/application/site/sites.${python_ver}-${SUFFIX}.so" \
    && BIN="/app/app/application/site/user.sites.v3.bin" \
    && if [ ! -s "$SO" ]; then echo "[resource-guard] re-download sites.so"; curl -fsSL "${BASE}/sites.${python_ver}-${SUFFIX}.so" -o "$SO"; fi \
    && if [ ! -s "$BIN" ]; then echo "[resource-guard] re-download user.sites.v3.bin"; curl -fsSL "${BASE}/user.sites.v3.bin" -o "$BIN"; fi \
    && test -s "$SO" && test -s "$BIN" && echo "[resource-guard] OK so=$(stat -c%s "$SO") bin=$(stat -c%s "$BIN")"

RUN cp -f /app/docker/nginx.common.conf /etc/nginx/common.conf \
    && cp -f /app/docker/nginx.template.conf /etc/nginx/nginx.template.conf \
    && mkdir -p /usr/local/lib/moviepilot/control \
    && find /app/docker -maxdepth 1 -type f -name '*.sh' ! -name 'launcher.sh' -exec cp -f -t /usr/local/lib/moviepilot/control {} + \
    && cp -f /app/docker/launcher.sh /entrypoint.sh \
    && cp -f /app/docker/docker_http_proxy.conf /etc/nginx/docker_http_proxy.conf \
    && printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'cd /app' 'exec "${VENV_PATH:-/opt/venv}/bin/python3" -m app.cli "$@"' > /usr/local/bin/moviepilot \
    && bash -n /entrypoint.sh \
    && for control_script in /usr/local/lib/moviepilot/control/*.sh; do bash -n "${control_script}" || exit 1; done \
    && chmod 755 /entrypoint.sh /usr/local/bin/moviepilot \
    && chmod 500 /usr/local/lib/moviepilot/control/*.sh \
    && mkdir -p ${HOME} \
    && groupadd -r moviepilot -g 918 \
    && useradd -r moviepilot -g moviepilot -d ${HOME} -s /bin/bash -u 918 \
    && python_ver=$(python3 -V | awk '{print $2}') \
    && echo "/app/" > ${VENV_PATH}/lib/python${python_ver%.*}/site-packages/app.pth \
    && echo 'fs.inotify.max_user_watches=5242880' >> /etc/sysctl.conf \
    && echo 'fs.inotify.max_user_instances=5242880' >> /etc/sysctl.conf \
    && echo "zh_CN.UTF-8 UTF-8" >> /etc/locale.gen \
    && locale-gen zh_CN.UTF-8

# 预下载 CloakBrowser 浏览器内核到镜像内（/app/.cloakbrowser），
# 避免每次容器启动都从网络重新下载；运行时由 entrypoint 设置相同的 CLOAKBROWSER_CACHE_DIR 命中缓存。
RUN CLOAKBROWSER_CACHE_DIR=/app/.cloakbrowser ${VENV_PATH}/bin/python -m cloakbrowser install \
    && chown -R moviepilot:moviepilot /app/.cloakbrowser

EXPOSE 3000
VOLUME [ "${CONFIG_DIR}" ]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD /usr/bin/curl -fsS "http://127.0.0.1:${PORT:-3001}/api/v1/system/global?token=moviepilot" >/dev/null || exit 1
ENTRYPOINT [ "/usr/bin/tini", "-g", "--", "/entrypoint.sh" ]
