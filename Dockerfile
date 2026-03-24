# context-broker-langgraph — Application container
# All LangGraph flows, queue workers, Imperator, and ASGI server.
#
# Build context: project root (.)

FROM python:3.12.1-slim

ARG USER_NAME=context-broker
ARG USER_UID=1001
ARG USER_GID=1001

# Root phase: system packages, user creation
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libpq-dev && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid ${USER_GID} ${USER_NAME} && \
    useradd --uid ${USER_UID} --gid ${USER_GID} --shell /bin/bash --create-home ${USER_NAME}

USER ${USER_NAME}
WORKDIR /app

# REQ-001 §10: Enable --user pip installs for runtime StateGraph packages
ENV PYTHONUSERBASE=/home/${USER_NAME}/.local
ENV PATH="/home/${USER_NAME}/.local/bin:${PATH}"

# Copy requirements and install dependencies
COPY --chown=${USER_NAME}:${USER_NAME} requirements.txt ./

# Package source is configurable: local wheels, pypi, or devpi
# Default: pypi (wheels can be placed in /app/packages for local mode)
ARG PACKAGE_SOURCE=pypi
ARG DEVPI_URL=""

RUN if [ "$PACKAGE_SOURCE" = "local" ]; then \
        pip install --no-cache-dir --no-index --find-links=/app/packages -r requirements.txt; \
    elif [ "$PACKAGE_SOURCE" = "devpi" ] && [ -n "$DEVPI_URL" ]; then \
        pip install --no-cache-dir --index-url "$DEVPI_URL" -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

# Copy application code and entrypoint
COPY --chown=${USER_NAME}:${USER_NAME} app/ ./app/
COPY --chown=${USER_NAME}:${USER_NAME} entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
