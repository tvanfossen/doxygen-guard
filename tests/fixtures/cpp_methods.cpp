/**
 * @brief Parse a name from input string.
 * @version 1.0
 */
std::string parse_name(const std::string& input) {
    return input.substr(0, input.find(' '));
}

/**
 * @brief Free a heap allocation from the C API.
 * @version 1.0
 */
extern "C" void entropic_free(void* ptr) {
    free(ptr);
}

/**
 * @brief Check if a key exists in the model registry.
 * @version 1.0
 */
bool Registry::contains(const std::string& key) const {
    return entries_.count(key) > 0;
}

/**
 * @brief Retrieve data by identifier.
 * @version 1.0
 */
std::vector<int> DataStore::getData(int id) {
    return cache_[id];
}

/**
 * @brief Simple standalone function.
 * @version 1.0
 */
void simple_func(int x) {
    process(x);
}

void Undocumented_Method(void) {
    do_stuff();
}
