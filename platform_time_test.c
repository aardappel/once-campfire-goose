#include "platform.c"
#include <assert.h>

static void reference_timestamp(char text[40], const struct timespec *instant, int sql, int future, int seconds_ago) {
    time_t second = instant->tv_sec - seconds_ago;
    struct tm utc;
    assert(gmtime_r(&second, &utc));
    if (future) {
        utc.tm_year += 20;
        int year = utc.tm_year + 1900;
        if (utc.tm_mon == 1 && utc.tm_mday == 29 && (year % 4 || (year % 100 == 0 && year % 400)))
            utc.tm_mday = 28;
    }
    size_t length = strftime(text, 40, sql ? "%Y-%m-%d %H:%M:%S" : "%Y-%m-%dT%H:%M:%S", &utc);
    assert(length);
    snprintf(text + length, 40 - length, sql ? ".%06ld" : ".%03ldZ", instant->tv_nsec / (sql ? 1000 : 1000000));
}

static void check(struct timespec instant) {
    for (int mode = 0; mode < 6; ++mode) {
        int sql = mode % 2, future = mode / 2 == 1, seconds_ago = mode / 2 == 2 ? 60 : 0;
        char expected[40], actual[40];
        reference_timestamp(expected, &instant, sql, future, seconds_ago);
        size_t length = campfire_timestamp_text(actual, &instant, sql, future, seconds_ago);
        assert(length == strlen(expected));
        assert(!strcmp(expected, actual));
    }
}

static void *check_thread(void *argument) {
    intptr_t offset = (intptr_t)argument * 86400;
    // Repeated seconds exercise cached prefixes, changing fractions and backward jumps.
    for (int i = 0; i < 2000; ++i)
        check((struct timespec){.tv_sec = 1709251199 + offset + (i % 3), .tv_nsec = (i * 499999L) % 1000000000});
    return NULL;
}

int main(void) {
    time_t seconds[] = {0, -1, 1709251199, 1709251200, 1798761599, 1798761600, 3476476799};
    long fractions[] = {0, 999, 1000, 999999, 1000000, 999999999, 0};
    for (size_t i = 0; i < sizeof seconds / sizeof seconds[0]; ++i)
        for (size_t j = 0; j < sizeof fractions / sizeof fractions[0]; ++j)
            check((struct timespec){.tv_sec = seconds[i], .tv_nsec = fractions[j]});
    struct tm leap = {.tm_year = 180, .tm_mon = 1, .tm_mday = 29};
    struct timespec instant = {.tv_sec = timegm(&leap), .tv_nsec = 123456789};
    char text[40];
    assert(campfire_timestamp_text(text, &instant, 0, 1, 0) == 24);
    assert(!strcmp(text, "2100-02-28T00:00:00.123Z"));
    pthread_t threads[8];
    for (intptr_t i = 0; i < 8; ++i) assert(!pthread_create(&threads[i], NULL, check_thread, (void *)i));
    for (int i = 0; i < 8; ++i) assert(!pthread_join(threads[i], NULL));
    puts("PASS: timestamp fractions, clock rollovers, leap expiry and independent threads");
}
