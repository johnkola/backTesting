# The repo's .git, for the commit this image is built from (G45). Compose
# passes it as a build context of this name (`additional_contexts`), which
# replaces this empty stage; a plain `docker build` gets the empty stage, and
# the image records no commit, as before. `.dockerignore` keeps .git out of
# the main context, so this is the only way it reaches the build.
FROM scratch AS gitdir

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# LightGBM links the system OpenMP runtime, which the slim image does not ship.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY python/pyproject.toml ./
# Install the CPU-only PyTorch wheel first so `pip install .` sees torch already
# satisfied and skips the multi-GB CUDA build from PyPI. The NN strategy is a small
# MLP that runs fine on CPU — no GPU stack needed.
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install .

COPY python/loader ./loader
COPY python/nn ./nn
COPY python/jobs ./jobs

# Last, so a new commit rebuilds only this layer: resolve HEAD from the files
# (the image has no git binary) and keep it where `nn.lineage.git_sha()` looks.
# Prints nothing when it cannot tell, and the build still succeeds.
RUN --mount=type=bind,from=gitdir,target=/tmp/gitdir \
    python -m nn.lineage --head /tmp/gitdir > /app/GIT_SHA

# The Python compute worker: it takes jobs from the jobs table and opens no
# port. FastAPI stays in the image as a library: the worker runs a route's
# function in-process when a job carries a request (jobs/calls.py).
CMD ["python", "-m", "jobs.worker"]
