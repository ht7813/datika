# Contributing

## Setup

    git clone https://github.com/ht7813/datika
    cd datika
    uv sync

## For developers in China

Configure the PyPI mirror in your **user-level** uv config, not in the
project:

    # ~/.config/uv/uv.toml
    [[index]]
    url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
    default = true

Do not commit mirror URLs to `uv.lock`. The lockfile must reference
official PyPI URLs so that contributors and CI outside China can
install from it. If `git diff uv.lock` shows mirror URLs, regenerate:

    uv lock --default-index https://pypi.org/simple
