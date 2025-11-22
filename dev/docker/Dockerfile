# Stage 1: Builder - Install dependencies and build the package
FROM python:3.14-slim-bookworm AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy only dependency files first for better layer caching
COPY pyproject.toml /build/pyproject.toml
COPY LICENSE /build/LICENSE

# Create minimal structure and install dependencies
# This layer will be cached unless pyproject.toml changes
RUN mkdir -p /build/mitup_bot && \
    echo '__version__ = "0.0.0"' > /build/mitup_bot/__about__.py && \
    touch /build/mitup_bot/__init__.py && \
    pip install --no-cache-dir --prefix=/install .

# Now copy the actual source code
COPY mitup_bot /build/mitup_bot

# Remove source translation files (keep only compiled .mo files from CI)
# Using find instead of ** glob for better shell compatibility
RUN find /build/mitup_bot/locales -type f \( -name "*.po" -o -name "*.pot" \) -delete

# Reinstall with actual source code
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime - Copy only what's needed
FROM python:3.14-slim-bookworm AS runtime

WORKDIR /mitup-telegram-bot

# Install only runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libpq5 \
    gettext && \
    rm -rf /var/lib/apt/lists/*

# Copy installed package from builder
# This copies /install/bin/* to /usr/local/bin/ and /install/lib/* to /usr/local/lib/
COPY --from=builder /install /usr/local

# Explicitly ensure bin directory is in PATH (where mitup console script is installed)
ENV PATH="/usr/local/bin:$PATH"

# Run the bot when the container launches
CMD ["mitup", "launch", "--env", "prod"]
