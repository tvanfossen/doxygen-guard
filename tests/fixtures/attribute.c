/**
 * @brief Queue an inbound event.
 * @version 1.0
 */
__attribute__((visibility("hidden")))
void queue_inbound_event(int event_id) {
    queue_push(event_id);
}

/**
 * @brief Unused callback placeholder.
 * @version 1.0
 */
__attribute__((unused))
void unused_callback(void) {
    /* intentionally empty */
}

/**
 * @brief Function without attribute.
 * @version 1.0
 */
void normal_function(void) {
    do_stuff();
}
