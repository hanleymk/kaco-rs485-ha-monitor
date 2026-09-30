ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip

# Break-system-packages needed since Alpine's Python is externally managed
RUN pip3 install --break-system-packages --no-cache-dir pyserial paho-mqtt

COPY kaco_monitor_mqtt.py /kaco_monitor_mqtt.py
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
