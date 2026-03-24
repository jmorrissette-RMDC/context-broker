FROM python:3.12-slim

WORKDIR /app

# Install dependencies
# Using slim instead of alpine ensures we can use wheels for asyncpg,
# keeping the image small without needing build-essentials.
RUN pip install --no-cache-dir aiodocker asyncpg

# Copy the shipper script
COPY shipper.py .

# Ensure logs are flushed immediately to stdout for debugging
ENV PYTHONUNBUFFERED=1

# Run the shipper
CMD ["python", "shipper.py"]
