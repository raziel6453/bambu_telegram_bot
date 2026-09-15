FROM python:3.11-alpine

# Build tools support dependencies without wheels on some architectures.
RUN apk add --no-cache gcc musl-dev linux-headers python3-dev bash

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bambu_monitor.py /app/bambu_monitor.py
COPY spool_wizard.py /app/spool_wizard.py
COPY pause_reason.py /app/pause_reason.py
COPY printer_diagnostics.py /app/printer_diagnostics.py
COPY inventory_share.py /app/inventory_share.py
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh

WORKDIR /app
CMD [ "/app/run.sh" ]
