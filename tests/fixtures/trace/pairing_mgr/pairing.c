/**
 * @brief Start the pairing process.
 * @version 1.0
 * @req REQ-0252
 * @emits EVENT:PAIRING_STARTED
 * @ext wifi_mgr::WIFIMGR_STACONNECTAFTERDELAY
 * @triggers BISSELL_CLOUDMGR_DISABLE
 */
void Pairing_Start(void) {
    disable_cloud();
    start_key_gen();
}

/**
 * @brief Continue pairing after WiFi connected.
 * @version 1.0
 * @req REQ-0252
 * @handles EVENT:EVENT_WIFI_MGR_WIFI_IP_ACQUIRED
 * @emits EVENT:EVENT_MQTT_START_CONNECTION
 */
void ContinuePairing(void) {
    request_certs();
    start_mqtt();
}
