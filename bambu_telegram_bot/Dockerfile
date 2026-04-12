FROM python:3.11-alpine

# Install build dependencies for psutil and others
RUN apk add --no-cache gcc musl-dev linux-headers python3-dev bash

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bambu_monitor.py /app/bambu_monitor.py
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh

WORKDIR /app
CMD [ "/app/run.sh" ]
