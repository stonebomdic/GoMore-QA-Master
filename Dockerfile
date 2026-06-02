# GoMore QA Master container — built primarily so any MCP
# catalog that introspects servers in a sandbox can boot the server,
# send `initialize` + `tools/list` over stdio, and confirm a clean
# JSON-RPC response.
#
# Day-to-day use: install from internal Git (see README). Real test runs
# need access to the user's project files, browsers, simulators, etc.
# that live outside any sane container. This image is deliberately
# minimal — enough to answer introspection, not enough to actually run
# pytest / playwright / maestro.

FROM python:3.12-slim

# Install from local source so the image always reflects the current
# commit (introspection should pass even before a PyPI release).
WORKDIR /srv
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Defaults for the introspection probe. QA_PROJECT_ROOT just needs to
# resolve to a writable path — config.py only `.resolve()`s it, doesn't
# require it to exist until a real run happens (which we don't expect
# inside this container).
ENV QA_RUNNER=pytest \
    QA_PROJECT_ROOT=/tmp/qa-project \
    PYTHONUNBUFFERED=1

WORKDIR /tmp/qa-project
ENTRYPOINT ["python", "-m", "gomore_qa_master.server"]
