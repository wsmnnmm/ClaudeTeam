# ClaudeTeam runtime image — minimum viable.
#
# Bakes in Python 3.11 + tmux + nodejs/npm (for npx @larksuite/cli) + git
# + the claudeteam package itself. Does NOT include the agent CLIs
# (claude / codex / kimi) — each has its own auth and licence
# requirement; derive from this image and add whichever you need.
#
# Volumes:
#   /data          - team config + runtime state (mount a host dir)
#   /root/.lark-cli - lark-cli OAuth profile (mount your existing one)
#
# Network:
#   lark-cli's event +subscribe long-poll needs to reach
#   open.larksuite.com / open.feishu.cn. Run the container with
#   --network host (or compose `network_mode: host`) on Linux to avoid
#   NAT timeouts; on macOS/Windows Docker Desktop, default bridge works
#   but expect the slower lark-cli round-trips noted in CLAUDE.md
#   (project_lark_cli_slow.md memory).

FROM python:3.11-slim

# Pin apt index once; install in one layer to keep the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tmux \
        nodejs \
        npm \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only what's needed to install the package — pyproject + src.
# Tests / docs / scenarios stay out of the image to keep it small;
# devs who want the full repo should bind-mount the working tree.
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

# Defaults so a fresh container has a sensible state layout. Override
# any of these at run time via `docker run -e CLAUDETEAM_STATE_DIR=...`
# or compose `environment:` if you want a different layout.
ENV CLAUDETEAM_STATE_DIR=/data/state \
    CLAUDETEAM_TEAM_FILE=/data/team.json \
    CLAUDETEAM_RUNTIME_CONFIG=/data/runtime_config.json \
    LARK_CLI_NO_PROXY=1

VOLUME ["/data", "/root/.lark-cli"]

# Default to a shell so operators attach with `docker exec -it … bash`
# and run `claudeteam up` / `claudeteam health` manually. A bare
# `claudeteam up` as CMD would exit immediately because tmux runs
# detached and the container would have no foreground process.
CMD ["bash"]
