/**
 * @brief Start the pairing process.
 * @version 1.0
 * @req REQ-0252
 * @sends EVENT_PAIRING_STARTED
 * @calls wifi_mgr::WiFi_ConnectAfterDelay
 * @note CLOUD_DISABLE
 */
void Pairing_Start(void) {
    disable_cloud();
    start_key_gen();
}

/**
 * @brief Continue pairing after WiFi connected.
 * @version 1.0
 * @req REQ-0252
 * @receives EVENT_WIFI_IP_ACQUIRED
 * @sends EVENT_MQTT_START_CONNECTION
 */
void ContinuePairing(void) {
    request_certs();
    start_mqtt();
}
