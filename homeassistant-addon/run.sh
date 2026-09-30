#!/usr/bin/with-contenv bashio

INVERTER_PORT=$(bashio::config 'inverter_port')
INVERTER_ADDRESS=$(bashio::config 'inverter_address')
INVERTER_MODEL=$(bashio::config 'inverter_model')
POLL_INTERVAL=$(bashio::config 'poll_interval')

# Pull MQTT broker details automatically from Home Assistant's own MQTT
# service (e.g. the Mosquitto broker add-on) instead of hardcoding them.
if bashio::services.available "mqtt"; then
    MQTT_HOST=$(bashio::services "mqtt" "host")
    MQTT_PORT=$(bashio::services "mqtt" "port")
    MQTT_USER=$(bashio::services "mqtt" "username")
    MQTT_PASSWORD=$(bashio::services "mqtt" "password")
    bashio::log.info "Using MQTT broker at ${MQTT_HOST}:${MQTT_PORT} (auto-discovered)"
else
    bashio::log.error "No MQTT service available. Make sure the Mosquitto broker add-on is installed and running."
    exit 1
fi

exec python3 /kaco_monitor_mqtt.py \
    --port "${INVERTER_PORT}" \
    --address "${INVERTER_ADDRESS}" \
    --model "${INVERTER_MODEL}" \
    --interval "${POLL_INTERVAL}" \
    --mqtt-host "${MQTT_HOST}" \
    --mqtt-port "${MQTT_PORT}" \
    --mqtt-user "${MQTT_USER}" \
    --mqtt-password "${MQTT_PASSWORD}"
