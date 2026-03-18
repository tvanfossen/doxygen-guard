/**
 * @brief Function with invalid tag values.
 * @version 1.0
 * @req INVALID-FORMAT
 * @emits BADPREFIX_EVENT
 * @ext modfunc
 */
void Bad_Tags(void) {
    do_stuff();
}

/**
 * @brief Function with valid tag values.
 * @version 1.0
 * @req REQ-0001 [verified]
 * @emits EVENT:DATA_READY
 * @ext mod::func
 */
void Good_Tags(void) {
    do_other_stuff();
}
