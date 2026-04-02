# Use an official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

# Create an app user so we don't run as root
RUN useradd -m appuser

# Set working directory
WORKDIR /app

# Install system dependencies
# gcc and python3-dev are sometimes needed for building python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create the uploads directory and ensure correct permissions
RUN mkdir -p /app/uploads && chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Expose the Flask port
EXPOSE 5000

# Start the application using gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
