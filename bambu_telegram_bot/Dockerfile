ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11-alpine3.18
FROM $BUILD_FROM

# Install requirements for python packages if needed (like build-base for C-extensions)
RUN apk add --no-cache \
    python3 \
    py3-pip

# Copy requirements and install
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# Copy logic and run script
COPY bambu_monitor.py /app/bambu_monitor.py
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh

WORKDIR /app
CMD [ "/app/run.sh" ]
