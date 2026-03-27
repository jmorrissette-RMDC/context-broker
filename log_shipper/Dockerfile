FROM python:3.12.1-slim

WORKDIR /app

# Install dependencies
# Using slim instead of alpine ensures we can use wheels for asyncpg,
# keeping the image small without needing build-essentials.
RUN pip install --no-cache-dir aiodocker asyncpg

# Copy the shipper script
COPY shipper.py .

# Ensure logs are flushed immediately to stdout for debugging
ENV PYTHONUNBUFFERED=1

# Log shipper health: verify the Python process is alive.
# No HTTP endpoint — checks that PID 1 (the shipper) is running.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD kill -0 1 || exit 1

# Run the shipper
CMD ["python", "shipper.py"]
