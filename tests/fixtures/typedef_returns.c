/**
 * @brief Return a status enum value.
 * @version 1.0
 * @return Status for the given ID
 */
status_t get_status(int id) {
    return lookup(id);
}

/**
 * @brief Return a pointer to a config struct.
 * @version 1.0
 * @return Pointer to config entry, or NULL if key not found
 */
config_entry_t* find_config(int key) {
    return &entries[key];
}

/**
 * @brief Test-seam static function using STATIC macro.
 * @version 1.0
 */
STATIC void internal_helper(int x) {
    process(x);
}

/**
 * @brief Weak-linked default handler.
 * @version 1.0
 */
WEAK void default_handler(void) {
    while(1);
}

void undocumented_func(err_code_t val) {
    handle(val);
}
